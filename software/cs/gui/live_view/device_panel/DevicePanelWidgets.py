import os
import re
import sys
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPainter, QBrush, QColor, QPen, QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QMessageBox,
    QInputDialog, QMenu, QToolButton
)


# -------- Project imports --------
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from utils.SVGIcon import svg_to_colored_pixmap


MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
def is_valid_mac(addr: str) -> bool:
    return bool(MAC_RE.match(addr.strip()))


class DeviceListEntry(QWidget):
    " "
    def __init__(self, parent = None):
        super().__init__(parent)
   
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

class DeviceWidget(DeviceListEntry):
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


class AddDeviceWidget(DeviceListEntry):
    """
    The widget at the bottom of the device list to add a new device.
    """

    deviceAdded = pyqtSignal(str, str) # (mac, name)

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

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            dlg = AddDeviceDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                mac, name = dlg.get_values()
                if mac:
                    self.deviceAdded.emit(mac, name)
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
                "Please enter a valid MAC address (format: A1:B2:C3:D4:E5:F6)."
            )
            return
        super().accept()

    def get_values(self):
        mac = self.mac_input.text().upper()
        name = self.name_input.text().strip() or "Unnamed Device"
        return mac, name
