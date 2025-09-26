from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, 
    QFrame, QPushButton, QSizePolicy, QDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal
)
from PyQt6.QtGui import QIcon

import os
import re
import sys
import json

from live_view.device_panel.DeviceManager import BluetoothManager
from live_view.device_panel.DeviceWidget import DeviceWidget
from live_view.device_panel.AddDeviceWidget import AddDeviceWidget
from live_view.BluetoothIO import BluetoothIO


class DevicePanel(QWidget):
    """
    UI panel showing Bluetooth status and settings button.
    Emits bluetoothEnabledChanged(enabled).
    """
    bluetoothEnabledChanged = pyqtSignal(bool)
    DEVICES_FILE = "epg_devices.json"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device Panel")
        self.setMinimumWidth(300) 

        # === Bluetooth === 
        self.bt_io: BluetoothIO = BluetoothIO()
        self.connected_address: str | None = None
        self.pending_address: str | None = None
        self.device_connected: bool = False
        
        def place_line_in_live_buffer(line: str):
            index, voltage = (int(x) for x in line.strip().split(','))
            self.parent().datawindow.buffer_data.append((index/1e4, voltage/1000))
            self.parent().datawindow.current_time = index/1e4
        
        self.bt_io.lineReceived.connect(place_line_in_live_buffer)
        self.bt_io.connectedChanged.connect(lambda connected: setattr(self, "device_connected", connected))
        self.bt_io.connectedChanged.connect(self._on_bt_connected_changed)
        self.bt_io.reconnectingChanged.connect(self._on_bt_reconnecting_changed)
        self.bt_io.error.connect(lambda e: self._on_bt_error(e))



        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Title
        title_label = QLabel("Bluetooth EPG Devices")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title_label)

        # Horizontal rule
        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr)

        bluetooth_title = QLabel("Bluetooth")
        bluetooth_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        bluetooth_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(bluetooth_title)

        self.status_label = QLabel("Checking Bluetooth...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status_label)

        # Button container to preserve space
        self.button_container = QWidget()
        button_layout = QVBoxLayout(self.button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.open_settings_btn = QPushButton("  Open Bluetooth Settings  ")
        self.open_settings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.button_container.setFixedHeight(self.open_settings_btn.sizeHint().height())
        button_layout.addWidget(self.open_settings_btn)
        layout.addWidget(self.button_container)

        # Bluetooth manager
        self.bt_manager = BluetoothManager()
        self.bt_manager.stateChanged.connect(self.handle_bluetooth_result)
        self.open_settings_btn.clicked.connect(self.bt_manager.open_settings)

        # Horizontal rule
        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr)


        # --- Device section wrapper ---
        self.device_section = QWidget()
        device_section_layout = QVBoxLayout(self.device_section)
        device_section_layout.setContentsMargins(0, 0, 0, 0)

        device_list_title = QLabel("EPG Device List")
        device_list_title.setContentsMargins(10, 5, 10, 5)
        device_list_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        device_list_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        device_section_layout.addWidget(device_list_title)

        # Device list container
        self.device_layout = QVBoxLayout()
        device_section_layout.addLayout(self.device_layout)

        # Add-device button
        self.add_device_button = AddDeviceWidget()
        self.add_device_button.clicked.connect(self.add_device_dialog)
        device_section_layout.addWidget(self.add_device_button)

        layout.addWidget(self.device_section)

        self.setLayout(layout)

        # Load devices from JSON
        self.load_devices()

    def load_devices(self):
        if os.path.exists(self.DEVICES_FILE):
            with open(self.DEVICES_FILE, "r") as f:
                try:
                    devices = json.load(f)
                except json.JSONDecodeError:
                    devices = []
        else:
            devices = []

        for dev in devices:
            mac = dev.get("MAC Address")
            name = dev.get("Name", "Unnamed Device")
            self.add_device_widget(mac, name, save=False)

    def save_devices(self):
        devices = []
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget):
                devices.append({"MAC Address": w.mac, "Name": w.name})
        with open(self.DEVICES_FILE, "w") as f:
            json.dump(devices, f, indent=2)

    def remove_device(self, mac: str):
        """Remove a device by MAC address from layout + JSON."""
        # Find and remove from layout
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget) and w.mac == mac:
                w.setParent(None)  # remove widget from layout
                break

        # Update JSON file
        self.save_devices()

    # --- Add new device ---
    def add_device_widget(self, mac: str, name: str, save=True):
        dev = DeviceWidget(mac=mac, name=name, status="Disconnected")

        # Hook up signals
        dev.forgetRequested.connect(self.remove_device)
        dev.editRequested.connect(lambda _: self.save_devices())
        dev.macEditRequested.connect(lambda _: self.save_devices())
        dev.connectRequested.connect(lambda m, w=dev: self.on_device_clicked(w))

        self.device_layout.addWidget(dev)
        if save:
            self.save_devices()

    def add_device_dialog(self):
        dlg = AddDeviceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            mac, name = dlg.get_values()
            if mac:  # only add if valid
                self.add_device_widget(mac, name)

    def _find_device_widget(self, mac: str) -> DeviceWidget | None:
        for i in range(self.device_layout.count()):
            device = self.device_layout.itemAt(i).widget()
            if isinstance(device, DeviceWidget) and device.mac == mac:
                return device
        return None
    
    def _set_device_status(self, mac: str | None, status: str) -> None:
        if not mac:
            return
        device = self._find_device_widget(mac)
        if device:
            device.set_status(status)
            device.update()

    def _clear_other_devices_connected(self, keep_mac: str | None) -> None:
        """Ensure only one device shows 'Connected'."""
        for i in range(self.device_layout.count()):
            device = self.device_layout.itemAt(i).widget()
            if isinstance(device, DeviceWidget) and device.mac != keep_mac:
                if device.status == "Connected":
                    device.set_status("Disconnected")

    def _on_bt_connected_changed(self, connected: bool) -> None:
        if connected:
            # Promote pending address (if any) to the active one
            new_mac = self.pending_address or self.connected_address
            if new_mac and self.connected_address and self.connected_address != new_mac:
                self._set_device_status(self.connected_address, "Disconnected")

            self.connected_address = new_mac
            self.pending_address = None
            self.device_connected = True

            if self.connected_address:
                self._set_device_status(self.connected_address, "Connected")
                self._clear_other_devices_connected(self.connected_address)
        else:
            # Disconnected. If not currently attempting a switch, mark the active one disconnected.
            self.device_connected = False
            if not self.pending_address and self.connected_address:
                self._set_device_status(self.connected_address, "Disconnected")
                self.connected_address = None

    def _on_bt_reconnecting_changed(self, reconnecting: bool) -> None:
        target_mac = self.connected_address or self.pending_address
        if reconnecting:
            self._set_device_status(target_mac, "Reconnecting...")
        # when reconnecting finishes, connectedChanged(True/False) will set final status

    def _on_bt_error(self, err: str) -> None:
        mac = self.connected_address or self.pending_address
        if mac:
            self._set_device_status(mac, "Error")



    def handle_bluetooth_result(self, has_adapter: bool, enabled: bool):
        """
        Update UI based on Bluetooth state.
        Args:
            has_adapter: True if adapter present.
            enabled: True if Bluetooth is on.
        """
        if not has_adapter:
            self.status_label.setText("No Bluetooth adapter found.")
            self.open_settings_btn.setVisible(False)
            self.bluetoothEnabledChanged.emit(False)
            self.device_section.setEnabled(False)   # gray out device section
        elif enabled:
            self.status_label.setText("Bluetooth is ON")
            self.open_settings_btn.setVisible(False)
            self.bluetoothEnabledChanged.emit(True)
            self.device_section.setEnabled(True)    # enable device section
        else:
            self.status_label.setText("Bluetooth is OFF")
            self.open_settings_btn.setVisible(True)
            self.bluetoothEnabledChanged.emit(False)
            self.device_section.setEnabled(False)   # gray out device section

    def on_device_clicked(self, device: DeviceWidget):
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

            choice = msg.exec()
            
            if not choice == QMessageBox.StandardButton.Yes:
                return
            
        if self.device_connected and self.connected_address == device.mac:
            return
        
        previous_mac = self.connected_address if (self.connected_address and self.connected_address != device.mac) else None
        if previous_mac:
            self._set_device_status(previous_mac, "Disconnecting...")

        self.bt_io.stop()

        if previous_mac:
            self._set_device_status(previous_mac, "Disconnected")
        self.connected_address = None
        self.device_connected = False  # UI truth until the new one connects
            
        self.pending_address = device.mac
        self._set_device_status(self.pending_address, "Connecting...")

        self.bt_io.start(device.mac)

        print(f"Clicked device: Name={device.name}, MAC={device.mac}")

    def _on_bt_error(self, err: str) -> None:
        mac = self.connected_address or self.pending_address
        if mac:
            self._set_device_status(mac, "Error")

    def _on_device_mac_changed(self, old_mac: str, widget: DeviceWidget) -> None:
        """
        Called when a DeviceWidget's MAC address is edited.
        Keeps DevicePanel's internal references in sync.
        """
        new_mac = widget.mac
        if self.connected_address == old_mac:
            self.connected_address = new_mac
        if self.pending_address == old_mac:
            self.pending_address = new_mac

class AddDeviceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Device")

        layout = QFormLayout(self)

        self.mac_input = QLineEdit()
        self.mac_input.setPlaceholderText("00:11:22:33:44:55")
        layout.addRow("MAC Address:", self.mac_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Unnamed Device")
        layout.addRow("Device Name:", self.name_input)

        # OK / Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        mac = self.mac_input.text().strip()
        if not is_valid_mac(mac):
            QMessageBox.warning(
                self, "Invalid MAC",
                "Please enter a valid MAC address (format: 00:11:22:33:44:55)."
            )
            return  # don’t close, let user fix input
        super().accept()

    def get_values(self):
        mac = self.mac_input.text().strip()
        name = self.name_input.text().strip() or "Unnamed Device"
        return mac, name
    
def is_valid_mac(addr: str) -> bool:
    pattern = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    return bool(pattern.match(addr.strip()))

def main():
    app = QApplication(sys.argv)
    w = DevicePanel()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()