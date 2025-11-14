import os
import sys
import json
from dataclasses import dataclass
from typing import Optional, List
from numpy.typing import NDArray

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QSizePolicy,
    QMessageBox,
)

# -------- Optional Windows-only import for radio state --------
if sys.platform.startswith("win"):
    try:
        from winrt.windows.devices import radios
    except Exception:
        radios = None
else:
    radios = None

# -------- Project imports --------
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from epg_board.bluetooth.BLEIOHandler import BLEIOHandler, ConnectionState
from live_view.device_panel.DevicePanelWidgets import DeviceWidget, AddDeviceWidget 
from live_view.device_panel.BluetoothStateChecker import BluetoothStateChecker


# =========================
# Utilities & Data Layer
# =========================

DEVICES_FILE = "epg_devices.json" # TODO: make this controlled by settings, with JSON importing/exporting?

@dataclass
class DeviceRecord:
    mac: str
    name: str = "Unnamed Device"

class DeviceStore:
    """Tiny persistence helper for device list."""
    path: str

    def __init__(self, path: str = DEVICES_FILE):
        self.path = path

    def load(self) -> List[DeviceRecord]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)
        except Exception:
            return []
        out: List[DeviceRecord] = []
        for d in raw:
            mac = d.get("MAC Address")
            name = d.get("Name", "Unnamed Device")
            if mac:
                out.append(DeviceRecord(mac=mac, name=name))
        return out

    def save(self, devices: List[DeviceRecord]) -> None:
        data = [{"MAC Address": d.mac, "Name": d.name} for d in devices]
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

# =========================
# Main Panel
# =========================

class DevicePanel(QWidget):
    """
    UI panel showing Bluetooth radio state and a saved device list.
    Emits bluetoothEnabledChanged(enabled).
    """
    bluetoothEnabledChanged = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Device Panel")
        self.setMinimumWidth(300)

        # --- Services ---
        self.store = DeviceStore()
        self.bt_state = BluetoothStateChecker(poll_interval_ms=2000, parent=self)
        self.bt_state.stateChanged.connect(self._on_bt_state_changed)

        self.ble_io: BLEIOHandler = BLEIOHandler()
        self.ble_io.start()
        self.connected_address: Optional[str] = None
        self.pending_address: Optional[str] = None
        self.device_connected: bool = False

        # Wire BluetoothIO to live buffer
        def place_batch_in_live_buffer(timestamps: NDArray, voltages: NDArray):
            if __name__ == "__main__":
                return
            for data_point in zip(timestamps, voltages):
                #print((data_point[0] / 1e3, data_point[1] / 1000))
                self.parent().datawindow.buffer_data.append((data_point[0] / 1e3, data_point[1] / 1000))            
                self.parent().datawindow.current_time = data_point[0] / 1e3

        self.ble_io.dataBatchReceived.connect(place_batch_in_live_buffer)
        self.ble_io.connectionStateChanged.connect(self._on_bt_connected_changed)
        self.ble_io.errorOccurred.connect(self._on_bt_error)

        # --- UI ---
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_label = QLabel("Bluetooth EPG Devices")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title_label)

        hr = QFrame(); hr.setFrameShape(QFrame.Shape.HLine); hr.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr)

        bluetooth_title = QLabel("Bluetooth")
        bluetooth_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        bluetooth_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(bluetooth_title)

        self.status_label = QLabel("Checking Bluetooth...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status_label)

        self.button_container = QWidget()
        button_layout = QVBoxLayout(self.button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.open_settings_btn = QPushButton("  Open Bluetooth Settings  ")
        self.open_settings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.button_container.setFixedHeight(self.open_settings_btn.sizeHint().height())
        button_layout.addWidget(self.open_settings_btn)
        layout.addWidget(self.button_container)
        self.open_settings_btn.clicked.connect(BluetoothStateChecker.open_settings)

        hr2 = QFrame(); hr2.setFrameShape(QFrame.Shape.HLine); hr2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr2)

        self.device_section = QWidget()
        device_section_layout = QVBoxLayout(self.device_section)
        device_section_layout.setContentsMargins(0, 0, 0, 0)

        device_list_title = QLabel("EPG Device List")
        device_list_title.setContentsMargins(10, 5, 10, 5)
        device_list_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        device_list_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        device_section_layout.addWidget(device_list_title)

        self.device_layout = QVBoxLayout()
        device_section_layout.addLayout(self.device_layout)

        self._device_widgets: dict[str, DeviceWidget] = {}

        self.add_device_button = AddDeviceWidget(parent=self)
        self.add_device_button.deviceAdded.connect(self._add_device)
        device_section_layout.addWidget(self.add_device_button)

        layout.addWidget(self.device_section)
        self.setLayout(layout)

        # Load devices
        for dev in self.store.load():
            self._add_device(dev.mac, dev.name, save=False)

    # ---- Device list helpers ----

    def _current_devices(self) -> List[DeviceRecord]:
        return [
            DeviceRecord(mac=w.mac, name=w.name)
            for w in self._device_widgets.values()
        ]

    def _save_devices(self):
        self.store.save(self._current_devices())

    def _find_device_widget(self, mac: Optional[str]) -> Optional[DeviceWidget]:
        if not mac:
            return None
        return self._device_widgets.get(mac)

    def _set_device_status(self, mac: Optional[str], status: str):
        dev = self._find_device_widget(mac)
        if dev is not None:
            dev.set_status(status)
            dev.update()
    
    def _on_mac_edited(self, old_mac: str, widget: DeviceWidget):
        if old_mac in self._device_widgets:
            self._device_widgets.pop(old_mac, None)

        self._device_widgets[widget.mac] = widget
        self._save_devices()

    def _clear_other_devices_connected(self, keep_mac: Optional[str]):
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget) and w.mac != keep_mac and w.status == "Connected":
                w.set_status("Disconnected")

    # ---- UI slots ----
    def _add_device(self, mac: str, name: str, save: bool = True):
        dev = DeviceWidget(mac=mac, name=name, status="Disconnected")
        dev.forgetRequested.connect(self._remove_device)
        dev.editRequested.connect(lambda _m: self._save_devices())
        dev.macEditRequested.connect(
            lambda old_mac, w=dev: self._on_mac_edited(old_mac, w)
        )
        dev.disconnectRequested.connect(
            lambda _m, w=dev: self._disconnect_device_clicked(w)
        )
        dev.connectRequested.connect(
            lambda _m, w=dev: self._on_device_clicked(w)
        )
        self.device_layout.addWidget(dev)
        self._device_widgets[mac] = dev

        if save:
            self._save_devices()

    def _remove_device(self, mac: str):
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget) and w.mac == mac:
                w.setParent(None)
                break
        self._save_devices()
        # Clean up selection state
        if self.connected_address == mac:
            self.connected_address = None
        if self.pending_address == mac:
            self.pending_address = None

    def _remove_device(self, mac: str):
        w = self._device_widgets.pop(mac, None)
        if w is not None:
            # Remove from layout/GUI
            w.setParent(None)
            w.deleteLater()

        if self.connected_address == mac:
            self.connected_address = None
        if self.pending_address == mac:
            self.pending_address = None

        self._save_devices()

    def _disconnect_device_clicked(self, device: DeviceWidget):
        if self.device_connected and self.connected_address == device.mac:
            self._set_device_status(device.mac, "Disconnecting...")
            self.ble_io.disconnect()

    # ---- BluetoothIO state reactions ----
    def _on_bt_connected_changed(self, state: ConnectionState):
        connected: bool = state == ConnectionState.CONNECTED
        self.device_connected = connected

        print(self.connected_address, state)
        print(self.pending_address, connected)

    
        if connected:
            new_mac = self.pending_address or self.connected_address

            # if new_mac and self.connected_address and self.connected_address != new_mac:
            #     self._set_device_status(self.connected_address, "Disconnected")

            self.connected_address = new_mac
            self.pending_address = None
            if self.connected_address:
                self._clear_other_devices_connected(self.connected_address)
                self._set_device_status(self.connected_address, state.name.title())

        else:
            # If not switching, mark current as disconnected
            if not self.pending_address and self.connected_address:
                self._set_device_status(self.connected_address, state.name.title())
                #self._set_device_status(self.connected_address, "Disconnected")
                self.connected_address = None


    # def _on_bt_reconnecting_changed(self, reconnecting: bool):
    #     target_mac = self.connected_address or self.pending_address
    #     if reconnecting:
    #         self._set_device_status(target_mac, "Reconnecting...")
    #     # final status will be set by connectedChanged

    def _on_bt_error(self, err: str, code: int):
        mac = self.connected_address or self.pending_address
        if mac:
            self._set_device_status(mac, "Error")
            print(err)

    def _on_bt_state_changed(self, has_adapter: bool, enabled: bool):
        if has_adapter and not enabled:
            try:
                if self.connected_address:
                    self.ble_io.disconnect()
            except Exception:
                pass

            if self.connected_address:
                self._set_device_status(self.connected_address, "Bluetooth OFF")
            if self.pending_address and self.pending_address != self.connected_address:
                self._set_device_status(self.pending_address, "Bluetooth OFF")

            self.connected_address = None
            self.pending_address = None
            self.device_connected = False

        if not has_adapter:
            self.status_label.setText("No Bluetooth adapter found.")
            self.open_settings_btn.setVisible(False)
            self.bluetoothEnabledChanged.emit(False)
            self.device_section.setEnabled(False)
        elif enabled:
            self.status_label.setText("Bluetooth is ON")
            self.open_settings_btn.setVisible(False)
            self.bluetoothEnabledChanged.emit(True)
            self.device_section.setEnabled(True)
        else:
            self.status_label.setText("Bluetooth is OFF")
            self.open_settings_btn.setVisible(True)
            self.bluetoothEnabledChanged.emit(False)
            self.device_section.setEnabled(False)


    def _on_device_clicked(self, device: DeviceWidget):
        if self.device_connected:
            msg = QMessageBox()
            msg.setWindowIcon(QIcon("SCIDO.ico"))
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("SCIDO - Confirm Device Connection")
            msg.setText(
                f"<b>Connecting to {device.name} will disconnect the current device.</b><br><br>"
                "Do you want to continue?"
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            if msg.exec() != QMessageBox.StandardButton.Yes:
                return

        if self.device_connected and self.connected_address == device.mac:
            return

        previous_mac = self.connected_address if (self.connected_address and self.connected_address != device.mac) else None
        # if previous_mac:
        #     self._set_device_status(previous_mac, "Disconnecting...")

        # #self.ble_io.disconnect()
        # if previous_mac:
        #     self._set_device_status(previous_mac, "Disconnected")

        self.connected_address = None
        self.device_connected = False

        self.pending_address = device.mac
        #self._set_device_status(self.pending_address, "Connecting...")
        self.ble_io.connectTo(device.mac)
        # self.parent().start_recording()
        self.parent().datawindow.plot_update_timer.start()


        print(f"Connecting to device: Name={device.name}, MAC={device.mac}")

    def closeEvent(self, event):
        try:
            self.ble_io.stop()
        except RuntimeError as e:
            print(f"[DevicePanel closeEvent] ble_io.stop() failed: {e!r}")
        try:
            self.bt_state.stop()
        except RuntimeError as e:
            print(f"[DevicePanel closeEvent] bt_state.stop() failed: {e!r}")

        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = DevicePanel()
    w.show()
    sys.exit(app.exec())
