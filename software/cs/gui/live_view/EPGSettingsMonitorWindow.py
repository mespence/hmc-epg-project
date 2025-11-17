from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QGridLayout,
    QSpacerItem,
    QSizePolicy,
)

from epg_board.EPGControlState import EPGControlState, EPGControlKey

class EPGSettingsMonitorWindow(QWidget):
    """
    Pop-up window to display the ground-truth engineering values of the EPG board controls.
    """
    def __init__(self, state: EPGControlState, parent: str = None):
        super().__init__(parent=parent)
        self.state: EPGControlState = state
        self._spec = self.state._spec

        self.setWindowTitle("EPG Settings Monitor")
        self.setFixedWidth(300)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

        layout = QVBoxLayout(self)
        title = QLabel("EPG Settings | Ground-truth Values")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(4)
        self.grid.setColumnStretch(0, 0)
        self.grid.setColumnStretch(1, 0)
        self.grid.setColumnStretch(2, 0)
        self.grid.setColumnStretch(3, 1)
        layout.addLayout(self.grid)
        layout.addStretch(1)

        self._labels: Dict[EPGControlKey, QLabel] = {}

        # Build fixed list of the engineering settings
        for row, key in enumerate(self._spec.engineering_ui_order):
            ctrl = self._spec.engineering_controls[key]
            name = QLabel(ctrl.label)
            name.setStyleSheet("color: #aaa;")
            val = QLabel("")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            val.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            unit = QLabel(ctrl.unit or "")
            unit.setStyleSheet("color: #888;")

            self.grid.addWidget(name, row, 0)
            self.grid.addWidget(val, row, 1)
            self.grid.addWidget(unit, row, 2)

            self._labels[key] = val

        spacer = QSpacerItem(20, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.grid.addItem(spacer, 0, 3, len(self._spec.engineering_ui_order), 1)

        self.refresh_all()

        self.state.changed.connect(self._on_changed)
        self.state.batch.connect(self._on_batch)

    def _on_changed(self, key: EPGControlKey, _old: Any, _new: Any):
        self._update_one(key)

    def _on_batch(self, updates: Dict[EPGControlKey, Any]):
        for k in updates.keys():
            self._update_one(k)

    def _update_one(self, key: EPGControlKey):
        if key not in self._labels:
            return
        ctrl = self._spec.engineering_controls[key]
        v = self.state.get(key)
        if ctrl.pytype is float and ctrl.decimal_places > 0:
            text = f"{v:.{ctrl.decimal_places}f}"
        else:
            text = str(v)
        self._labels[key].setText(text)

    def refresh_all(self):
        for k in self._labels.keys():
            self._update_one(k)
