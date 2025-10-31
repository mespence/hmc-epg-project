from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QFrame, QLabel, QToolButton, QComboBox, QPushButton, QSizePolicy,
    QSpinBox, QCheckBox, QColorDialog, QLineEdit, QMessageBox, 
    QFileDialog, QGridLayout, QGroupBox
)
from PyQt6.QtGui import (
    QColor, QIcon, QMouseEvent,
)
from PyQt6.QtCore import Qt, QSize, QSettings

import os
import json
from settings import settings



class AppearanceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(15)

        # === Plot Theme ComboBox ===
        theme_label = QLabel("Plot Theme")
        theme_label.setStyleSheet("font-weight: bold;")

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.setCurrentText(settings.get("plot_theme")["NAME"].title())
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_row.addSpacing(20)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()

        layout.addWidget(theme_label)
        layout.addLayout(theme_row)

        # === Data Line Appearance ===
        data_line_label = QLabel("Data Line")
        data_line_label.setStyleSheet("font-weight: bold;")

        # Color Picker
        color_label = QLabel("Color:")
        color_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        self.data_line_color_button = QPushButton()
        self.data_line_color_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.data_line_color_button.clicked.connect(self.pick_line_color)

        self.set_data_line_color_button(settings.get("data_line_color"))

        # Width Spinner
        width_label = QLabel("Width:")
        width_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        self.width_spinbox = QSpinBox()
        self.width_spinbox.setRange(1, 5)
        self.width_spinbox.setValue(settings.get("data_line_width"))
        self.width_spinbox.setSuffix(" px")
        self.width_spinbox.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.width_spinbox.valueChanged.connect(self.on_width_changed)

        # Horizontal layout with spacing
        line_appearance_row = QHBoxLayout()
        line_appearance_row.setSpacing(10)

        line_appearance_row.addSpacing(20)
        line_appearance_row.addWidget(color_label)
        line_appearance_row.addWidget(self.data_line_color_button)
        line_appearance_row.addSpacing(20)
        line_appearance_row.addWidget(width_label)
        line_appearance_row.addWidget(self.width_spinbox)
        line_appearance_row.addStretch()

        layout.addWidget(data_line_label)
        layout.addLayout(line_appearance_row)

        # === Boolean Toggles ===
        toggles_label = QLabel("Display Options")
        toggles_label.setStyleSheet("font-weight: bold;")

        layout.addWidget(toggles_label)

        self.checkboxes = {}
        bool_settings = {
            "Show Horizontal Grid Lines": "show_h_grid",
            "Show Vertical Grid Lines": "show_v_grid",
            "Show Waveform Labels": "show_labels",
            "Show Waveform Durations": "show_durations",
            "Show Comments": "show_comments"
        }
        for label_text, attr in bool_settings.items():
            checkbox = QCheckBox(label_text)
            setattr(self, f"{attr}_checkbox", checkbox)
            checkbox.setChecked(settings.get(attr))
            self.checkboxes[attr] = checkbox
    
            row = QHBoxLayout()
            row.addSpacing(20)
            row.addWidget(checkbox)
            row.addStretch()
            layout.addLayout(row)

        self.show_labels_checkbox.toggled.connect(lambda checked: self.show_durations_checkbox.setEnabled(checked))
        self.show_durations_checkbox.setEnabled(self.show_labels_checkbox.isChecked())



        

        # Horizontal Rule
        hrule = QFrame()
        hrule.setFrameShape(QFrame.Shape.HLine)
        hrule.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hrule)


        # === Waveform Labels ===
        waveform_label = QLabel("Waveform Labels")
        waveform_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(waveform_label)

        # Grid layout 
        label_grid = QGridLayout()
        label_grid.setHorizontalSpacing(20)
        label_grid.setVerticalSpacing(10)

        # --- Label selection and delete button ---
        select_row = QHBoxLayout()
        select_row.setSpacing(10)

        select_label = QLabel("Select Waveform:")
        self.label_selector = QComboBox()
        self.label_selector.setFixedWidth(60)
        self.label_selector.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.label_selector.addItems(sorted(settings.get("label_colors").keys()))
        self.label_selector.currentTextChanged.connect(self.load_label_info)

        select_row.addWidget(select_label)
        select_row.addWidget(self.label_selector)
        select_row.addStretch()

        layout.addLayout(select_row)
    

        # === Grid for rename, color, delete (indented) ===
        label_layout = QVBoxLayout()
        label_layout.setSpacing(10)

        # Row 0: Rename
        name_layout = QHBoxLayout()
        name_label = QLabel("Waveform Name:")
        self.name_edit = QLineEdit()
        self.name_edit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.name_edit.textChanged.connect(self.update_rename_button_state)

        self.rename_button = QPushButton("Rename")
        self.rename_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.rename_button.clicked.connect(self.rename_label)
        

        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        name_layout.addWidget(self.rename_button)
        name_layout.addStretch()

        label_layout.addLayout(name_layout)

        # Row 1: Color
        color_layout = QHBoxLayout()
        label_color_label = QLabel("Color:")
        self.label_color_button = QPushButton()
        self.label_color_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.label_color_button.clicked.connect(self.pick_label_color)

        self.label_color_wrapper = QFrame()
        self.label_color_wrapper.setObjectName("LabelColorWrapper")
        self.update_label_color_wrapper_style()

        wrapper_layout = QHBoxLayout(self.label_color_wrapper)
        wrapper_layout.setContentsMargins(10, 10, 10, 10)
        wrapper_layout.addWidget(self.label_color_button)

        color_layout.addWidget(label_color_label)
        color_layout.addWidget(self.label_color_wrapper)
        color_layout.addStretch()

        label_layout.addLayout(color_layout)

        # # Row 2: Delete button
        # self.delete_button = QPushButton("Delete All Instances of Waveform")
        # self.delete_button.setStyleSheet("QPushButton {padding: 3px 12px;}")
        # self.delete_button.setContentsMargins(0, 0, 0, 0)
        # self.delete_button.setFixedWidth(200)
        # self.delete_button.clicked.connect(self.delete_label)

        # label_layout.addWidget(self.delete_button, alignment= Qt.AlignmentFlag.AlignLeft)

        self.label_edit_panel = QWidget()
        self.label_edit_panel.setLayout(label_layout)

        group = QGroupBox()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #888;
                border-radius: 6px;
                margin-top: 6px;
                background-color: transparent;
            }
        """)

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.addWidget(self.label_edit_panel)
        
        # Wrap the grid in an HBox with indent
        grid_wrapper = QHBoxLayout()
        grid_wrapper.addSpacing(40)
        grid_wrapper.addWidget(group)
        grid_wrapper.addStretch()

        layout.addLayout(grid_wrapper)
        layout.addStretch()
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Static interactable widgets
        self.apply_interactive_cursor(self.theme_combo)
        self.apply_interactive_cursor(self.data_line_color_button)
        self.apply_interactive_cursor(self.width_spinbox)
        self.apply_interactive_cursor(self.rename_button)
        #self.apply_interactive_cursor(self.delete_button)
        self.apply_interactive_cursor(self.label_selector)
        self.apply_interactive_cursor(self.label_color_button)

        for checkbox in self.checkboxes.values():
            self.apply_interactive_cursor(checkbox)

        self.rename_button.installEventFilter(self)
        self.load_label_info(self.label_selector.currentText())

        # === Connect Setting Signals ===
        for attr, checkbox in self.checkboxes.items():
            checkbox.toggled.connect(lambda state, a=attr: self.on_checkbox_toggled(a, state))

    def sync_ui_from_settings(self):
        self.theme_combo.blockSignals(True)
        self.width_spinbox.blockSignals(True)

        self.theme_combo.setCurrentText(settings.get("plot_theme")["NAME"].title())
        self.set_data_line_color_button(settings.get("data_line_color"))
        self.width_spinbox.setValue(settings.get("data_line_width"))

        self.theme_combo.blockSignals(False)
        self.width_spinbox.blockSignals(False)


        for attr, checkbox in self.checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(settings.get(attr))
            checkbox.blockSignals(False)

        # Update label selector
        self.label_selector.blockSignals(True)
        self.label_selector.clear()
        self.label_selector.addItems(sorted(settings.get("label_colors").keys()))
        if settings.get("label_colors"):
            self.label_selector.setCurrentIndex(0)
            self.load_label_info(self.label_selector.currentText())
        else:
            self.name_edit.clear()
            self.label_color_button.setText("")
            self.label_color_button.setStyleSheet("")
        self.label_selector.blockSignals(False)


    def set_data_line_color_button(self, color: QColor):
        text_color = self.get_contrasting_text_color(color)
        self.data_line_color_button.setText(color.name().upper())
        self.data_line_color_button.setStyleSheet(
            f"background-color: {color.name()}; color: {text_color};"
        )

    def on_theme_changed(self):
        new_theme_text = self.theme_combo.currentText()
        new_theme = settings.PLOT_LIGHT if new_theme_text == "Light" else settings.PLOT_DARK
        settings.set("plot_theme", new_theme)
        
        self.update_label_color_wrapper_style()
        self.refresh_label_color_preview()

    def on_width_changed(self, value):
        settings.set("data_line_width", value)

    def on_checkbox_toggled(self, key, value):
        settings.set(key, value)

    def on_label_rename(self):
        old_label = self.label_name_edit.text().strip()
        new_label = self.rename_edit.text().strip()
        if old_label and new_label and old_label != new_label:
            self.parent().parent().rename_label_color(old_label, new_label)
    

    def load_label_info(self, label: str):
        visible = bool(label and label in settings.get("label_colors"))
        self.label_edit_panel.setVisible(visible)
        if not visible:
            return

        self.label_color_wrapper.setVisible(True)
        self.name_edit.setVisible(True)
        self.rename_button.setVisible(True)
        #self.delete_button.setVisible(True)
        self.name_edit.setText(label)
        color = QColor(settings.get("label_colors")[label][settings.get("plot_theme")["NAME"]])
        self.label_color_button.setText(color.name().upper())
        text_color = self.get_contrasting_text_color(color)
        border_color = settings.get("plot_theme")["TRANSITION_LINE_COLOR"]
        self.label_color_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color.name()};
                color: {text_color};
                border: 2px solid {border_color};
                border-radius: 4px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{
                background-color: #8C8C8C;
                color: #FFFFFF;
            }}
            QPushButton:pressed {{
                background-color: #1E1E1E;
                color: #FFFFFF;
            }}
        """)
        self.update_rename_button_state()

    def update_label_color_wrapper_style(self):
        theme_bg = settings.get("plot_theme")["BACKGROUND"]
        theme_fg = settings.get("plot_theme")["FOREGROUND"]
        self.label_color_wrapper.setStyleSheet(f"""
            QFrame#LabelColorWrapper {{
                background-color: {theme_bg};
                border: 1px solid {theme_fg};
                border-radius: 6px;
                padding: 6px;
            }}
        """)

    def pick_label_color(self):
        label = self.label_selector.currentText()
        current_color = settings.get_label_color(label)
        new_color = QColorDialog.getColor(current_color, self)

        if new_color.isValid():
            settings.set_label_color(label, new_color)
            self.load_label_info(label)
                

    def pick_line_color(self):
        color = QColorDialog.getColor(initial=settings.get("data_line_color"), parent=self)
        if color.isValid():
            self.set_data_line_color_button(color)
            settings.set("data_line_color", color)

    def rename_label(self):
        old_name = self.label_selector.currentText()
        new_name = self.name_edit.text().strip().upper()

        if not new_name or new_name == old_name:
            return

        if new_name in settings.get("label_colors"):
            QMessageBox.warning(self, "Rename Failed", "Label name already exists.")
            return

        reply = QMessageBox.question(
            self,
            "Rename Waveform",
            f"Are you sure you want to change waveform label '{old_name}' to '{new_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.No:
            return

        settings.rename_label(old_name, new_name)
        self.parent().parent().parent().rename_label_areas(old_name, new_name)

        # UI update
        self.label_selector.blockSignals(True)
        self.label_selector.clear()
        self.label_selector.addItems(sorted(settings.get("label_colors").keys()))
        self.label_selector.setCurrentText(new_name)
        self.label_selector.blockSignals(False)
        self.load_label_info(new_name)

    def update_rename_button_state(self):
        current_name = self.label_selector.currentText().strip().upper()
        typed_name = self.name_edit.text().strip().upper()
        self.rename_button.setEnabled(bool(typed_name and typed_name != current_name))

    def delete_label(self):
        label = self.label_selector.currentText()
        reply = QMessageBox.question(
            self, "Delete Waveform",
            f"Are you sure you want to delete all instances of waveform '{label}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            settings.delete_label(label)
            self.label_selector.clear()
            self.label_selector.addItems(sorted(settings.get("label_colors").keys()))
            if settings.get("label_colors"):
                self.label_selector.setCurrentIndex(0)
                self.load_label_info(self.label_selector.currentText())
            else:
                self.name_edit.clear()
                self.label_color_button.setText("")
                self.label_color_button.setStyleSheet("")

    def refresh_label_color_preview(self):
        current_label = self.label_selector.currentText()
        self.load_label_info(current_label)

    def get_contrasting_text_color(self, bg_color: QColor) -> str:
        r, g, b = bg_color.red(), bg_color.green(), bg_color.blue()
        brightness = (0.299 * r + 0.587 * g + 0.114 * b)
        return "#000000" if brightness > 186 else "#FFFFFF"
    
    def apply_interactive_cursor(self, widget):
        widget.setCursor(Qt.CursorShape.PointingHandCursor)

    def eventFilter(self, obj, event):
        if obj == self.rename_button:
            if event.type() == event.Type.Enter:
                if not self.rename_button.isEnabled():
                    self.rename_button.setCursor(Qt.CursorShape.ArrowCursor)
                else:
                    self.rename_button.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)

class FolderRow(QWidget):
    """
    Helper widget, one horizontal row: label, path display, folder button
    """
    def __init__(self, label_text: str, setting_attr: str, parent=None):
        super().__init__(parent)
        self.setting_attr = setting_attr

        # --- left label ---
        label = QLabel(label_text + ":")
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # --- center: read‑only path ---
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setText(str(settings.get(setting_attr)))
        self.path_edit.setMinimumWidth(400)
        self.path_edit.setStyleSheet("""
            QLineEdit {
                background-color: #1f1f1f;
                color: #dcdcdc;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
            }
        """)

        # --- right: browse button ---
        browse_btn = QPushButton()
        browse_btn.setIcon(QIcon.fromTheme("folder"))  # uses system theme icon
        browse_btn.setFixedSize(QSize(32, 32))
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        browse_btn.clicked.connect(self.pick_directory)

        # lay out row
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(12)
        row.addWidget(label)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_btn)

    def pick_directory(self):
        start_dir = settings.get(self.setting_attr)
        path = QFileDialog.getExistingDirectory(
            self, "Select Folder", start_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        if path:
            settings.set(self.setting_attr, path)
            self.path_edit.setText(path)

class EPGSettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 24, 32, 24)

        header = QLabel("Folders")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        # --- rows ---
        self.rows = []
        self.rows.append(FolderRow("Default Directory", "default_recording_directory", self))
        self.rows.append(FolderRow("Backup Directory", "backup_recording_directory", self))

        for r in self.rows:
            layout.addWidget(r)

        layout.addStretch()

    def sync_ui_from_settings(self):
        for row in self.rows:
            new_path = settings.get(row.setting_attr)
            row.path_edit.setText(new_path)

class SidebarButton(QToolButton):
    def __init__(self, text: str, index: int, icon_path: str = None, parent=None):
        super().__init__(parent)
        self.index = index
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if icon_path:
            self.setIcon(QIcon(icon_path))
            self.setIconSize(QSize(32, 32))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self.setStyleSheet("""
            QToolButton {
                background-color: #2b2b2b;
                color: white;
                padding: 12px 32px;
                text-align: left;
                border: none;
                font-size: 10pt;
                font-weight: normal;
            }
            QToolButton:hover {
                background-color: rgba(32, 147, 254, 0.15);
            }
            QToolButton:pressed {
                background-color: rgba(32, 147, 254, 0.35);
            }
            QToolButton:checked {
                background-color: rgba(32, 147, 254, 0.25);
                border-left: 4px solid #2093FE;
                padding-left: 28px;
                font-weight: 600;
            }
        """)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SCIDO Settings")
        self.setMinimumSize(700, 700)

        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint  # optionally include maximize if desired
        )

        settings.settingChanged.connect(self.sync_all_tabs)

        # === Main Layout ===
        central = QWidget()
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === Sidebar ===
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setStyleSheet("background-color: #2b2b2b;")
        self.sidebar_layout = QVBoxLayout(self.sidebar_frame)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(0)
            
        button_info = [
            ("Appearance", None),  # Use valid icon paths or leave empty
            ("Recording Settings", None)
        ]

        self.buttons = []
        for index, (label, icon) in enumerate(button_info):
            btn = SidebarButton(label, index, icon)
            btn.clicked.connect(lambda _, i=index: self.switch_tab(i))
            self.sidebar_layout.addWidget(btn)
            self.buttons.append(btn)
        self.buttons[0].setChecked(True)

        self.sidebar_layout.addStretch()

        # === Stacked Content Area ===
        self.stack = QStackedWidget(parent=self)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    
        self.appearance_tab = AppearanceTab(self.stack)
        self.stack.addWidget(self.appearance_tab)
        self.epg_tab = EPGSettingsTab()
        self.stack.addWidget(self.epg_tab)

         # === Final Layout Assembly ===
        main_layout.addWidget(self.sidebar_frame, stretch=0)
        main_layout.addWidget(self.stack, stretch=1)

            # === Footer ===
        footer = QFrame()
        footer.setStyleSheet("background-color: #2c2c2c; border-top: 1px solid palette(light);")
        footer.setFixedHeight(60)

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 10, 20, 10)
        footer_layout.setSpacing(10)

        # Left: Reset Button
        reset_button = QPushButton("Reset Settings")
        reset_button.setStyleSheet("""
            QPushButton {
                background-color: #444;
                color: white;
                padding: 8px 20px;
                border: none;
                font-weight: normal;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        reset_button.clicked.connect(self.reset_settings)
        reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        footer_layout.addWidget(reset_button)
        footer_layout.addStretch()

        # Right: OK Button
        ok_button = QPushButton("OK")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.setStyleSheet("""
            QPushButton {
                background-color: #0078D7;
                color: white;
                padding: 8px 32px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2093FE;
            }
        """)
        ok_button.clicked.connect(self.close)
        footer_layout.addWidget(ok_button)

        # Wrap main + footer in a vertical layout
        wrapper = QVBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        wrapper.addLayout(main_layout, stretch=1)
        wrapper.addWidget(footer)

        central.setLayout(wrapper)
        self.setCentralWidget(central)

        self.sync_all_tabs()

    def rename_label_areas(self, old_label: str, new_label: str):
        datawindow = self.parent().label_tab.datawindow

        for label_area in datawindow.labels:
            if label_area.label == old_label:
                label_area.label = new_label
                label_area.refreshColor()
                label_area.update_label_area()

        # Trigger label color refresh + collision recheck
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: [
            label.update_label_area() for label in datawindow.labels
        ])

    def sync_all_tabs(self):
        self.appearance_tab.sync_ui_from_settings()
        self.epg_tab.sync_ui_from_settings()

    def switch_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.buttons):
            btn.setChecked(i == index)

    def load_settings(self, init = False):
        settings.load()
        if not init:
            self.appearance_tab.sync_ui_from_settings()
            self.epg_tab.sync_ui_from_settings()
        
    def reset_settings(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Reset to Default Settings")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText("<b>Are you sure you want to reset all settings?</b>")
        msg_box.setInformativeText("This will restore all waveform colors, display preferences, and default file paths.")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)

        reply = msg_box.exec()

        if reply == QMessageBox.StandardButton.Yes:
            settings.reset()
            self.appearance_tab.refresh_label_color_preview()
    def closeEvent(self, event):
        settings.save_all()
        super().closeEvent(event)