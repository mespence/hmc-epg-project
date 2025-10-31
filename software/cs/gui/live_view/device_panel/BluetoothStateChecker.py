import sys
import shutil
import subprocess
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread, QTimer, QMetaObject

if sys.platform.startswith("win"):
    try:
        from winrt.windows.devices import radios
    except Exception:
        radios = None
else:
    radios = None


class _BluetoothStateWorker(QObject):
    """Runs periodic Bluetooth state checks in its own thread."""
    stateReady = pyqtSignal(bool, bool)  # (has_adapter, enabled)

    def __init__(self, poll_interval_ms: int = 2000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self.check_once)

    @pyqtSlot()
    def start(self):
        if not self._running:
            self._running = True
            self._timer.start()
            self.check_once()

    @pyqtSlot()
    def stop(self):
        self._running = False
        self._timer.stop()

    @pyqtSlot()
    def check_once(self):
        if not self._running:
            return

        has_adapter, enabled = False, False

        if sys.platform.startswith("win") and radios is not None:
            try:
                radios_list = radios.Radio.get_radios_async().get()
                for r in radios_list:
                    if r.kind == radios.RadioKind.BLUETOOTH:
                        has_adapter = True
                        enabled = (r.state == radios.RadioState.ON)
                        break
            except Exception:
                pass

        if not has_adapter and sys.platform.startswith("win"):
            try:
                result = subprocess.run(
                    ["powershell", "-Command", "Get-PnpDevice -Class Bluetooth"],
                    capture_output=True, text=True
                )
                has_adapter = bool(result.stdout.strip())
            except Exception:
                has_adapter = False

        self.stateReady.emit(has_adapter, enabled)


class BluetoothStateChecker(QObject):
    """
    Manages periodic Bluetooth state checks with a worker thread.
    Emits stateChanged(has_adapter, enabled).
    """
    stateChanged = pyqtSignal(bool, bool)

    def __init__(self, poll_interval_ms: int = 2000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _BluetoothStateWorker(poll_interval_ms=poll_interval_ms)
        self._worker.moveToThread(self._thread)
        self._worker.stateReady.connect(self.stateChanged)

        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def stop(self):
        QMetaObject.invokeMethod(self._worker, "stop", QMetaObject.QueuedConnection)
        self._thread.quit()
        self._thread.wait(1000)

    @staticmethod
    def open_settings():
        """Open the system's Bluetooth settings panel (platform-specific)."""
        if sys.platform.startswith("win"):
            subprocess.Popen(["start", "ms-settings:bluetooth"], shell=True)
        elif sys.platform.startswith("linux"):
            if shutil.which("gnome-control-center"):
                subprocess.Popen(["gnome-control-center", "bluetooth"])
            elif shutil.which("kcmshell5"):
                subprocess.Popen(["kcmshell5", "bluetooth"])
            else:
                print("Bluetooth settings command not found. Please open it manually.")
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "/System/Library/PreferencePanes/Bluetooth.prefPane"])
        else:
            print(f"Bluetooth settings command not found for platform {sys.platform}. Please open it manually.")
