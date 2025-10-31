from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSizePolicy, QDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QMessageBox,
    QInputDialog, QMenu, QToolButton
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, pyqtSlot, QSize,
    QThread, QObject, QTimer, QMetaObject
)
from PyQt6.QtGui import QIcon, QPainter, QBrush, QColor, QPen, QAction

import os
import re
import sys
import json
from dataclasses import dataclass
from typing import Optional, List

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

from epg_board.BluetoothIO import BluetoothIO
from live_view.device_panel.BluetoothStateChecker import BluetoothStateChecker
from utils.SVGIcon import svg_to_colored_pixmap



# TODO: consolidate code in DeviceWidget and AddDeviceWidget to reduce code duplication

# =========================
# Utilities & Data Layer
# =========================

DEVICES_FILE = "epg_devices.json"

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
def is_valid_mac(addr: str) -> bool:
    return bool(MAC_RE.match(addr.strip()))

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
# UI Widgets
# =========================

class DeviceWidget(QWidget):
    """
    The widget for an entry in the device list. 
    """
    connectRequested = pyqtSignal(str)  
    disconnectRequested = pyqtSignal(str) 
    editRequested = pyqtSignal(str)     
    macEditRequested = pyqtSignal(str)   
    forgetRequested = pyqtSignal(str)

    def __init__(self, mac: str, name: Optional[str] = None, status: str = "Disconnected", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mac = mac
        self.name = name or mac
        self.status = status
        self.setFixedHeight(70)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(12, 6, 12, 6)
        main_layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(svg_to_colored_pixmap("resources/icons/circuit-board.svg", "#EBEBEB", 40))
        main_layout.addWidget(self.icon_label)

        self.info_label = QLabel()
        self._render_info()
        self.info_label.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.info_label)
        main_layout.addStretch()

        self.right_box = QHBoxLayout()
        self.right_box.setContentsMargins(0, 0, 0, 0)
        self.right_box.setSpacing(8)

        # Disconnect button (hidden unless Connected)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setVisible(False)
        self.disconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disconnect_btn.setFixedHeight(26)
        self.disconnect_btn.setStyleSheet("QPushButton { padding: 3px 8px; }")
        self.disconnect_btn.clicked.connect(lambda: self.disconnectRequested.emit(self.mac))
        self.right_box.addWidget(self.disconnect_btn)

        # Existing edit dropdown
        self.edit_button = QToolButton()
        icon_pixmap = svg_to_colored_pixmap("resources/icons/edit.svg", "white", 30)
        self.edit_button.setIcon(QIcon(icon_pixmap))
        self.edit_button.setIconSize(QSize(24, 24))
        self.edit_button.setToolTip("Edit this device")
        self.edit_button.setAutoRaise(True)
        self.right_box.addWidget(self.edit_button)

        main_layout.addLayout(self.right_box)

        self.menu = QMenu(self)
        self.action_edit_name = QAction("Edit name", self)
        self.action_edit_mac = QAction("Edit MAC address", self)
        self.action_forget = QAction("Forget device", self)
        self.menu.addAction(self.action_edit_name)
        self.menu.addAction(self.action_edit_mac)
        self.menu.addSeparator()
        self.menu.addAction(self.action_forget)
        self.edit_button.setMenu(self.menu)
        self.edit_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.action_edit_name.triggered.connect(self._edit_name_dialog)
        self.action_edit_mac.triggered.connect(self._edit_mac_dialog)
        self.action_forget.triggered.connect(self._confirm_forget)

        self._hover = False

    # ---- Painting & hover ----
    def enterEvent(self, event):
        if self.isEnabled():
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isEnabled():
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = 5

        if not self.isEnabled():
            brush = QBrush(QColor("#1E1E1E"))
        elif self._hover:
            brush = QBrush(QColor("#2F2F2F"))
        else:
            brush = QBrush(QColor("#1E1E1E"))

        pen = QPen(QColor("#888888"))
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, radius, radius)
        super().paintEvent(event)

    # ---- Interaction ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.status == "Connected":
                return
            self.connectRequested.emit(self.mac)
        super().mousePressEvent(event)

    # ---- Helpers ----
    def _render_info(self):
        self.info_label.setText(
            f"""
            <div style="text-align:left;">
                <div style="font-weight:bold; font-size:14px;">{self.name}</div>
                <div style="color:gray; font-size:12px;">{self.status}</div>
            </div>
            """
        )

    def set_status(self, status: str):
        self.status = status
        self.disconnect_btn.setVisible(status == "Connected")
        self._render_info()

    def set_name(self, name: str):
        self.name = name
        self._render_info()

    def _edit_name_dialog(self):
        text, ok = QInputDialog.getText(self, "Edit Device Name", "Enter new name:", text=self.name)
        if ok and text.strip():
            self.set_name(text.strip())
            self.editRequested.emit(self.mac)

    def _edit_mac_dialog(self):
        dlg = EditMacDialog(self.mac, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            old_mac = self.mac
            self.mac = dlg.get_mac()
            self.macEditRequested.emit(old_mac)  # panel can remap references
            self._render_info()

    def _confirm_forget(self):
        reply = QMessageBox.question(
            self,
            "Forget Device",
            f"Are you sure you want to forget device:\n\n{self.name} ({self.mac})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.forgetRequested.emit(self.mac)


class AddDeviceWidget(QWidget):
    """
    The widget at the bottom of the device list to add a new device.
    """
    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(svg_to_colored_pixmap("resources/icons/plus.svg", "#EBEBEB", 24))
        layout.addWidget(self.icon_label)

        self.text_label = QLabel("Add new EPG device")
        self.text_label.setStyleSheet("""
            QLabel { font-size: 14px; font-weight: bold; color: #EBEBEB; }
            QLabel:disabled { color: #777777; }
        """)
        layout.addWidget(self.text_label)
        layout.addStretch()

        self._hover = False

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        color = "#EBEBEB" if enabled else "#777777"
        self.icon_label.setPixmap(svg_to_colored_pixmap("resources/icons/plus.svg", color, 24))

    def enterEvent(self, event):
        if self.isEnabled():
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isEnabled():
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        if not self.isEnabled():
            brush = QBrush(QColor("#1E1E1E"))
        elif self._hover:
            brush = QBrush(QColor("#2F2F2F"))
        else:
            brush = QBrush(QColor("#1E1E1E"))

        pen = QPen(QColor("#666666"))
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(rect, 5, 5)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class EditMacDialog(QDialog):
    """
    Shown when editing an existing device's MAC address.
    """
    def __init__(self, current_mac: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Edit MAC Address")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 10)

        dev_name = getattr(self.parent(), "name", "Device")
        self.label = QLabel(f"Enter new MAC address for \"{dev_name}\":")
        layout.addWidget(self.label)

        self.mac_input = QLineEdit(current_mac)
        self.mac_input.setInputMask("HH:HH:HH:HH:HH:HH;_") # H = hex; colons fixed; '_' as placeholder
        self.mac_input.setCursorPosition(0)
        layout.addWidget(self.mac_input)
        layout.addSpacing(10)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_mac(self) -> str:
        return self.mac_input.text().strip().upper()

    def accept(self):
        mac = self.mac_input.text().strip()
        if not is_valid_mac(mac):
            QMessageBox.warning(
                self, "Invalid MAC",
                "Please enter a valid MAC address (format: AA:BB:CC:DD:EE:FF)."
            )
            return
        super().accept()


class AddDeviceDialog(QDialog):
    """
    Shown when adding a new device to the device list.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Add New Device")

        layout = QFormLayout(self)
        self.mac_input = QLineEdit()
        self.mac_input.setInputMask("HH:HH:HH:HH:HH:HH;_")  # H = hex; colons fixed; '_' as placeholder
        self.mac_input.setCursorPosition(0)
        layout.addRow("MAC Address:", self.mac_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Unnamed Device")
        layout.addRow("Device Name:", self.name_input)

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
            return
        super().accept()

    def get_values(self):
        mac = self.mac_input.text().upper()
        name = self.name_input.text().strip() or "Unnamed Device"
        return mac, name


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

        self.bt_io: BluetoothIO = BluetoothIO()
        self.connected_address: Optional[str] = None
        self.pending_address: Optional[str] = None
        self.device_connected: bool = False

        # Wire BluetoothIO to live buffer
        def place_line_in_live_buffer(line: str):
            if __name__ == "__main__":
                return
            index, voltage = (int(x) for x in line.strip().split(','))
            self.parent().datawindow.buffer_data.append((index / 1e4, voltage / 1000))
            self.parent().datawindow.current_time = index / 1e4

        self.bt_io.lineReceived.connect(place_line_in_live_buffer)
        self.bt_io.connectedChanged.connect(self._on_bt_connected_changed)
        self.bt_io.reconnectingChanged.connect(self._on_bt_reconnecting_changed)
        self.bt_io.error.connect(self._on_bt_error)

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

        self.add_device_button = AddDeviceWidget()
        self.add_device_button.clicked.connect(self._add_device_dialog)
        device_section_layout.addWidget(self.add_device_button)

        layout.addWidget(self.device_section)
        self.setLayout(layout)

        # Load devices
        for rec in self.store.load():
            self._add_device_widget(rec.mac, rec.name, save=False)

    # ---- Device list helpers ----

    def _current_devices(self) -> List[DeviceRecord]:
        out: List[DeviceRecord] = []
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget):
                out.append(DeviceRecord(mac=w.mac, name=w.name))
        return out

    def _save_devices(self):
        self.store.save(self._current_devices())

    def _find_device_widget(self, mac: Optional[str]) -> Optional[DeviceWidget]:
        if not mac:
            return None
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget) and w.mac == mac:
                return w
        return None

    def _set_device_status(self, mac: Optional[str], status: str):
        w = self._find_device_widget(mac)
        if w:
            w.set_status(status)
            w.update()

    def _clear_other_devices_connected(self, keep_mac: Optional[str]):
        for i in range(self.device_layout.count()):
            w = self.device_layout.itemAt(i).widget()
            if isinstance(w, DeviceWidget) and w.mac != keep_mac and w.status == "Connected":
                w.set_status("Disconnected")

    # ---- UI slots ----

    def _add_device_widget(self, mac: str, name: str, save: bool = True):
        dev = DeviceWidget(mac=mac, name=name, status="Disconnected")
        dev.forgetRequested.connect(self._remove_device)
        dev.editRequested.connect(lambda _m: self._save_devices())
        dev.macEditRequested.connect(lambda _old: self._save_devices())
        dev.disconnectRequested.connect(lambda _m, w=dev: self._disconnect_device_clicked(w))
        dev.connectRequested.connect(lambda _m, w=dev: self._on_device_clicked(w))
        self.device_layout.addWidget(dev)
        if save:
            self._save_devices()

    def _add_device_dialog(self):
        dlg = AddDeviceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            mac, name = dlg.get_values()
            if mac:
                self._add_device_widget(mac, name)

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

    def _disconnect_device_clicked(self, device: DeviceWidget):
        if self.device_connected and self.connected_address == device.mac:
            self._set_device_status(device.mac, "Disconnecting...")
            self.bt_io.stop()

    # ---- BluetoothIO state reactions ----

    def _on_bt_connected_changed(self, connected: bool):
        self.device_connected = connected
        if connected:
            new_mac = self.pending_address or self.connected_address
            if new_mac and self.connected_address and self.connected_address != new_mac:
                self._set_device_status(self.connected_address, "Disconnected")
            self.connected_address = new_mac
            self.pending_address = None
            if self.connected_address:
                self._set_device_status(self.connected_address, "Connected")
                self._clear_other_devices_connected(self.connected_address)
        else:
            # If not switching, mark current as disconnected
            if not self.pending_address and self.connected_address:
                self._set_device_status(self.connected_address, "Disconnected")
                self.connected_address = None

    def _on_bt_reconnecting_changed(self, reconnecting: bool):
        target_mac = self.connected_address or self.pending_address
        if reconnecting:
            self._set_device_status(target_mac, "Reconnecting...")
        # final status will be set by connectedChanged

    def _on_bt_error(self, err: str):
        mac = self.connected_address or self.pending_address
        if mac:
            self._set_device_status(mac, "Error")

    def _on_bt_state_changed(self, has_adapter: bool, enabled: bool):
        if has_adapter and not enabled:
            try:
                self.bt_io.stop()
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
        if previous_mac:
            self._set_device_status(previous_mac, "Disconnecting...")

        self.bt_io.stop()
        if previous_mac:
            self._set_device_status(previous_mac, "Disconnected")

        self.connected_address = None
        self.device_connected = False

        self.pending_address = device.mac
        self._set_device_status(self.pending_address, "Connecting...")
        self.bt_io.start(device.mac)

        print(f"Connecting to device: Name={device.name}, MAC={device.mac}")

    def closeEvent(self, event):
        try:
            self.bt_io.stop()
        except Exception:
            pass
        try:
            self.bt_state.stop()
        except Exception:
            pass
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = DevicePanel()
    w.show()
    sys.exit(app.exec())