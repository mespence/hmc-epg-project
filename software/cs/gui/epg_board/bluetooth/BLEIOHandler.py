import os
import sys
import asyncio
import enum
import time
from dataclasses import dataclass
from typing import Optional, Any, List, Tuple, Callable

import numpy as np
from bleak import BleakError
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from EPGControlKey import EPGControlKey
from bluetooth.BLEDeviceClient import BLEDeviceClient, Timeouts
from bluetooth.BLEFrameParser import BLEFrameParser

try:  # Windows WinRT needs STA when using Bleak in a non-main thread
    if sys.platform.startswith("win"):
        from bleak.backends.winrt.util import allow_sta as _allow_sta
    else:
        _allow_sta = None
except Exception:
    _allow_sta = None

# Enums & Config

class ConnectionState(enum.Enum):
    IDLE = 0
    CONNECTING = 1
    CONNECTED = 2
    RECONNECTING = 3
    DISCONNECTED = 4
    ERROR = 5

class DropPolicy(enum.Enum):
    OLDEST = "oldest"
    NEWEST = "newest"
    BLOCK  = "block"

NOTIFY_CHARACTERISTIC_UUID = "445817D2-9E86-1078-1F76-703DC002EF42"
WRITE_CHARACTERISTIC_UUID  = "445817D2-9E86-1078-1F76-703DC002EF43"


class BLEIOHandler(QObject):
    """
    Qt-friendly BLE I/O handler.

    Responsibilities:
    - Owns a QThread + asyncio event loop.
    - Owns a BLEDeviceClient (device I/O) and BLEFrameParser (parsing).
    - Exposes Qt signals/slots for GUI use.
    - Batches data frames into NumPy arrays via a periodic task.
    """

    # Signals
    connectionStateChanged = pyqtSignal(ConnectionState)
    errorOccurred = pyqtSignal(str, int)
    dataBatchReceived = pyqtSignal(object, object)   # (timestamps: np.ndarray, volts: np.ndarray)
    throughputUpdated = pyqtSignal(float)
    droppedSamples = pyqtSignal(int)
    writeCompleted = pyqtSignal(bool, str)
    managementFramesReceived = pyqtSignal(object)    # List[str] of management lines

    def __init__(
        self,
        *,
        batch_interval_ms: int = 10,
        drop_policy: DropPolicy = DropPolicy.OLDEST,
        max_buffer_seconds: float = 2.0,
        timeouts: Timeouts = Timeouts(),
        reconnect_backoff_s: List[float] = (1.0, 2.0, 5.0, 10.0),
        enable_throughput_telemetry: bool = False,
        default_write_mode_sync: bool = True,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # Tunables
        self._batch_interval_ms = int(batch_interval_ms)             # time (in ms) between data batch emissions
        self._drop_policy = drop_policy                              # how data should be dropped if the buffer fills
        self._max_buffer_seconds = float(max_buffer_seconds)         # the maximum amount of time between 
        self._timeouts = timeouts                                    # the timeouts (in sec) of the connect sequence
        self._reconnect_backoff = list(reconnect_backoff_s)          # the backoff (in sec) for auto-reconnection
        self._enable_throughput = bool(enable_throughput_telemetry)  # whether data throughput should be calculated
        self._default_write_sync = bool(default_write_mode_sync)     # whether writes should wait for an acknolwedgement

        # State
        self._state = ConnectionState.IDLE                             # the state of the connection (e.g. disconnected, reconnecting)
        self._target_address: Optional[str] = None                     # the MAC address of the currently targeted board
        self._notify_uuid: Optional[str] = NOTIFY_CHARACTERISTIC_UUID  # the GATT characteristic UUID used for incoming notifications 
        self._write_uuid: Optional[str] = WRITE_CHARACTERISTIC_UUID    # the GATT characteristic UUID used for outgoing writes
        self._sticky = False                                           # whether the handler should keep trying to stay connected to _target_address

        # Thread / loop
        self._thread: Optional[QThread] = None                  # the worker thread that owns the asyncio event loop
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # the asyncio event loop running in the worker thread
        self._loop_task: Optional[asyncio.Task] = None          # the top-level keep-alive task for the event loop

        # Async tasks
        self._connect_task: Optional[asyncio.Task] = None     # the in-flight connect sequence task (if any)
        self._reconnect_task: Optional[asyncio.Task] = None   # the in-flight auto-reconnect task (if any)
        self._batch_task: Optional[asyncio.Task] = None       # the periodic batching task that emits dataBatchReceived
        self._throughput_task: Optional[asyncio.Task] = None  # the periodic task that computes and emits throughputUpdated

        # Device + parser
        self._device: Optional[BLEDeviceClient] = None     # the active BLEDeviceClient wrapping the BleakClient connection
        self._parser = BLEFrameParser()                    # the parser that turns raw bytes into data and management frames

        # Throughput telemetry
        self._samples_received_window = 0              # the number of samples seen since the last throughput emission
        self._last_throughput_emit = time.monotonic()  # the monotonic timestamp of the last throughputUpdated emission

    # ---------- Thread/loop lifecycle ----------
    def start(self) -> None:
        """Create worker thread, move this object into it, and start the asyncio loop."""
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self.moveToThread(self._thread)
        self._thread.started.connect(self._on_thread_started)
        self._thread.start()

    def stop(self) -> None:
        """Stop the asyncio loop and worker thread (app shutdown)."""
        if self._thread is None:
            return
        self._invoke_in_loop(self._shutdown_async, fire_and_forget=True)

    def _on_thread_started(self) -> None:
        # Windows-only STA setup
        try:
            if _allow_sta is not None:
                _allow_sta()
        except Exception as e:
            print(f"[BLEIOHandler] Warning: allow_sta() failed: {e}")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_task = self._loop.create_task(self._run_loop())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _run_loop(self) -> None:
        """Idle task that keeps the loop alive until shutdown."""
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self._close_device()

    # ---------- Public API (slots) ----------
    @pyqtSlot(str, str, str)
    def connectTo(self, address: str, notify_uuid: str, write_uuid: str) -> None:
        """
        Begin (or switch to) a sticky connection to `address`.
        """
        self._target_address = address
        self._notify_uuid = notify_uuid
        self._write_uuid = write_uuid
        self._sticky = True
        self._invoke_in_loop(self._connect_sequence, fire_and_forget=True)

    @pyqtSlot()
    def disconnectFrom(self) -> None:
        """User-initiated disconnect."""
        self._sticky = False
        self._invoke_in_loop(self._disconnect_sequence, fire_and_forget=True)

    @pyqtSlot()
    def startStream(self) -> None:
        """Optional helper for firmware that needs a start command."""
        # Example: self.sendCommand("START")
        pass

    @pyqtSlot()
    def stopStream(self) -> None:
        """Optional helper for firmware that needs a stop command."""
        # Example: self.sendCommand("STOP")
        pass

    @pyqtSlot(str, str)
    def sendCommand(self, text: str, tag: str = "") -> None:
        """
        Send a UTF-8 command with NUL terminator.
        """
        nowait = not self._default_write_sync
        self._invoke_in_loop(
            self._send_command_async,
            args=(text, nowait, tag),
            fire_and_forget=True,
        )

    @pyqtSlot(int)
    def setBatchInterval(self, ms: int) -> None:
        self._batch_interval_ms = max(1, int(ms))

    @pyqtSlot(str)
    def setDropPolicy(self, policy: str) -> None:
        try:
            self._drop_policy = DropPolicy(policy)
        except Exception:
            print(f"[BLEIOHandler] Invalid drop policy '{policy}', defaulting to OLDEST")
            self._drop_policy = DropPolicy.OLDEST

    @pyqtSlot(float)
    def setMaxBufferedSeconds(self, seconds: float) -> None:
        self._max_buffer_seconds = max(0.1, float(seconds))

    @pyqtSlot(bool)
    def setWriteModeSync(self, sync_default: bool) -> None:
        self._default_write_sync = bool(sync_default)

    # ---------- Loop dispatch helper ----------

    def _invoke_in_loop(
        self,
        coro_func: Callable[..., asyncio.Future],
        args: Tuple = (),
        fire_and_forget: bool = False,
    ):
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(coro_func(*args), self._loop)
        except RuntimeError as e:
            print(f"[BLEIOHandler] _invoke_in_loop failed: {e}")
            return
        if fire_and_forget:
            return
        return fut

    # ---------- State helpers ----------

    def _set_state(self, new_state: ConnectionState) -> None:
        self._state = new_state
        self.connectionStateChanged.emit(new_state)

    def _emit_error(self, message: str, code: int = 0) -> None:
        self.errorOccurred.emit(message, code)

    def _sequence_preempted(self, addr_snapshot: str) -> bool:
        return (
            not self._sticky
            or self._target_address is None
            or self._target_address != addr_snapshot
        )

    # ---------- Async connect / reconnect / disconnect sequences ----------

    async def _connect_sequence(self) -> None:
        # Cancel existing tasks
        await self._cancel_task("_reconnect_task")
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

        # Close any existing device
        await self._close_device()

        if not self._target_address or not self._notify_uuid or not self._write_uuid:
            self._emit_error("Cannot connect: no target address or UUIDs set")
            self._set_state(ConnectionState.ERROR)
            return

        addr_snapshot = self._target_address
        self._set_state(ConnectionState.CONNECTING)

        ok = await self._one_attempt()

        if self._sequence_preempted(addr_snapshot):
            return

        if not ok:
            if self._sticky and self._target_address is not None:
                if self._reconnect_task is None or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._begin_reconnect())
            else:
                self._set_state(ConnectionState.DISCONNECTED)
            return

        await self._start_batching()
        if self._enable_throughput and (
            self._throughput_task is None or self._throughput_task.done()
        ):
            self._throughput_task = asyncio.create_task(self._throughput_loop())

        self._set_state(ConnectionState.CONNECTED)

    async def _begin_reconnect(self) -> None:
        if not self._sticky or not self._target_address:
            await self._disconnect_sequence(reason="Reconnect aborted: no target or not sticky")
            return

        addr_snapshot = self._target_address

        for delay in self._reconnect_backoff:
            if self._sequence_preempted(addr_snapshot):
                return

            self._set_state(ConnectionState.RECONNECTING)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if self._sequence_preempted(addr_snapshot):
                return

            ok = await self._one_attempt()
            if self._sequence_preempted(addr_snapshot):
                return

            if ok:
                await self._start_batching()
                if self._enable_throughput and (
                    self._throughput_task is None or self._throughput_task.done()
                ):
                    self._throughput_task = asyncio.create_task(self._throughput_loop())
                self._set_state(ConnectionState.CONNECTED)
                return

        await self._disconnect_sequence(reason="Reconnect attempts exhausted")

    async def _disconnect_sequence(self, reason: str = "") -> None:
        await self._cancel_task("_reconnect_task")
        await self._cancel_task("_connect_task")
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

        await self._close_device()

        # Reset parser / telemetry
        self._parser = BLEFrameParser()
        self._samples_received_window = 0
        self._last_throughput_emit = time.monotonic()

        self._target_address = None
        self._notify_uuid = None
        self._write_uuid = None
        self._sticky = False

        self._set_state(ConnectionState.DISCONNECTED)
        if reason:
            self._emit_error(reason, code=0)

    async def _cancel_task(self, task_attr: str) -> None:
        task = getattr(self, task_attr, None)
        if task is None:
            return
        if task.done():
            setattr(self, task_attr, None)
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        setattr(self, task_attr, None)

    async def _one_attempt(self) -> bool:
        if not self._target_address or not self._notify_uuid or not self._write_uuid:
            self._emit_error("Missing BLE target address or UUIDs")
            return False

        addr = self._target_address
        notify_uuid = self._notify_uuid
        write_uuid = self._write_uuid

        try:
            self._device = BLEDeviceClient(
                addr,
                notify_uuid=notify_uuid,
                write_uuid=write_uuid,
                timeouts=self._timeouts,
            )
            await self._device.connect()
            await self._device.start_notifications(self._on_notify_bytes)
            return True

        except (asyncio.TimeoutError, BleakError, RuntimeError) as e:
            self._emit_error(f"BLE connection attempt failed: {e}")
            await self._close_device()
            return False

    async def _shutdown_async(self) -> None:
        await self._disconnect_sequence()
        if self._loop is None:
            return

        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            finally:
                self._loop_task = None

        self._loop.stop()

    async def _close_device(self) -> None:
        dev = self._device
        self._device = None
        if dev is None:
            return
        try:
            await dev.stop_notifications()
        except Exception:
            pass
        try:
            await dev.disconnect()
        except Exception:
            pass

    # ---------- Notification path & batching ----------

    def _on_notify_bytes(self, data: bytes) -> None:
        """Run in the BLE event loop thread; keep this as cheap as possible."""
        self._parser.feed(data)

    async def _start_batching(self) -> None:
        if self._batch_task is None or self._batch_task.done():
            self._batch_task = asyncio.create_task(self._batch_loop())
        if self._enable_throughput:
            if self._throughput_task is None or self._throughput_task.done():
                self._samples_received_window = 0
                self._last_throughput_emit = time.monotonic()
                self._throughput_task = asyncio.create_task(self._throughput_loop())

    async def _stop_batching(self) -> None:
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

    async def _batch_loop(self) -> None:
        interval_s = max(1, self._batch_interval_ms) / 1000.0
        try:
            while True:
                await asyncio.sleep(interval_s)

                data_frames, mgmt_frames = self._parser.take_frames()
                if not data_frames and not mgmt_frames:
                    continue

                # Emit management frames (if any)
                if mgmt_frames:
                    self.managementFramesReceived.emit(
                        [f.payload for f in mgmt_frames]
                    )

                if not data_frames:
                    continue

                # Apply backpressure based on timestamps
                ts_list = [f.timestamp_ms for f in data_frames]
                v_list = [f.millivolts for f in data_frames]
                self._apply_backpressure_if_needed(ts_list, v_list)
                if not ts_list:
                    continue

                ts = np.asarray(ts_list, dtype=np.uint64)
                vv = np.asarray(v_list, dtype=np.int32)

                self.dataBatchReceived.emit(ts, vv)
                self._samples_received_window += len(ts_list)

        except asyncio.CancelledError:
            pass

    def _apply_backpressure_if_needed(self, ts_batch: List[int], v_batch: List[int]) -> None:
        if not ts_batch:
            return

        duration_s = (ts_batch[-1] - ts_batch[0]) / 1000.0
        if duration_s <= self._max_buffer_seconds:
            return

        cutoff_time = ts_batch[-1] - int(self._max_buffer_seconds * 1000)
        drop_idx = 0
        for i, t in enumerate(ts_batch):
            if t >= cutoff_time:
                drop_idx = i
                break

        dropped = drop_idx
        if dropped > 0:
            del ts_batch[:drop_idx]
            del v_batch[:drop_idx]
            self.droppedSamples.emit(dropped)

    async def _throughput_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                count = self._samples_received_window
                self._samples_received_window = 0
                self._last_throughput_emit = time.monotonic()
                self.throughputUpdated.emit(float(count))
        except asyncio.CancelledError:
            pass

    # ---------- Writes / commands ----------

    async def _send_command_async(self, text: str, nowait: bool, tag: str) -> None:
        """
        Append NUL, encode UTF-8, and write via BLEDeviceClient.
        """
        if not self._device or not self._device.is_connected:
            if not nowait:
                self.writeCompleted.emit(False, tag)
            return

        payload = text.encode("utf-8") + b"\x00"

        try:
            await self._device.write(payload, response=not nowait)
            if not nowait:
                self.writeCompleted.emit(True, tag)
        except Exception as e:
            self._emit_error(f"BLE write failed: {e}")
            if not nowait:
                self.writeCompleted.emit(False, tag)

    def write_command_from_key(self, key: EPGControlKey, value: Any) -> str | None:
        """
        Returns the BLE command string for an engineering key/value.
        """
        match key:
            # 1. Input Resistance
            case EPGControlKey.INPUT_RESISTANCE:
                resistance_conversion = {
                    "100K":     "M:0",
                    "1M":       "M:1",
                    "10M":      "M:2",
                    "100M":     "M:3",
                    "1G":       "M:6",
                    "10G":      "M:4",
                    "SR":       "M:5",
                    "Loopback": "M:7",
                }
                return resistance_conversion.get(value)

            # 2. PGA + DigiPot
            case EPGControlKey.PGA_1:
                return f"P1:{value}"
            case EPGControlKey.PGA_2:
                return f"P2:{value}"
            case EPGControlKey.SIGNAL_CHAIN_AMPLIFICATION:
                return f"SCA:{value}"
            case EPGControlKey.SIGNAL_CHAIN_OFFSET:
                return f"SCO:{value:.3f}"
            case EPGControlKey.DDS_AMPLIFICATION:
                return f"DDSA:{value:.3f}"
            case EPGControlKey.DDS_OFFSET:
                return f"DDSO:{value:.3f}"
            case EPGControlKey.DIGIPOT_CHANNEL_0:
                return f"D0:{value}"
            case EPGControlKey.DIGIPOT_CHANNEL_1:
                return f"D1:{value}"
            case EPGControlKey.DIGIPOT_CHANNEL_2:
                return f"D2:{value}"
            case EPGControlKey.DIGIPOT_CHANNEL_3:
                return f"D3:{value}"

            # 3. Excitation frequency
            case EPGControlKey.EXCITATION_FREQUENCY:
                frequency_conversion = {
                    "1000": "SDDS:1000",
                    "1":    "SDDS:1",
                    "0":    "DDSOFF",
                }
                return frequency_conversion.get(value)

            case _:
                return None



# ---------- Tester window ----------
def main():
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QTextEdit,
        QLabel,
        QPushButton,
    )
    from PyQt6.QtCore import Qt

    BLE_ADDRESS = "C2:83:79:F8:C2:86" # CS's test nRF board

    app = QApplication(sys.argv)

    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)

    status_label = QLabel("Status: disconnected")
    status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

    throughput_label = QLabel("Throughput: — samples/s")
    throughput_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

    log = QTextEdit()
    log.setReadOnly(True)

    connect_button = QPushButton(f"Connect to {BLE_ADDRESS}")
    disconnect_button = QPushButton("Disconnect")
    disconnect_button.setEnabled(False)

    layout.addWidget(status_label)
    layout.addWidget(throughput_label)
    layout.addWidget(connect_button)
    layout.addWidget(disconnect_button)
    layout.addWidget(log)

    win.setCentralWidget(central)
    win.resize(700, 400)
    win.setWindowTitle("BLEIOHandler tester")
    win.show()

    ble = BLEIOHandler(
        batch_interval_ms=50,
        max_buffer_seconds=2.0,
        enable_throughput_telemetry=True,
    )
    ble.start()

    def log_line(msg: str) -> None:
        log.append(msg)

    def on_connection_state(state: ConnectionState) -> None:
        status_label.setText(f"Status: {state.name}")
        if state == ConnectionState.CONNECTED:
            connect_button.setEnabled(False)
            disconnect_button.setEnabled(True)
        else:
            connect_button.setEnabled(True)
            disconnect_button.setEnabled(False)
        log_line(f"[Signal] connectionStateChanged: {state.name}")

    def on_error(message: str, code: int) -> None:
        log_line(f"[Signal] errorOccurred (code={code}): {message}")

    def on_data_batch(timestamps: object, voltages: object) -> None:
        ts = np.asarray(timestamps)
        vv = np.asarray(voltages)
        n = len(ts)
        if n == 0:
            return
        log_line(
            f"[Signal] dataBatchReceived: {n} samples, "
            f"t=[{int(ts[0])} .. {int(ts[-1])}] ms, "
            f"v=[{int(vv[0])} .. {int(vv[-1])}] mV"
        )

    def on_throughput(sps: float) -> None:
        throughput_label.setText(f"Throughput: {sps:.1f} samples/s")

    def on_write_completed(ok: bool, tag: str) -> None:
        log_line(f"[Signal] writeCompleted(tag='{tag}'): ok={ok}")

    def on_mgmt_frames(frames: object) -> None:
        for line in frames:
            log_line(f"[Mgmt] {line}")

    ble.connectionStateChanged.connect(on_connection_state)
    ble.errorOccurred.connect(on_error)
    ble.dataBatchReceived.connect(on_data_batch)
    ble.throughputUpdated.connect(on_throughput)
    ble.writeCompleted.connect(on_write_completed)
    ble.managementFramesReceived.connect(on_mgmt_frames)

    def do_connect():
        log_line(f"[Action] connectTo({BLE_ADDRESS})")
        ble.connectTo(BLE_ADDRESS, NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID)

    def do_disconnect():
        log_line("[Action] disconnectFrom()")
        ble.disconnectFrom()

    connect_button.clicked.connect(do_connect)
    disconnect_button.clicked.connect(do_disconnect)

    def send_start_when_connected(state: ConnectionState):
        if state == ConnectionState.CONNECTED:
            ble.sendCommand("ON", tag="startup-ON")
            ble.sendCommand("START", tag="startup-START")

    ble.connectionStateChanged.connect(send_start_when_connected)

    def on_about_to_quit():
        log_line("[Action] app.aboutToQuit -> ble.stop()")
        ble.stop()

    app.aboutToQuit.connect(on_about_to_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
