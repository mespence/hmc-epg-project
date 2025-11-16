from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QComboBox

from epg_board.SpecLoader import EPGSettingsSpec, DefaultControl, DefaultCurrentTypeConfig, AffineMapping
from epg_board.EPGControlState import EPGControlState
from epg_board.EPGControlKey import EPGControlKey
from epg_board.CurrentType import CurrentType
from live_view.slider_panel.SliderRow import SliderRow, SliderSpec
from utils.ToggleSwitch import ToggleSwitch

# -----------------------
# Helper functions
# -----------------------
def _is_numeric(t: type) -> bool:
    return t in (int, float)

def to_debug_view(mapping: AffineMapping, view_value: Any) -> Any:
    # default_view = a * debug_view + b  =>  debug_view = (default_view - b) / a
    a = mapping.a if mapping.a != 0 else 1.0
    c = (float(view_value) - mapping.b) / a
    if mapping.round_to_int:
        c = int(round(c))
    return c

def to_default_view(mapping: AffineMapping, control_value: Any) -> Any:
    v = mapping.a * float(control_value) + mapping.b
    if mapping.round_to_int:
        v = int(round(v))
    return v

def _set_combo_text(combo: QComboBox, text: str) -> None:
    idx = combo.findText(text)
    combo.blockSignals(True)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        if combo.isEditable():
            combo.setEditText(text)
    combo.blockSignals(False)

def _apply_slider_config(slider: SliderRow, cfg: DefaultCurrentTypeConfig) -> None:
    spec = SliderSpec(
        min_v=float(cfg.min_value),
        max_v=float(cfg.max_value),
        step=cfg.step_size or (1.0 if cfg.pytype is int else 0.1),
        decimals=(getattr(cfg, "decimal_places", 0) or 0)
    )
    slider.configure(spec)


# -----------------------
# Helper Data Structures
# -----------------------
@dataclass
class Entry:
    widget: QWidget
    unit_label: Optional[QLabel] = None

@dataclass
class Binding:
    """Current-type-specific mapping between a default-view control and a debug-view key."""
    target_key: EPGControlKey
    current_type_cfg: DefaultCurrentTypeConfig
    default_pytype: type
    state_pytype: type
    mapping: Any  # may be None

    def default_from_state(self, state_val):
        """Transform epg_state -> default_view value."""
        if self.mapping and _is_numeric(self.default_pytype) and _is_numeric(self.state_pytype):
            return to_default_view(self.mapping, state_val)
        return self.default_pytype(state_val)

    def state_from_default(self, default_val):
        """Transform default_view -> epg_state value."""
        if self.mapping and _is_numeric(self.default_pytype) and _is_numeric(self.state_pytype):
            return to_debug_view(self.mapping, default_val)
        return self.state_pytype(default_val)

# -----------------------
# Main class
# -----------------------
class DefaultSliderPanel(QWidget):
    """
    Default (entomologist) view for the EPG controls.
    Talks only to the epg_state, not the engineering view.
    Reconfigures rows in place between DC/AC
    """
    def __init__(self, spec: EPGSettingsSpec, epg_state: EPGControlState, parent=None):
        super().__init__(parent)
        self._spec = spec
        self.epg_state = epg_state
        self._updating = False

        self.current_type = (
            CurrentType.DC
            if self.epg_state.get(EPGControlKey.EXCITATION_FREQUENCY) == 0
            else CurrentType.AC
        )

        layout = QVBoxLayout(self)
        title = QLabel("Entomologist View")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        layout.addLayout(self.grid)
        layout.addStretch(1)

        # registry of ui rows and bindings to engineering values
        self._entries: Dict[str, Entry] = {}
        self._bindings: Dict[str, Binding] = {}

        # ----- Build UI -----

        # Current type toggle (fixed row 0)
        row = 0
        self._current_type_toggle = ToggleSwitch("DC", "AC", disabled_left=False)
        self._current_type_toggle.toggled.connect(self._on_current_type_toggled_int)
        self.grid.addWidget(QLabel("Current Type"), row, 0)
        self.grid.addWidget(self._current_type_toggle, row, 1, 1, 2)
        row += 1

        self._current_type_toggle.setChecked(self.current_type is CurrentType.AC, emit_signal=False)

        # build remaining rows from spec
        for name in self._spec.default_ui_order:
            if name == "CURRENT_TYPE":
                continue
            dctl = self._spec.default_controls[name]
            cfg = dctl.current_types.get(self.current_type)

            name_lbl = QLabel(dctl.label)
            unit_lbl = QLabel(self._unit_text(dctl, self.current_type))
            unit_lbl.setStyleSheet("color: #888;")

            if cfg.widget_type == "combo_box":
                widget = QComboBox()
                for choice in (cfg.choices or []):
                    widget.addItem(str(choice))
                widget.currentTextChanged.connect(lambda s, d=dctl: self._on_default_combo_changed(d, s))
            elif cfg.widget_type == "slider":
                widget = SliderRow(
                    spec_min=float(cfg.min_value),
                    spec_max=float(cfg.max_value),
                    step=cfg.step_size or (1 if cfg.pytype is int else 0.1),
                    pytype=cfg.pytype,
                    decimals=cfg.decimal_places or 0
                )
                widget.valueChanged.connect(lambda v, d=dctl: self._on_default_slider_changed(d, v))
            else:
                widget = QLabel("<unsupported>")
                widget.setEnabled(False)

            self.grid.addWidget(name_lbl, row, 0)
            self.grid.addWidget(widget,   row, 1)
            self.grid.addWidget(unit_lbl, row, 2)

            # Add unit to tooltip if pressent
            unit = self._unit_text(dctl, self.current_type)
            if unit:
                widget.setToolTip(unit)

            self._entries[dctl.name] = Entry(widget=widget, unit_label=unit_lbl)
            row += 1

        # Bind to current type + seed from epg_state
        self._rebind_all_for_current_type(self.current_type, seed_from_state=True)

        # Hook epg_state -> default_view mirroring
        self.epg_state.changed.connect(self._on_state_changed)
        self.epg_state.batch.connect(self._on_state_batch)

    # ---------- binding + current type ----------

    def _binding_for(self, dctl_name: str, current_type: CurrentType) -> Optional[Binding]:
        dctl = self._spec.default_controls.get(dctl_name)
        if not dctl:
            return None
        cfg = dctl.current_types.get(current_type)
        if not cfg or not cfg.target_key:
            return None
        target_ctrl = self._spec.engineering_controls[cfg.target_key]
        return Binding(
            target_key=cfg.target_key,
            current_type_cfg=cfg,
            default_pytype=cfg.pytype,
            state_pytype=target_ctrl.pytype,
            mapping=cfg.mapping
        )
    
    def _rebind_all_for_current_type(self, current_type: CurrentType, seed_from_state: bool) -> None:
        self._updating = True
        try:
            for name in self._spec.default_ui_order:
                if name == "CURRENT_TYPE":
                    continue
                dctl = self._spec.default_controls[name]
                entry = self._entries.get(name)
                if not entry:
                    continue

                unit_text = self._unit_text(dctl, current_type)
                if unit_text:
                    entry.widget.setToolTip(unit_text)
                if entry.unit_label:
                    entry.unit_label.setText(unit_text)

                binding = self._binding_for(name, current_type)
                self._bindings[name] = binding

                if not binding:
                    continue

                # Determine default_view value to seed
                if seed_from_state:
                    state_val = self.epg_state.get(binding.target_key)
                    try:
                        default_val = binding.default_from_state(state_val)
                    except Exception:
                        default_val = binding.current_type_cfg.default_value
                else:
                    default_val = binding.current_type_cfg.default_value

                # Apply config + seed value
                if isinstance(entry.widget, SliderRow):
                    entry.widget.blockSignals(True)
                    _apply_slider_config(entry.widget, binding.current_type_cfg) 
                    entry.widget.setValue(default_val)                 
                    entry.widget.blockSignals(False)
                elif isinstance(entry.widget, QComboBox):
                    entry.widget.blockSignals(True)
                    entry.widget.clear()
                    for c in (binding.current_type_cfg.choices or []):
                        entry.widget.addItem(str(c))
                    _set_combo_text(entry.widget, str(default_val))
                    entry.widget.blockSignals(False)
        finally:
            self._updating = False
    
    # ---------- units ----------

    def _unit_text(self, dctl: DefaultControl, current_type: CurrentType) -> str:
        cfg = dctl.current_types.get(current_type)
        if cfg and getattr(cfg, "unit", None): # try from UI
            return str(cfg.unit)
        if cfg and cfg.target_key:
            eng = self._spec.engineering_controls.get(cfg.target_key) # try from spec
            if eng and getattr(eng, "unit", None):
                return str(eng.unit) 
        return "" # fallback
    
    # ---------- default_view -> epg_state ----------

    def _on_default_slider_changed(self, dctl: DefaultControl, default_val: Any) -> None:
        if self._updating:
            return
        binding = self._bindings.get(dctl.name)
        if not binding:
            return
        try:
            state_val = binding.state_from_default(default_val)
        except Exception:
            return
        self.epg_state.set(binding.target_key, state_val)

    def _on_default_combo_changed(self, dctl: DefaultControl, default_text: str) -> None:
        if self._updating:
            return
        binding = self._bindings.get(dctl.name)
        if not binding:
            return
        try:
            state_val = binding.state_from_default(default_text)
        except Exception:
            return
        self.epg_state.set(binding.target_key, state_val)

    # ---------- epg_state -> default_view ----------

    def _on_state_changed(self, key: EPGControlKey, _old: Any, new: Any) -> None:
        self._mirror_one_from_state(key)

    def _on_state_batch(self, updates: Dict[EPGControlKey, Any]) -> None:
        for k in updates.keys():
            self._mirror_one_from_state(k)

    def _mirror_one_from_state(self, key: EPGControlKey) -> None:
        targets: List[Tuple[str, Binding]] = [
            (name, b) for name, b in self._bindings.items()
            if b and b.target_key == key
        ]
        if not targets:
            return

        state_val = self.epg_state.get(key)
        self._updating = True
        try:
            for name, binding in targets:
                entry = self._entries.get(name)
                if not entry:
                    continue
                try:
                    default_val = binding.default_from_state(state_val)
                except Exception:
                    default_val = binding.current_type_cfg.default_value

                if isinstance(entry.widget, SliderRow):
                    entry.widget.blockSignals(True)
                    entry.widget.setValue(default_val)
                    entry.widget.blockSignals(False)
                elif isinstance(entry.widget, QComboBox):
                    entry.widget.blockSignals(True)
                    _set_combo_text(entry.widget, str(default_val))
                    entry.widget.blockSignals(False)
        finally:
            self._updating = False


    def _on_current_type_toggled_int(self, v: int) -> None:
        new_current_type = CurrentType.AC if v == 1 else CurrentType.DC
        if new_current_type == self.current_type:
            return
        
        self.current_type = new_current_type

        # CURRENT_TYPE side effects (write to epg_state only)
        dctl_current_type = self._spec.default_controls.get("CURRENT_TYPE")

        eff = dctl_current_type.on_change.get(new_current_type) if dctl_current_type else None

        if eff and eff.set_engineering:
            self.epg_state.set_batch(eff.set_engineering)

        # Rebind + reseed from epg_state
        self._rebind_all_for_current_type(new_current_type, seed_from_state=True)