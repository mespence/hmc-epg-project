from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QComboBox, 
    QSlider, QLineEdit, QPushButton, QVBoxLayout, 
    QHBoxLayout, QGridLayout, QFrame, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot

from utils.ToggleSwitch import ToggleSwitch
import sys

class SliderPanel(QWidget):
    def __init__(self, parent: str = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Slider Panel")

        self.socket_client = self.parent().socket_client
        self._suppress = False  # whether slider signals are suppressed

        
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_label = QLabel("EPG Controls")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title_label)

        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr)

        grid = QGridLayout()
        grid.setHorizontalSpacing(0)

        # DC/AC Toggle Switch
        self.mode_toggle = ToggleSwitch("DC", "AC")
        grid.addWidget(self.mode_toggle, 0, 1)

        
        def sync_slider_and_entry(slider: QSlider, spinbox: QDoubleSpinBox, scale: float, precision: int):
            syncing = {"active": False}  # mutable container to persist across closures

            def slider_to_spinbox(val):
                if syncing["active"]:
                    return
                syncing["active"] = True
                spinbox.setValue(round(val * scale, precision))
                syncing["active"] = False

            def spinbox_to_slider():
                if syncing["active"]:
                    return
                syncing["active"] = True
                try:
                    val = int(round(spinbox.value() / scale))
                    val = max(slider.minimum(), min(val, slider.maximum()))
                    if slider.value() != val:
                        slider.setValue(val)
                except ValueError:
                    pass
                syncing["active"] = False

            slider.valueChanged.connect(slider_to_spinbox)
            spinbox.editingFinished.connect(spinbox_to_slider)
            spinbox.valueChanged.connect(spinbox_to_slider)

        def create_slider_row_widgets(label_text, unit=None, scale = 1, precision = 0):
            """
            Helper function to create a slider row with a label, slider, and text box, all synced.
            `scale` controls the ratio between the slider's ints and what is displayed in the text box
            (e.g. scale = 0.1 -> slider 33 = text 3.3 )
            """
            label = QLabel(label_text)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setFixedWidth(150)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            spinbox = QDoubleSpinBox()
            spinbox.setDecimals(precision)
            spinbox.setFixedWidth(80)

            unit_label = QLabel(unit) if unit else None
            if unit_label:
                unit_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

                # Prevent width changes when unit changes
                unit_label.setMinimumWidth(40)
                unit_label.setStyleSheet("padding-left: 6px;")

            sync_slider_and_entry(slider, spinbox, scale, precision)    

            return label, slider, spinbox, unit_label

        self.slider_widgets_map = {}

        self.dds_offset_widget = create_slider_row_widgets("Applied Voltage", "mV", scale = 10, precision = 0)
        self.dds_slider = self.dds_offset_widget[1]
        self.dds_slider.setRange(-330, 330)
        dds_spinbox = self.dds_offset_widget[2]
        dds_spinbox.setRange(-3300, 3300)
        dds_spinbox.setSingleStep(10)
        self.slider_widgets_map["ddso"] = self.dds_offset_widget

        self.ddsa_amplitude_widget = create_slider_row_widgets("Applied Voltage", "mV RMS", scale = 1, precision=0)
        self.ddsa_slider = self.ddsa_amplitude_widget[1]
        self.ddsa_slider.setRange(7, 1000)
        ddsa_spinbox = self.ddsa_amplitude_widget[2]
        ddsa_spinbox.setRange(7, 1000)
        ddsa_spinbox.setSingleStep(1)
        self.slider_widgets_map["ddsa"] = self.ddsa_amplitude_widget

        # Input Resistance
        grid.addWidget(QLabel("Input Resistance"), 3, 0)
        self.input_resistance = QComboBox()
        self.input_resistance.addItems(["10\u2075 (100K)", "10\u2076 (1M)", "10\u2077 (10M)", "10\u2078 (100M)", "10\u2079 (1G)", "10\u00b9\u2070 (10G)", "Loopback"])
        grid.addWidget(self.input_resistance, 3, 1)
        grid.addWidget(QLabel("Ω"), 3, 2)

        self.sca_widgets = create_slider_row_widgets("Gain", "\u2715")
        self.sca_slider = self.sca_widgets[1]
        self.sca_slider.setRange(2, 7000)
        sca_spinbox = self.sca_widgets[2]
        sca_spinbox.setRange(2,7000)

        self.slider_widgets_map["sca"] = self.sca_widgets

        self.sco_widgets = create_slider_row_widgets("Offset", "V", scale = 0.01, precision = 1)
        self.sco_slider = self.sco_widgets[1]
        self.sco_slider.setRange(-330, 330)
        sco_spinbox = self.sco_widgets[2]
        sco_spinbox.setRange(-3.3, 3.3)
        sco_spinbox.setSingleStep(0.1)
        self.slider_widgets_map["sco"] = self.sco_widgets

        grid_row = 1
        for value in self.slider_widgets_map.values():
            label, slider, entry, unit_label = value
            grid.addWidget(label, grid_row, 0)
            grid.addWidget(slider, grid_row, 1)
            grid.addWidget(entry, grid_row, 2)
            if unit_label:
                grid.addWidget(unit_label, grid_row, 3)
            grid_row += 1
            if grid_row == 3:
                grid_row += 1 # skip row 3, for input resistance

        layout.addLayout(grid)

        hr = QFrame()
        hr.setFrameShape(QFrame.Shape.HLine)
        hr.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(hr)

        recording_label = QLabel("Recording")
        recording_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        recording_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(recording_label)

        # Buttons
        #self.on_button = QPushButton("ON", self)
        self.start_button = QPushButton("START", self)

        self.pause_button = QPushButton("PAUSE", self)
        self.pause_button.setCheckable(True)
        self.pause_button.setEnabled(False)

        self.stop_button = QPushButton("STOP", self)
        self.stop_button.setEnabled(False)

        # self.off_button  = QPushButton("OFF", self)
        # self.cancel_button  = QPushButton("Cancel", self)
        # self.revert_default_button  = QPushButton("Revert to Defaults", self)
        # self.apply_button  = QPushButton("Apply", self)
        # self.apply_close_button  = QPushButton("Apply \u0026 Close", self)

        button_layout1 = QHBoxLayout()
        #button_layout1.addWidget(self.on_button)
        button_layout1.addWidget(self.start_button)
        button_layout1.addWidget(self.pause_button)
        button_layout1.addWidget(self.stop_button)
        #button_layout1.addWidget(self.off_button)
        layout.addLayout(button_layout1)

        # button_layout2 = QGridLayout()
        # button_layout2.addWidget(self.cancel_button, 0, 0)
        # button_layout2.addWidget(self.revert_default_button, 0, 1)
        # button_layout2.addWidget(self.apply_button, 1, 0)
        # button_layout2.addWidget(self.apply_close_button, 1, 1)

        #layout.addLayout(button_layout2)

        layout.addStretch(1)
        self.setLayout(layout)

        if self.mode_toggle.isChecked():  # AC mode
            for widget in self.slider_widgets_map["ddsa"]:
                widget.setVisible(True)
            for widget in self.slider_widgets_map["ddso"]:
                widget.setVisible(False)
        else:  # DC mode
            for widget in self.slider_widgets_map["ddso"]:
                widget.setVisible(True)
            for widget in self.slider_widgets_map["ddsa"]:
                widget.setVisible(False)

        self.controls = {
            "modeToggle": self.mode_toggle,
            "sca": self.sca_slider,
            "sco": self.sco_slider,
            "inputResistance": self.input_resistance,
            "ddso": self.dds_slider,
            "ddsa": self.ddsa_slider,
            #"on": self.on_button,
            "start": self.start_button,
            "pause": self.pause_button,
            "stop": self.stop_button,
            # "off": self.off_button,
            # "cancel": self.cancel_button,
            # "revert": self.revert_default_button,
            # "apply": self.apply_button,
            # "applyClose": self.apply_close_button
        }

        self.resistance_map = {
            "10\u2075 (100K)": "100K",
            "10\u2076 (1M)": "1M",
            "10\u2077 (10M)": "10M",
            "10\u2078 (100M)": "100M",
            "10\u2079 (1G)": "1G",
            "10\u00b9\u2070 (10G)": "10G",
            "Loopback": "Loopback"
        }

        # Connect all controls except AC/DC toggle
        for label, item in self.controls.items():
            if isinstance(item, ToggleSwitch):
                item.toggled.connect(self.on_mode_change)
            if isinstance(item, QSlider):
                item.valueChanged.connect(lambda val, l=label: self.send_control_update(l, val))
            elif isinstance(item, QPushButton):
                if item == self.start_button:
                    item.clicked.connect(self.parent().start_recording)
                elif item == self.pause_button:
                    item.clicked.connect(self.toggle_pause_resume)
                elif item == self.stop_button:
                    item.clicked.connect(self.parent().stop_recording)
            elif isinstance(item, QComboBox):
                item.currentTextChanged.connect(lambda text, l=label: self.send_control_update(l, text))

                
    def on_mode_change(self, value: int):
        selected_mode = "DC" if value == 0 else "AC"
        
        always_visible = ["sca", "sco"]

        for name in always_visible:
            for widget in self.slider_widgets_map[name]:
                widget.setVisible(True)

        if selected_mode == "DC":
            for widget in self.slider_widgets_map["ddso"]:
                widget.setVisible(True)
            for widget in self.slider_widgets_map["ddsa"]:
                widget.setVisible(False)
            self.ddsa_slider.setValue(217)
            #self.send_control_update("ddsa", 1)

            self.send_control_update("ddsa", "217.282")
            QTimer.singleShot(300, lambda: self.send_control_update(
                "excitationFrequency", "0"))

        elif selected_mode == "AC":
            for widget in self.slider_widgets_map["ddsa"]:
                widget.setVisible(True)
            for widget in self.slider_widgets_map["ddso"]:
                widget.setVisible(False)
            self.dds_slider.setValue(-34)  # actually -0.341

            self.send_control_update("ddso", "34") # positive 34 because it's not going to be inverted by DDS amplifier since total offset will go to 0
            QTimer.singleShot(300, lambda: self.send_control_update(
                "excitationFrequency", "1000"))

        QTimer.singleShot(0, lambda: setattr(self, "_suppress", False))


    def toggle_pause_resume(self, checked: bool):
        if checked:
            self.pause_button.setText("RESUME")
            self.parent().pause_recording()
        else:
            self.pause_button.setText("PAUSE")
            self.parent().resume_recording()

    def send_control_update(self, name, value):
        if self._suppress:
            return
        
        if name == "inputResistance":
            value = self.resistance_map[value]

        if name == "sco":
            value = value // 10

        if name == "ddsa": # send to d0 w/ conversion formula
            name = "d0"
            value = int((float(value) - 1075.51) / -4.207)

        if name =="ddso":
            value = -1*int(value)

        self.socket_client.send({
            "source": self.socket_client.client_id,
            "type": "control",
            "name": name,
            "value": value
        })
            
    @pyqtSlot(str, object, str)
    def set_control_value(self, name, value, source = None):
        if source == self.socket_client.client_id:
            return
        
        
        if name == "excitationFrequency":
            name = "modeToggle"
            if value == "1":
                return # ignore the debug setting
            value = 1 if value == "1000" else 0 # 0/1 for toggle index
        
        widget = self.controls.get(name)
        if widget is None:
            print(f"[CS] Unknown control name: {name}")
            return
          
        self._suppress = True



        if isinstance(widget, ToggleSwitch):
            if widget.isChecked() != int(value):
                widget.toggle_state()
        elif isinstance(widget, QSlider):
            value = int(value)
            if widget.value() != value:
                widget.setValue(value)
        if isinstance(widget, QComboBox): # input resistance
            value = next((k for k, v in self.resistance_map.items() if v == value), None)
            #key_list = list(self.resistance_map.keys())
            #value = key_list[list(self.resistance_map.values()).index(value)]
            index = widget.findText(value)
            if index != -1 and widget.currentIndex() != index:
                widget.setCurrentIndex(index)

        QTimer.singleShot(0, lambda: setattr(self, "_suppress", False))

    @pyqtSlot(dict)        
    def set_all_controls(self, full_state: dict):
        """
        Sets all controls to the values given by a full control state dictionary.
        """
        if "mode" in full_state:
            self.set_control_value("mode", full_state["mode"])

        for name, value in full_state.items():
            self.set_control_value(name, value)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SliderPanel()
    window.show()
    sys.exit(app.exec())
