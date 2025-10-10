import sys
import time
import random
import asyncio
from bleak import BleakClient, BleakScanner
from typing import Callable, Optional
from PyQt6.QtCore import QObject, pyqtSignal, QThread

# ---------- CONFIG ----------
BLE_ADDRESS = "C2:83:79:F8:C2:86"  # sw team's test board. acutal address will be passed in at runtime
NOTIFY_CHARACTERISTIC_UUID = "445817D2-9E86-1078-1F76-703DC002EF42"
WRITE_CHARACTERISTIC_UUID  = "445817D2-9E86-1078-1F76-703DC002EF43"  
MAX_PACKET = 180  # conservative; maybe raise?
# ----------------------------

# Windows WinRT needs STA when using Bleak in a non-main thread
try:
    if sys.platform.startswith("win"):
        from bleak.backends.winrt.util import allow_sta as _allow_sta
    else:
        _allow_sta = None
except Exception:
    _allow_sta = None


class BluetoothIO(QObject):
    """
    TODO: fix QThread error on app close
    Qt-friendly Bluetooth Low Energy (BLE) I/O manager.

    This class encapsulates all asynchronous BLE communication
    inside a dedicated QThread running its own asyncio event loop.
    Uses Bleak for cross-platform BLE support and exposes
    Qt signals for thread-safe communication with the UI.

    Lifecycle:
        - Call `start(address)` to launch a QThread and begin
          connecting to the target device.
        - Once connected, notifications from `_notify_uuid`
          are decoded and emitted via `lineReceived(str)`.
        - Outgoing writes are enqueued with `send(bytes)`;
          the writer task serializes them and writes to `_write_uuid`.
        - Call `stop()` (or let the app exit) to cancel tasks,
          stop notifications, disconnect, and cleanly tear down
          the thread and event loop.

    Notes:
        On Windows, the thread is initialized as an single-threaded 
        apartment (STA) to satisfy WinRT’s BLE API. On all platforms, 
        the connection manager attempts to reconnect with exponential 
        backoff if the device is lost.
    """

    connectedChanged = pyqtSignal(bool)     # Emitted when connection state changes.
    reconnectingChanged = pyqtSignal(bool)  # Emitted while in the grace-period fast reconnect loop.
    lineReceived = pyqtSignal(str)          # Emitted for each decoded UTF-8 notification.
    error = pyqtSignal(str)                 # Emitted on recoverable errors for logging or UI feedback.

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: Optional[QThread] = None                   # Dedicated Qt thread that hosts the asyncio event loop
        self._loop: Optional[asyncio.AbstractEventLoop] = None   # Asyncio event loop running inside self._thread.
        self._runner_task: Optional[asyncio.Task] = None         # Long-running coroutine that manages BLE connection & reconnection.
        self._writer_task: Optional[asyncio.Task] = None         # Long-running coroutine that drains the write queue and sends data.
        self._client: Optional[BleakClient] = None               # Active BleakClient object when connected; None otherwise.
 
        self._address: Optional[str] = None                      # Target BLE device address (or identifier) to connect to.
        self._stop_event = asyncio.Event()                       # Async event used to signal shutdown of manager/writer tasks. 
        self._write_queue: Optional[asyncio.Queue[bytes]] = None # Queue of outgoing payloads to be written to the device.

        # tunables
        self._notify_uuid = NOTIFY_CHARACTERISTIC_UUID           # UUID of the characteristic to subscribe to for notifications.
        self._write_uuid = WRITE_CHARACTERISTIC_UUID             # UUID of the characteristic used for writing commands.
        self._max_packet = MAX_PACKET                            # Maximum chunk size for GATT writes.
        self._connecting = False                                 # True during initial connect attempt(s)
        self._reconnecting = False                               # True during grace reconnect window after a drop
        self._use_jitter = True                                  # Whether to add small random delay to backoff sleeps
        self._ever_connected = False                             # Tracks if we’ve had at least one successful session

        # timeouts (seconds)
        self._scan_timeout    = 2.0   # BleakScanner.find_device_by_address
        self._connect_timeout = 3.0   # client.connect()
        self._notify_timeout  = 1.0   # client.start_notify()
        self._grace_seconds   = 2.0   # quick reconnect window after a drop

    def start(self, address: str):
        """Spin up a QThread + asyncio loop and start connecting."""
        if self._thread:
            return
        self._address = address

        self._thread = QThread(self)  # main thread that hosts bluetooth event loop and tasks
        self.moveToThread(self._thread)
        self._thread.started.connect(self._bootstrap_loop)
        self._thread.finished.connect(self._teardown_loop)
        self._thread.start()


    def stop(self):
        """Signal the loop to stop and wait for the thread to finish."""
        if not self._thread:
            return
        # marshal to loop thread
        def _ask_stop():
            if self._loop and not self._stop_event.is_set():
                self._stop_event.set()
        self._post_to_loop(_ask_stop)

        self._thread.quit()
        self._thread = None

    def send(self, payload: bytes):
        """Enqueue a write"""
        def _enqueue():
            if self._write_queue is not None:
                self._write_queue.put_nowait(payload)
        self._post_to_loop(_enqueue)

    def _bootstrap_loop(self):
        # Windows WinRT needs STA in non-main threads
        if _allow_sta:
            _allow_sta()

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._stop_event = asyncio.Event()
        self._write_queue = asyncio.Queue()

        # Run the manager and writer concurrently
        async def _main():
            # start tasks
            self._runner_task = asyncio.create_task(self._run_manager())
            self._writer_task = asyncio.create_task(self._run_writer())
            try:
                # wait for stop request
                await self._stop_event.wait()
            finally:
                # orderly shutdown: cancel tasks, stop notify, disconnect
                for t in (self._writer_task, self._runner_task):
                    if t and not t.done():
                        t.cancel()
                await asyncio.gather(
                    *(t for t in (self._writer_task, self._runner_task) if t),
                    return_exceptions=True
                )
                if self._client:
                    try:
                        await self._client.stop_notify(self._notify_uuid)
                    except Exception:
                        pass
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass

        # block the thread until _main() finishes
        try:
            self._loop.run_until_complete(_main())
        finally:
            # ensure the loop is fully stopped/closed before thread exits
            self._loop.stop()
            self._loop.close()

    def _teardown_loop(self):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop = None
        self._runner_task = None
        self._writer_task = None
        self._client = None
        self._write_queue = None

    def _post_to_loop(self, fn: Callable[[], None]):
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(fn) 


    async def _resolve_visible_device(self, addr: str, timeout: float = 6.0):
        """Return a Bleak device handle if the target address is currently visible."""
        return await BleakScanner.find_device_by_address(addr, timeout=timeout)
    
    async def _connect_and_subscribe(self, device, connect_timeout: float = 5.0, notify_timeout: float = 2.0):
        """
        Connect to `device` and start notifications.
        Returns an initialized BleakClient on success; None on failure (already cleaned up).
        """
        client = BleakClient(device)
        try:
            await asyncio.wait_for(client.connect(), timeout=connect_timeout)
            await asyncio.wait_for(client.start_notify(self._notify_uuid, self._on_notify), timeout=notify_timeout)
            return client
        except Exception as e:
            self.error.emit(f"BLE error: {e}")
            try:
                await asyncio.wait_for(client.disconnect(), timeout=0.8)
            except Exception:
                pass
            return None
        
    async def _connected_session_loop(self, client: BleakClient):
        """
        Pump the connected session until the link drops or stop is requested.
        Returns when client.is_connected becomes False or stop_event is set.
        """
        self._client = client
        self.connectedChanged.emit(True)
        self.send(bytearray("ON", 'utf-8') + b'\0')
        self.send(bytearray("START", 'utf-8') + b'\0')

        try:
            while client.is_connected and not self._stop_event.is_set():
                await asyncio.sleep(0.1)
        finally:
            # best-effort disconnect to ensure a clean state before any retry
            try:
                await asyncio.wait_for(client.disconnect(), timeout=0.8)
            except Exception:
                pass
            self.connectedChanged.emit(False)

    async def _grace_reconnect_window(self, addr: str, seconds: float) -> bool:
        """
        Try quick reconnects for `seconds`. Returns True if we recovered, else False.
        Emits reconnectingChanged(True/False) around the attempt window.
        """
        self.reconnectingChanged.emit(True)
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not self._stop_event.is_set():
                device = await self._resolve_visible_device(self._address, timeout=self._scan_timeout)
                if not device:
                    await asyncio.sleep(0.3)
                    continue

                client2 = await self._connect_and_subscribe(device, connect_timeout=3.0, notify_timeout=2.0)
                if client2 is None:
                    await asyncio.sleep(0.3)
                    continue

                # recovered: run a connected loop; when it exits we return to caller
                await self._connected_session_loop(client2)
                return True  # either dropped again (caller may re-enter grace) or stop requested
            return False
        finally:
            self.reconnectingChanged.emit(False)

    async def _backoff_sleep(self, backoff: float) -> float:
        """
        Sleep for backoff (+ tiny jitter if enabled) and return the next backoff value.
        """
        if self._use_jitter:
            await asyncio.sleep(backoff + random.uniform(0.0, 0.25))
        else:
            await asyncio.sleep(backoff)
        return min(backoff * 2.0, 5.0)

    async def _run_manager(self):
        """Coordinator: resolve -> connect -> session -> grace reconnect -> backoff retry."""
        backoff = 0.5
        ever_connected = False

        while not self._stop_event.is_set():
            try:
                if not self._address:
                    self.error.emit("No BLE address provided.")
                    await asyncio.sleep(1.0)
                    continue

                # Resolve current visibility
                device = await self._resolve_visible_device(self._address, timeout=self._scan_timeout)
                if not device:
                    # Before first success, we just report disconnected; after that, rely on grace/backoff
                    if not ever_connected:
                        self.connectedChanged.emit(False)
                    backoff = await self._backoff_sleep(backoff)
                    continue

                # Connect + subscribe
                client = await self._connect_and_subscribe(
                    device,
                    connect_timeout=self._connect_timeout,
                    notify_timeout=self._notify_timeout
                )
                if client is None:
                    backoff = await self._backoff_sleep(backoff)
                    continue

                # Connected session; returns when link drops or stop is requested
                await self._connected_session_loop(client)
                ever_connected = True

                # If we didn’t request stop, try a grace reconnect window before declaring Disconnected
                if not self._stop_event.is_set():
                    recovered = await self._grace_reconnect_window(self._address, self._grace_seconds)
                    if recovered:
                        # If it recovered, just continue loop; small pause avoids hot-spin
                        await asyncio.sleep(0.1)
                        continue

                # No recovery within grace → backoff before a full retry
                backoff = await self._backoff_sleep(backoff)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error.emit(f"BLE error: {e!s}")
                backoff = await self._backoff_sleep(backoff)

        # Ensure final signals are sane on exit
        self.reconnectingChanged.emit(False)
        self.connectedChanged.emit(False)

    async def _run_writer(self):
        """Drain the write queue; chunk payloads to safe GATT sizes."""
        while not self._stop_event.is_set():
            try:
                payload = await self._write_queue.get()
                client = self._client
                if not client or not client.is_connected:
                    # TODO: write behavior if client not connected when a payload is sent
                    # silently drops payload currently
                    continue

                for chunk in _chunk(payload, self._max_packet):
                    await client.write_gatt_char(self._write_uuid, chunk)
                    await asyncio.sleep(0.002)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error.emit(f"Write failed: {e!s}")
                # short pause to avoid tight loop on persistent failures
                await asyncio.sleep(0.05)

    def _on_notify(self, handle: int, data: bytearray):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            print(f"Notification decode error: {e}")
            return
        self.lineReceived.emit(text)

def _chunk(b: bytes, size: int):
    for i in range(0, len(b), size):
        yield b[i:i+size]

async def _gather_silent(*tasks: Optional[asyncio.Task]):
    for t in tasks:
        if not t:
            continue
        try:
            await t
        except Exception:
            pass

if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QTextEdit, QLabel
    app = QApplication(sys.argv)

    # Simple window to show logs
    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    log = QTextEdit(readOnly=True)
    layout.addWidget(QLabel(f"Connecting to {BLE_ADDRESS}..."))
    layout.addWidget(log)
    win.setCentralWidget(central)
    win.resize(500, 300)
    win.show()

    # Start BLE worker
    ble = BluetoothIO()

    def log_and_print(msg: str):
        log.append(msg)
        print(msg)

    ble.connectedChanged.connect(lambda ok: log_and_print(f"[Signal] Connected: {ok}"))
    ble.reconnectingChanged.connect(lambda r: log_and_print(f"[Signal] Reconnecting: {r}"))
    ble.lineReceived.connect(lambda line: log_and_print(f"[Signal] Notify: {line.strip()}"))
    ble.error.connect(lambda msg: log_and_print(f"[Signal] Error: {msg}"))

    ble.start(BLE_ADDRESS)

    # clean stop on exit
    def on_exit():
        ble.stop()
    app.aboutToQuit.connect(on_exit)

    sys.exit(app.exec())