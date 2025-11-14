from typing import Any, Callable, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QComboBox
from PyQt6.QtCore import Qt

from epg_board.SpecLoader import EPGSettingsSpec, EngineeringControl
from epg_board.EPGControlState import EPGControlState, EPGControlKey
from .SliderRow import SliderRow


def _set_combo_text(combo, text: str):
    """Safely set combo box text, adding the entry if missing."""
    idx = combo.findText(text, Qt.MatchFlag.MatchExactly)
    old = combo.blockSignals(True)          # avoid firing while mirroring
    try:
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else: # fallback: add it so UI always reflects state
            combo.addItem(text)
            combo.setCurrentIndex(combo.count() - 1)
    finally:
        combo.blockSignals(old)

# -----------------------
# Main class
# -----------------------
class EngineeringSliderPanel(QWidget):
    """
    Engineering engineering (debug) slider panel for direct manipulation of EPG controls.

    Builds a grid of controls (sliders, combo boxes) from an EPGSettingsSpec
    and synchronizes bidirectionally with an EPGControlState instance.
    """
    def __init__(self, spec: EPGSettingsSpec, state: EPGControlState, parent: str = None):
        super().__init__(parent=parent)
        self._spec: EPGSettingsSpec = spec    
        self.epg_state: EPGControlState = state   
        self._updating: bool = False          

        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)
        title = QLabel("Debug View (Engineering EPG Settings)")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        layout.addLayout(self.grid)
        layout.addStretch(1)

        self._setters: Dict[EPGControlKey, Callable[[Any], None]] = {}

        # Build rows from spec
        for row, key in enumerate(self._spec.engineering_ui_order):
            ctrl = self._spec.engineering_controls[key]
            name = QLabel(ctrl.label)
            unit = QLabel(ctrl.unit or "")
            unit.setStyleSheet("color: #888;")

            if ctrl.widget_type == "combo_box":
                widget = self._create_combo_box_control(key, ctrl)
                self._setters[key] = lambda v, w=widget: _set_combo_text(w, str(v))

            elif ctrl.widget_type == "slider":
                widget = self._create_slider_row_control(key, ctrl)
                self._setters[key] = widget.setValue

            else:
                widget = QLabel("<toggle unsupported>")
                widget.setEnabled(False)
                self._setters[key] = lambda _v: None

            self.grid.addWidget(name, row, 0)
            self.grid.addWidget(widget, row, 1)
            self.grid.addWidget(unit, row, 2)

        # Initialize from current state
        self.refresh()

        # Listen to global state
        self.epg_state.changed.connect(self._on_state_changed)
        self.epg_state.batch.connect(self._on_state_batch)

    def _create_combo_box_control(self, key: EPGControlKey, ctrl: EngineeringControl):
        """Construct a combo box for a discrete-valued engineering control."""
        combo_box = QComboBox()
        for entry in ctrl.choices or []:
            combo_box.addItem(str(entry))
        combo_box.currentTextChanged.connect(lambda s, k=key, ctype=ctrl.pytype: self._on_combo(k, ctype(s)))
        return combo_box
        
    def _create_slider_row_control(self, key: EPGControlKey, ctrl: EngineeringControl):
        """Construct a SliderRow widget for an engineering control."""
        slider_row = SliderRow(
            spec_min=float(ctrl.min_value),
            spec_max=float(ctrl.max_value),
            step=ctrl.step_size or (1 if ctrl.pytype is int else 0.1),
            pytype=ctrl.pytype,
            decimals=ctrl.decimal_places or 0
        )
        slider_row.valueChanged.connect(lambda v, k=key: self._on_slider(k, v))
        return slider_row

    def _on_combo(self, key: EPGControlKey, value: Any):
        """Handle combo box updates from the UI."""
        if self._updating:
            return
        self.epg_state.set(key, value)

    def _on_slider(self, key: EPGControlKey, value: Any):
        """Handle slider/spinbox updates from the UI."""
        if self._updating:
            return
        self.epg_state.set(key, value)

    def _on_state_changed(self, key: EPGControlKey, _old: Any, new_value: Any):
        """Update a single widget when a control's state changes."""
        setter = self._setters.get(key)
        if not setter:
            return
        self._updating = True
        try:
            setter(new_value)
        finally:
            self._updating = False

    def _on_state_batch(self, updates: Dict[EPGControlKey, Any]):
        """Update multiple controls at once (batch signal)."""
        self._updating = True
        try:
            for key, value in updates.items():
                setter = self._setters.get(key)
                if setter:
                    setter(value)
        finally:
            self._updating = False

    def refresh(self):
        """Refresh all widgets to reflect current EPGControlState."""
        self._updating = True
        try:
            for k, setter in self._setters.items():
                setter(self.epg_state.get(k))
        finally:
            self._updating = False
