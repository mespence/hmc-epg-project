# Things to look at/clarify/maybe remove
# - throughput telemetry tasks


from __future__ import annotations

import sys
import asyncio
import enum
import time
from dataclasses import dataclass
from typing import Optional, Any, List, Tuple

import numpy as np
from bleak import BleakClient, BleakError
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from EPGControlKey import EPGControlKey

try: # Windows WinRT needs STA when using Bleak in a non-main thread
    if sys.platform.startswith("win"):
        from bleak.backends.winrt.util import allow_sta as _allow_sta
    else:
        _allow_sta = None
except Exception:
    _allow_sta = None

# ────────────────────────────────────────────────────────────────────────────────
# Enums & Config
# ────────────────────────────────────────────────────────────────────────────────

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


@dataclass
class Timeouts:
    """Per-attempt time budgets (seconds) for connect -> discover -> subscribe."""
    connect: float = 10.0
    discover: float = 5.0
    subscribe: float = 5.0


NOTIFY_CHARACTERISTIC_UUID = "445817D2-9E86-1078-1F76-703DC002EF42"
WRITE_CHARACTERISTIC_UUID  = "445817D2-9E86-1078-1F76-703DC002EF43"  

# ────────────────────────────────────────────────────────────────────────────────
# Main Handler
# ────────────────────────────────────────────────────────────────────────────────

class BLEIoHandler(QObject):
    """
    Qt-friendly BLE I/O handler designed for high-rate streaming.

    Design:
      - Lives in its own QThread.
      - Owns a dedicated asyncio loop in that thread for Bleak.
      - Notification callback is ultra-cheap: append bytes to an assembler buffer.
      - A periodic batch task parses complete lines into NumPy arrays and emits one
        'dataBatchReceived' per tick.
      - Reconnect uses a fixed backoff sequence (e.g., [1, 2, 5, 10] seconds).
      - Commands are UTF-8 **plus NUL terminator**; default write mode is synchronous,
        with a per-call 'nowait' to do fire-and-forget.

    Wire format (current firmware):
      Each notification payload is an ASCII line: "<timestamp_ms>,<millivolts>\\r\\n".
      Timestamp is on-device (k_uptime_get, ms since boot). Voltage is in mV (signed int).

    Signals (separate, lean):
      - connectionStateChanged(bool)
      - errorOccurred(str, int)
      - dataBatchReceived(np.ndarray, np.ndarray)
      - throughputUpdated(float)                # optional, low rate (off by default)
      - droppedSamples(int)                     # optional
      - writeCompleted(bool, str)               # only for sync writes
    """

    # Signals
    connectionStateChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str, int)
    dataBatchReceived = pyqtSignal(object, object)   # (timestamps: np.ndarray, volts: np.ndarray)
    throughputUpdated = pyqtSignal(float)
    droppedSamples = pyqtSignal(int)                # number of dropped samples if the data buffer fills
    writeCompleted = pyqtSignal(bool, str)          # (ok, tag)

    # Construction
    def __init__(
        self,
        *,
        batch_interval_ms: int = 10,                              # how often a data batch is emitted to dataBatchReceived
        drop_policy: DropPolicy = DropPolicy.OLDEST,              # how data should be dropped if the ring buffer fills
        max_buffer_seconds: float = 2.0,
        timeouts: Timeouts = Timeouts(),
        reconnect_backoff_s: List[float] = (1.0, 2.0, 5.0, 10.0), # list of backoff timeouts for reconnects
        enable_throughput_telemetry: bool = False,
        default_write_mode_sync: bool = True,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # Tunables
        self._batch_interval_ms = int(batch_interval_ms)
        self._drop_policy = drop_policy
        self._max_buffer_seconds = float(max_buffer_seconds)
        self._timeouts = timeouts
        self._reconnect_backoff = list(reconnect_backoff_s)
        self._enable_throughput = bool(enable_throughput_telemetry)
        self._default_write_sync = bool(default_write_mode_sync)

        # State
        self._state = ConnectionState.IDLE
        self._target_address: Optional[str] = None
        self._notify_uuid: Optional[str] = NOTIFY_CHARACTERISTIC_UUID
        self._write_uuid: Optional[str] = WRITE_CHARACTERISTIC_UUID
        self._sticky = False  # becomes True on connectTo(); False after disconnectFrom().

        # Thread / loop
        self._thread: Optional[QThread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_task: Optional[asyncio.Task] = None

        # Async tasks & cancellation guards
        self._connect_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._batch_task: Optional[asyncio.Task] = None
        self._throughput_task: Optional[asyncio.Task] = None

        # BLE client placeholder (BleakClient); keep as 'object' to avoid import now
        self._client: Optional[object] = None

        # Incoming assembler & buffers
        self._rx_bytes = bytearray()  # stores partial lines between notifications
        self._buffered_timestamps = []  # list for decoded timestamps (temporary)
        self._buffered_volts = []      # list for decoded voltages (temporary)

        # Throughput telemetry
        self._samples_received_window = 0
        self._last_throughput_emit = time.monotonic()

    # ── Thread/loop lifecycle helpers ────────────────────────────────────────
    def start(self) -> None:
        """
        Create a worker thread, move this object into it, and start the asyncio loop.
        Call this once after constructing the handler (from the GUI thread).
        """
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self.moveToThread(self._thread)
        self._thread.started.connect(self._on_thread_started)
        self._thread.start()

    def stop(self) -> None:
        """
        Stop the asyncio loop and worker thread. Safe to call on app shutdown.
        """
        if self._thread is None:
            return
        # Use a single-shot post into the worker thread to tear down cleanly
        self._invoke_in_loop(self._shutdown_async, fire_and_forget=True)

    # ── Public API (slots) ───────────────────────────────────────────────────
    @pyqtSlot(str, str, str)
    def connectTo(self, address: str, notify_uuid: str, write_uuid: str) -> None:
        """
        Begin (or switch to) a sticky connection to 'address'. Preempts any
        ongoing connect/reconnect to a different target.
        """
        self._target_address = address
        self._notify_uuid = notify_uuid
        self._write_uuid = write_uuid
        self._sticky = True

        self._invoke_in_loop(self._connect_sequence, fire_and_forget=True)

    @pyqtSlot()
    def disconnectFrom(self) -> None:
        """
        User-initiated disconnect. Cancels connect/reconnect, closes the client,
        clears buffers, resets telemetry, and transitions to DISCONNECTED.
        """
        self._sticky = False
        self._invoke_in_loop(self._disconnect_sequence, fire_and_forget=True)

    @pyqtSlot()
    def startStream(self) -> None:
        """
        Optional helper: if your firmware requires a command to start data.
        """
        # Example: self.sendCommand("START")
        pass

    @pyqtSlot()
    def stopStream(self) -> None:
        """
        Optional helper: if your firmware requires a command to stop data.
        """
        # Example: self.sendCommand("STOP")
        pass

    @pyqtSlot(str, bool, str)
    def sendCommand(self, text: str, nowait: bool = False, tag: str = "") -> None:
        """
        Send a UTF-8 command with **NUL terminator** appended.
          - nowait=False -> synchronous write with response; emits writeCompleted.
          - nowait=True  -> fire-and-forget; no ack signal.
        If disconnected, default behavior is to reject and emit writeCompleted(False, tag).
        """
        self._invoke_in_loop(self._send_command_async, args=(text, nowait, tag), fire_and_forget=True)

    # ── Tunables (setters) ───────────────────────────────────────────────────
    @pyqtSlot(int)
    def setBatchInterval(self, ms: int) -> None:
        self._batch_interval_ms = max(1, int(ms))

    @pyqtSlot(str)
    def setDropPolicy(self, policy: str) -> None:
        try:
            self._drop_policy = DropPolicy(policy)
        except Exception:
            self._drop_policy = DropPolicy.OLDEST

    @pyqtSlot(float)
    def setMaxBufferedSeconds(self, seconds: float) -> None:
        self._max_buffer_seconds = max(0.1, float(seconds))

    @pyqtSlot(bool)
    def setWriteModeSync(self, sync_default: bool) -> None:
        self._default_write_sync = bool(sync_default)

    # ── Internal: worker-thread bootstrap ─────────────────────────────────────
    def _on_thread_started(self) -> None:
        # Windows-only STA setup
        try:
            if _allow_sta is not None:
                _allow_sta()
        except Exception as e:
            print(f"[BLEIoHandler] Warning: allow_sta() failed: {e}")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._run_loop()) # schedule the root coroutine

        # Run loop until explicitly stopped
        try:
            self._loop.run_forever()
        finally:
            # Cleanup on thread exit
            self._loop.close()


    async def _run_loop(self) -> None:
        """
        Main loop task: runs forever until shutdown. This is where we create tasks,
        manage reconnect sequences, etc. Most actions are triggered via _invoke_in_loop().
        """
        try:
            await asyncio.sleep(0)  # give control to the loop
            # Idle until shutdown; everything else is scheduled via _invoke_in_loop.
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            # Close BLE client if still open
            await self._close_client()
            # Loop exits; thread will be stopped in _shutdown_async.

    # ── Dispatch helper ───────────────────────────────────────────────────────
    def _invoke_in_loop(self, coro_func, args: Tuple = (), fire_and_forget: bool = False) -> asyncio.Future:
        """
        Schedule 'coro_func(*args)' on the handler's asyncio loop, from any thread.
        """
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(coro_func(*args), self._loop)
        except RuntimeError as e:
            # Loop not running / already closed
            print(f"[BLEIoHandler] _invoke_in_loop failed: {e}")
            return

        if fire_and_forget:
            return

        return fut 

    # ── State helpers ─────────────────────────────────────────────────────────
    def _set_state(self, new_state: ConnectionState) -> None:
        self._state = new_state
        if new_state in (ConnectionState.CONNECTED,):
            self.connectionStateChanged.emit(True)
        elif new_state in (ConnectionState.DISCONNECTED, ConnectionState.IDLE, ConnectionState.RECONNECTING, ConnectionState.ERROR, ConnectionState.CONNECTING):
            # We emit False for anything not "CONNECTED"
            self.connectionStateChanged.emit(False)

    def _emit_error(self, message: str, code: int = 0) -> None:
        self.errorOccurred.emit(message, code)

    def _sequence_preempted(self, addr_snapshot: str) -> bool:
        """
        Return True if this connect/reconnect sequence should stop because:
        - the handler is no longer sticky (user called disconnectFrom), or
        - the target address changed (user called connectTo for another device), or
        - the target was cleared (_disconnect_sequence).
        """
        return (
            not self._sticky
            or self._target_address is None
            or self._target_address != addr_snapshot
        )


    # ── Async sequences (to be implemented) ───────────────────────────────────
    async def _connect_sequence(self) -> None:
        """
        Preempt any ongoing connect/reconnect, then perform a single connect->discover->subscribe attempt.
        On failure, enter the reconnect sequence with the fixed backoff list.
        """
        # Preempt any ongoing work tied to a previous device/attempt
        # Cancel reconnect + batch + throughput tasks
        await self._cancel_task("_reconnect_task")
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

        # Close any existing client from a previous session
        await self._close_client()

        # must have a target address and UUIDs
        if not self._target_address or not self._notify_uuid or not self._write_uuid:
            self._emit_error("Cannot connect: no target address or UUIDs set")
            self._set_state(ConnectionState.ERROR)
            return
        
        # Remember attempted device
        addr_snapshot = self._target_address

        # 2) Move into CONNECTING state
        self._set_state(ConnectionState.CONNECTING)

        # 3) Attempt one full connection (connect -> discover -> subscribe)
        ok = await self._one_attempt()

        if self._sequence_preempted(addr_snapshot):
            return
        
        if not ok:
            # 4) Failed: decide whether to reconnect or bail
            if self._sticky and self._target_address is not None:
                # Start the reconnect backoff sequence
                if self._reconnect_task is None or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._begin_reconnect())
            else:
                # Not sticky, or target cleared: declare disconnected
                self._set_state(ConnectionState.DISCONNECTED)
            return

        # 5) Success: start batching (and throughput if enabled)
        await self._start_batching()
        if self._enable_throughput and (self._throughput_task is None or self._throughput_task.done()):
            self._throughput_task = asyncio.create_task(self._throughput_loop())

        # 6) Transition to CONNECTED
        self._set_state(ConnectionState.CONNECTED)


    async def _begin_reconnect(self) -> None:
        """
        Run the fixed reconnect backoff sequence. When the list is exhausted,
        transition to DISCONNECTED and fully reset (boot-like state).
        """
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

            if self._sequence_preempted(addr_snapshot): # if things changed during the sleep.
                return

            # Try a single connect -> discover -> subscribe attempt.
            ok = await self._one_attempt()

            if self._sequence_preempted(addr_snapshot): # if things changed during the attempt
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
        """
        Graceful disconnect: cancel tasks, unsubscribe, close client,
        clear buffers, reset telemetry, clear target info, and set DISCONNECTED.

        Used for both user-initiated disconnects and reconnect give-up.
        """
        # Cancel any ongoing reconnect / connect / batch / throughput tasks
        await self._cancel_task("_reconnect_task")
        await self._cancel_task("_connect_task")
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

        # Stop BLE I/O
        await self._close_client()

        # Reset incoming data buffers & telemetry
        self._rx_bytes.clear()
        self._buffered_timestamps.clear()
        self._buffered_volts.clear()
        self._samples_received_window = 0
        self._last_throughput_emit = time.monotonic()

        # Clear configuration & sticky state: boot-like
        self._target_address = None
        self._notify_uuid = None
        self._write_uuid = None
        self._sticky = False

        # Transition to DISCONNECTED
        self._set_state(ConnectionState.DISCONNECTED)

        # Inform UI / logs of final state
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
        """
        A single attempt that does connect->discover->subscribe with the 3 timeouts.
        Returns True on success (and sets state CONNECTED), False on failure.
        """
        if not self._target_address or not self._notify_uuid or not self._write_uuid:
            self._emit_error("Missing BLE target address or UUIDs")
            return False
        
        addr = self._target_address
        notify_uuid = self._notify_uuid
        write_uuid = self._write_uuid

        try:
            # Connect
            self._client = BleakClient(addr)
            await asyncio.wait_for(self._client.connect(), timeout=self._timeouts.connect)

            # Discover / validate UUIDs
            await asyncio.wait_for(self._client.get_services(), timeout=self._timeouts.discover)
            services = self._client.services
            if services is None or notify_uuid not in services.characteristics or write_uuid not in services.characteristics:
                raise RuntimeError("Required characteristics not found on device")

            # Subscribe to notifications
            async def _callback(_, data: bytes):
                self._on_notify_bytes(data)

            await asyncio.wait_for(
                self._client.start_notify(notify_uuid, _callback),
                timeout=self._timeouts.subscribe,
            )

            return True

        except (asyncio.TimeoutError, BleakError, RuntimeError) as e:
            msg = f"BLE connection attempt failed: {e}"
            self._emit_error(msg)

            # don't leave a half-open client
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            return False
    

    async def _shutdown_async(self) -> None:
        """
        Full shutdown path for application exit.

        Runs inside the BLE worker's asyncio loop.
        - First performs a full disconnect/cleanup (idempotent).
        - Then cancels the root _run_loop task.
        - Finally asks the event loop to stop; this causes run_forever()
        in _on_thread_started() to return, and the QThread will exit.
        """
        # Tear down BLE connection and all related tasks.
        await self._disconnect_sequence()

        if self._loop is None:
            return

        # 2) Cancel the idle root task that keeps the loop alive, if it exists.
        if getattr(self, "_loop_task", None) is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            finally:
                self._loop_task = None

        # 3) Stop the event loop.
        #    This will cause loop.run_forever() to return in _on_thread_started(),
        #    after which the thread's run() will finish and the QThread will exit.
        self._loop.stop()

    async def _close_client(self) -> None:
        """
        Close BLE client if open.

        Ensures notifications are stopped and the connection is closed cleanly.
        Safe to call multiple times; all exceptions are caught and logged.
        """
        client = getattr(self, "_client", None)
        if client is None:
            return

        try:
            # Stop notifications if they’re active
            if client.is_connected and self._notify_uuid:
                try:
                    await client.stop_notify(self._notify_uuid)
                except Exception as e:
                    print(f"[BLEIoHandler] Warning: stop_notify failed: {e}")

            # Attempt to disconnect cleanly
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception as e:
                    print(f"[BLEIoHandler] Warning: disconnect failed: {e}")

        finally:
            # Always clear the reference so we don't reuse a dead client
            self._client = None


    # ── Notification path & batching (skeleton) ───────────────────────────────
    def _on_notify_bytes(self, data: bytes) -> None:
        """
        Called by Bleak notify callback (from the loop thread).
        Append incoming bytes to the assembler buffer as cheaply as possible.
        """
        self._rx_bytes.extend(data)

    async def _start_batching(self) -> None:
        """
        Start the periodic batch task and (optionally) throughput task.

        Safe to call multiple times; it will only create tasks if they
        are not already running.
        """
        if self._loop is None:
            return

        # Start batch loop if not running
        if self._batch_task is None or self._batch_task.done():
            self._batch_task = asyncio.create_task(self._batch_loop())

        # Start throughput telemetry loop if enabled and not already running
        if self._enable_throughput:
            if self._throughput_task is None or self._throughput_task.done():
                # Reset window so first second isn't polluted by old count
                self._samples_received_window = 0
                self._last_throughput_emit = time.monotonic()
                self._throughput_task = asyncio.create_task(self._throughput_loop())


    async def _stop_batching(self) -> None:
        """Stop batch & throughput tasks."""
        await self._cancel_task("_batch_task")
        await self._cancel_task("_throughput_task")

    async def _batch_loop(self) -> None:
        """
        Every self._batch_interval_ms, parse as many COMPLETE lines as available
        into NumPy arrays and emit one dataBatchReceived.

        - Assembles partial lines across notifications.
        - Handles the current 1-line-per-notify firmware, but is ready for future
        batched payloads (multiple lines in one notification).
        """
        interval_s = max(1, self._batch_interval_ms) / 1000.0

        try:
            while True:
                await asyncio.sleep(interval_s)

                # Fast path: nothing buffered, skip work
                if not self._rx_bytes:
                    continue

                # Split out complete lines and keep any partial tail
                lines, leftover = self._split_complete_lines(self._rx_bytes)
                # Replace the buffer with just the leftover partial bytes
                self._rx_bytes = bytearray(leftover)

                if not lines:
                    # We had bytes, but no full CRLF-terminated lines yet
                    continue

                # Parse all complete lines into Python ints
                ts_list, v_list = self._parse_lines(lines)
                if not ts_list:
                    # Either all malformed or empty; nothing to emit
                    continue

                # Apply backpressure policy (may drop from ts_list/v_list)
                self._apply_backpressure_if_needed(ts_list, v_list)
                if not ts_list:
                    # Everything got dropped
                    continue

                # Convert to NumPy arrays for downstream consumers
                ts = np.asarray(ts_list, dtype=np.uint64)   # device ms since boot
                vv = np.asarray(v_list, dtype=np.int32)     # millivolts

                # Emit one batched signal for this interval
                self.dataBatchReceived.emit(ts, vv)

                # Update throughput window
                self._samples_received_window += len(ts_list)

        except asyncio.CancelledError:
            # Normal exit when _stop_batching or shutdown cancels this task
            pass


    def _split_complete_lines(self, buf: bytearray) -> Tuple[List[bytes], bytes]:
        """
        Split assembler buffer by CRLF into complete lines and leftover partial tail.
        Returns (list_of_lines_without_CRLF, leftover_bytes).

        Example:
            buf = b"1,10\\r\\n2,20\\r\\n3,3"
            -> lines = [b"1,10", b"2,20"]
            leftover = b"3,3"
        """
        if not buf:
            return [], b""

        data = bytes(buf)  # make an immutable snapshot for searching/splitting
        sep = b"\r\n"

        # Find the last complete CRLF terminator
        last_idx = data.rfind(sep) # TODO: look into if this is too slow
        if last_idx == -1:
            # No complete line yet
            return [], data

        # Everything up to and including that CRLF is "complete"
        complete_region = data[: last_idx + len(sep)]
        leftover = data[last_idx + len(sep) :]

        # Split complete region into lines; the final element from split() will be b""
        # because the region ends with sep, so we drop empty pieces.
        raw_lines = complete_region.split(sep)
        lines = [line for line in raw_lines if line]

        return lines, leftover


    def _parse_lines(self, lines: List[bytes]) -> Tuple[List[int], List[int]]:
        """
        Parse ASCII lines of the form b'12345,678' -> (timestamp_ms:int, millivolts:int).
        Returns two lists (timestamps, mVs). Malformed lines are skipped; a single
        errorOccurred is emitted per batch if any malformed lines are seen.
        """
        ts_list: List[int] = []
        v_list: List[int] = []
        malformed_count = 0

        for line in lines:
            # Strip any stray whitespace just in case
            stripped = line.strip()
            if not stripped:
                continue

            # Split on first comma
            parts = stripped.split(b",", 1)
            if len(parts) != 2:
                malformed_count += 1
                continue

            ts_bytes, v_bytes = parts

            try:
                # int() handles bytes directly for base-10 ASCII
                ts_val = int(ts_bytes)
                v_val = int(v_bytes)
            except ValueError:
                malformed_count += 1
                continue

            ts_list.append(ts_val)
            v_list.append(v_val)

        if malformed_count:
            self._emit_error(
                f"Skipped {malformed_count} malformed ADC line(s) in BLE stream", 0
            )

        return ts_list, v_list


    def _apply_backpressure_if_needed(self, ts_batch: List[int], v_batch: List[int]) -> None:
        """
        If buffered data would exceed max_buffer_seconds, drop according to policy
        and emit droppedSamples(count).
        """
        if not ts_batch:
            return

        # Convert milliseconds to seconds for comparison
        duration_s = (ts_batch[-1] - ts_batch[0]) / 1000.0
        if duration_s <= self._max_buffer_seconds:
            return  # within desired time window

        # Compute how many samples to drop to keep only the most recent window
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
        """
        Optional low-rate telemetry: emit samples/sec once per second.

        Relies on _batch_loop incrementing self._samples_received_window
        by the number of samples delivered in each batch.
        """
        try:
            while True:
                await asyncio.sleep(1.0)

                # Take and reset the window count
                count = self._samples_received_window
                self._samples_received_window = 0
                self._last_throughput_emit = time.monotonic()

                # Emit samples-per-second estimate
                # (count is "samples delivered in the last ~1s")
                self.throughputUpdated.emit(float(count))

        except asyncio.CancelledError:
            # Normal termination when task is cancelled (disconnect/shutdown)
            pass


    # ── Writes (commands) ─────────────────────────────────────────────────────
    async def _send_command_async(self, text: str, nowait: bool, tag: str) -> None:
        """
        Append NUL, encode UTF-8, and write to the write characteristic.
        - If disconnected: emit writeCompleted(False, tag) and return.
        - If nowait: fire-and-forget (no writeCompleted).
        - Else: await response
                    "100000" : "M:0"
                    "1000000" : "M:1"
                    "10000000" : "M:2"
                    "100000000"=True, then emit writeCompleted(bool, tag).
        """
        # TODO:
        # - if state not CONNECTED -> writeCompleted(False, tag); return
        # - payload = text.encode('utf-8') + b'\x00'
        # - try:
        #       if nowait: await client.write_gatt_char(uuid, payload, response=False)
        #                (and do NOT emit writeCompleted)
        #       else:
        #           await client.write_gatt_char(uuid, payload, response=True)
        #           self.writeCompleted.emit(True, tag)
        #   except Exception as e:
        #       if not nowait: self.writeCompleted.emit(False, tag)
        pass
    
    def write_command_from_key(self, key: EPGControlKey, value: Any) -> str | None:
        """
            Returns the BLE command string for an engineering key/value.
        """
        # match key: 
        #     ### 1. Input Resistance ------------------------------
        #     case EPGControlKey.INPUT_RESISTANCE:
        #         conversion = {
        #             "100000" : "M:0",        # 100k
        #             "1000000" : "M:1",       # 1M
        #             "10000000": "M:2",       # 10M
        #             "100000000": "M:3",      # 100M
        #             "1000000000": "M:6",     # 1G
    
                    

        #         }
    




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

    BLE_ADDRESS = "C2:83:79:F8:C2:86"


    app = QApplication(sys.argv)

    # --- Simple window to show status and logs ---
    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)

    status_label = QLabel(f"Status: disconnected")
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
    win.setWindowTitle("BLEIoHandler tester")
    win.show()

    # --- Instantiate and start BLE worker ---
    ble = BLEIoHandler(
        batch_interval_ms=50,         # easy to read in logs
        max_buffer_seconds=2.0,
        enable_throughput_telemetry=True,
    )
    ble.start()  # starts the QThread + asyncio loop

    # --- Helpers for logging & UI updates ---

    def log_line(msg: str) -> None:
        log.append(msg)
        # Also print to console for debugging
        print(msg)

    def on_connection_state(connected: bool) -> None:
        # We only have a bool here; you can add a helper in BLEIoHandler if you want more granularity.
        if connected:
            status_label.setText("Status: connected")
            connect_button.setEnabled(False)
            disconnect_button.setEnabled(True)
        else:
            # Could be connecting/reconnecting/disconnected; for now, just show generic
            status_label.setText("Status: not connected")
            connect_button.setEnabled(True)
            disconnect_button.setEnabled(False)
        log_line(f"[Signal] connectionStateChanged: {connected}")

    def on_error(message: str, code: int) -> None:
        text = f"[Signal] errorOccurred (code={code}): {message}"
        log_line(text)

    def on_data_batch(timestamps: object, voltages: object) -> None:
        # timestamps, voltages are np.ndarray (dtype uint64/int32)
        ts = np.asarray(timestamps)
        vv = np.asarray(voltages)
        n = len(ts)
        if n == 0:
            return

        t0 = int(ts[0])
        t1 = int(ts[-1])
        v0 = int(vv[0])
        v1 = int(vv[-1])

        log_line(
            f"[Signal] dataBatchReceived: {n} samples, "
            f"t=[{t0} .. {t1}] ms, v=[{v0} .. {v1}] mV"
        )

    def on_throughput(sps: float) -> None:
        throughput_label.setText(f"Throughput: {sps:.1f} samples/s")
        # Optional log:
        # log_line(f"[Signal] throughputUpdated: {sps:.1f} samples/s")

    def on_write_completed(ok: bool, tag: str) -> None:
        log_line(f"[Signal] writeCompleted(tag='{tag}'): ok={ok}")

    # --- Wire up signals ---
    ble.connectionStateChanged.connect(on_connection_state)
    ble.errorOccurred.connect(on_error)
    ble.dataBatchReceived.connect(on_data_batch)
    ble.throughputUpdated.connect(on_throughput)
    ble.writeCompleted.connect(on_write_completed)

    # --- Button actions ---

    def do_connect():
        log_line(f"[Action] connectTo({BLE_ADDRESS})")
        # Set sticky connect by default (BLEIoHandler does this in connectTo)
        ble.connectTo(BLE_ADDRESS, NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID)

    def do_disconnect():
        log_line("[Action] disconnectFrom()")
        ble.disconnectFrom()

    connect_button.clicked.connect(do_connect)
    disconnect_button.clicked.connect(do_disconnect)

    # Optional: send a simple command when connected (uncomment if desired)
    # def send_start_when_connected(connected: bool):
    #     if connected:
    #         log_line("[Action] sending 'ON\\0' command")
    #         ble.sendCommand("ON", nowait=False, tag="startup-ON")
    # ble.connectionStateChanged.connect(send_start_when_connected)

    # --- Clean shutdown on app exit ---
    def on_about_to_quit():
        log_line("[Action] app.aboutToQuit -> ble.stop()")
        ble.stop()

    app.aboutToQuit.connect(on_about_to_quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()