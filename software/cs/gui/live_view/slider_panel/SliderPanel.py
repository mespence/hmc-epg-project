import sys

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
)
from PyQt6.QtCore import Qt

from epg_board.EPGStateManager import get_spec, get_state
from live_view.slider_panel.DefaultSliderPanel import DefaultSliderPanel
from live_view.slider_panel.EngineeringSliderPanel import EngineeringSliderPanel
from utils.ToggleSwitch import ToggleSwitch


class SliderPanel(QWidget):
    def __init__(self, parent: str = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Slider Panel")

        self._spec = get_spec()
        self.epg_settings = get_state(parent=self)

        
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)


        self.view_stack = QStackedWidget()
        layout.addWidget(self.view_stack)

        self.engineering_view = EngineeringSliderPanel(self._spec, self.epg_settings, parent=self)
        self.default_view = DefaultSliderPanel(self._spec, self.epg_settings, parent=self)
        

        self.view_stack.addWidget(self.default_view)
        self.view_stack.addWidget(self.engineering_view)



        # hr = QFrame()
        # hr.setFrameShape(QFrame.Shape.HLine)
        # hr.setFrameShadow(QFrame.Shadow.Sunken)
        # layout.addWidget(hr)

        # recording_label = QLabel("Recording")
        # recording_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        # recording_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # layout.addWidget(recording_label)

        # # Buttons
        # #self.on_button = QPushButton("ON", self)
        # self.start_button = QPushButton("START", self)

        # self.pause_button = QPushButton("PAUSE", self)
        # self.pause_button.setCheckable(True)
        # self.pause_button.setEnabled(False)

        # self.stop_button = QPushButton("STOP", self)
        # self.stop_button.setEnabled(False)

        # # self.off_button  = QPushButton("OFF", self)
        # # self.cancel_button  = QPushButton("Cancel", self)
        # # self.revert_default_button  = QPushButton("Revert to Defaults", self)
        # # self.apply_button  = QPushButton("Apply", self)
        # # self.apply_close_button  = QPushButton("Apply \u0026 Close", self)

        # recording_layout = QHBoxLayout()
        # #recording_layout.addWidget(self.on_button)
        # recording_layout.addWidget(self.start_button)
        # recording_layout.addWidget(self.pause_button)
        # recording_layout.addWidget(self.stop_button)
        # #recording_layout.addWidget(self.off_button)
        # layout.addLayout(recording_layout)

        layout.addStretch()

        bottom_buttons = QHBoxLayout()
        bottom_buttons.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.engr_view_toggle_switch = ToggleSwitch("", "Debug View")
        self.engr_view_toggle_switch.toggled.connect(self.view_stack.setCurrentIndex)
        # self.engr_view_toggle_switch.setSizePolicy(
        #     QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        # )
        bottom_buttons.addWidget(self.engr_view_toggle_switch)

        layout.addLayout(bottom_buttons)



        


        # button_layout2 = QGridLayout()
        # button_layout2.addWidget(self.cancel_button, 0, 0)
        # button_layout2.addWidget(self.revert_default_button, 0, 1)
        # button_layout2.addWidget(self.apply_button, 1, 0)
        # button_layout2.addWidget(self.apply_close_button, 1, 1)

        #layout.addLayout(button_layout2)

        
        self.setLayout(layout)

        # # Connect all controls except AC/DC toggl


    #def change_panel_type(self, toggled: int):


    def toggle_pause_resume(self, checked: bool):
        if checked:
            self.pause_button.setText("RESUME")
            self.parent().pause_recording()
        else:
            self.pause_button.setText("PAUSE")
            self.parent().resume_recording()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SliderPanel()
    window.show()
    sys.exit(app.exec())
