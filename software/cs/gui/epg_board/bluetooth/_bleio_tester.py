import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QTextEdit, QLabel, QPushButton,
)

if __name__ == "__main__":
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

from bluetooth.BLEIOHandler import BLEIOHandler, ConnectionState

BLE_ADDRESS = "C2:83:79:F8:C2:86"  # CS's test nRF board
NOTIFY_CHARACTERISTIC_UUID = "445817D2-9E86-1078-1F76-703DC002EF42"
WRITE_CHARACTERISTIC_UUID  = "445817D2-9E86-1078-1F76-703DC002EF43"


def main():
    """
    Minimal testing window for BLEIOHandler
    """
    app = QApplication(sys.argv)
    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)

    status_label = QLabel("Status: Disconnected")
    status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

    log = QTextEdit()
    log.setReadOnly(True)

    connect_button = QPushButton(f"Connect to {BLE_ADDRESS}")
    disconnect_button = QPushButton("Disconnect")
    disconnect_button.setEnabled(False)

    layout.addWidget(status_label)
    layout.addWidget(connect_button)
    layout.addWidget(disconnect_button)
    layout.addWidget(log)

    win.setCentralWidget(central)
    win.resize(600, 350)
    win.setWindowTitle("BLEIOHandler minimal tester")
    win.show()

    ble = BLEIOHandler(
        batch_interval_ms=50,
        max_buffer_seconds=2.0,
        enable_throughput_telemetry=False,
    )
    ble.start()

    def on_connection_state(state: ConnectionState) -> None:
        status_label.setText(f"Status: {state.value}")
        is_connected = (state == ConnectionState.CONNECTED)
        connect_button.setEnabled(not is_connected)
        disconnect_button.setEnabled(is_connected)
        log.append(f"[connectionStateChanged] {state.value}")

        if is_connected:
            ble.sendCommand("ON",    tag="startup-ON")
            ble.sendCommand("START", tag="startup-START")

    def on_data_batch(ts, vs) -> None:
        n = len(ts)
        if n == 0:
            return
        log.append(f"[data] {n} samples, t=[{ts[0]}..{ts[-1]}] ms, v=[{vs[0]}..{vs[-1]}] mV")

    ble.connectionStateChanged.connect(on_connection_state)
    ble.errorOccurred.connect(lambda msg, c: log.append(msg))
    ble.dataBatchReceived.connect(on_data_batch)

    def do_connect():
        log.append(f"[action] connectTo({BLE_ADDRESS})")
        ble.connectTo(BLE_ADDRESS, NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID)

    def do_disconnect():
        log.append("[action] disconnect()")
        ble.disconnect()

    connect_button.clicked.connect(do_connect)
    disconnect_button.clicked.connect(do_disconnect)

    app.aboutToQuit.connect(ble.stop)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()