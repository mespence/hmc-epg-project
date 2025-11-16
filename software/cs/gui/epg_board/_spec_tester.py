######################################################################
# Debugging tool used to test the spec.
# Confirms whether controls have the right ranges/options and whether
# the relations/mappings within/between the views are correct.
######################################################################

import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGroupBox
)

if __name__ == "__main__":
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

from epg_board.SpecLoader import load_spec, EPGSettingsSpec
from epg_board.EPGControlState import EPGControlState
from epg_board.RelationsEngine import RelationsEngine

from live_view.slider_panel.DefaultSliderPanel import DefaultSliderPanel
from live_view.EPGSettingsMonitorWindow import EPGSettingsMonitorWindow
from live_view.slider_panel.EngineeringSliderPanel import EngineeringSliderPanel


class SpecTester(QWidget):
    def __init__(self, spec: EPGSettingsSpec, state: EPGControlState):
        super().__init__()
        self.setWindowTitle("EPG Spec Tester")

        # Hook relations engine once to the shared ground-truth state
        self._engine = RelationsEngine(spec, state, parent=self)

        root = QHBoxLayout(self)

        box_default = QGroupBox("Default")
        box_truth   = QGroupBox("Ground-Truth")
        box_eng     = QGroupBox("Engineering")

        lay_default = QVBoxLayout(box_default)
        lay_truth   = QVBoxLayout(box_truth)
        lay_eng     = QVBoxLayout(box_eng)

        self.default_panel = DefaultSliderPanel(spec, state, parent=self)
        lay_default.addWidget(self.default_panel)

        self.truth_panel = EPGSettingsMonitorWindow(state, parent=self)
        self.truth_panel.setWindowFlags(Qt.WindowType.Widget)
        lay_truth.addWidget(self.truth_panel)

        self.eng_panel = EngineeringSliderPanel(spec, state, parent=self)
        lay_eng.addWidget(self.eng_panel)

        root.addWidget(box_default, 1)
        root.addWidget(box_truth,   1)
        root.addWidget(box_eng,     1)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    spec_path = Path(__file__).resolve().parent / "DR3ControlSpec.yaml"
    spec = load_spec(str(spec_path))
    state = EPGControlState(spec)

    w = SpecTester(spec, state)
    w.resize(1300, 760)
    w.show()

    sys.exit(app.exec())