import os
import sys
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Type, Union
from pathlib import Path

if __name__ == "__main__":
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

from epg_board.EPGControlKey import EPGControlKey
from epg_board.CurrentType import CurrentType


# ===== DATACLASSES =====

@dataclass(frozen=True)
class AffineMapping:
    """
    Affine map between a control and how its displayed in the view(s). 
    to_default_view(debug_value) = a * debug_value + b
    """
    a: float = 1.0
    b: float = 0.0
    round_to_int: bool = False

WidgetType = Literal["slider", "combo_box", "toggle_switch"]

@dataclass(frozen=True)
class EngineeringControl:
    """A single engineering (debug view) control definition."""
    key: EPGControlKey
    label: str
    unit: str
    pytype: Type
    widget_type: WidgetType
    default_value: Any
    # slider-only
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step_size: Optional[float] = None
    decimal_places: int = 0
    # combo-only
    choices: Optional[List[str]] = None

@dataclass(frozen=True)
class DefaultCurrentTypeConfig:
    """UI config for one current type (i.e AC/DC) of a default view control."""
    unit: str
    pytype: Type
    widget_type: WidgetType
    # slider
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step_size: Optional[float] = None
    decimal_places: int = 0
    # combo
    choices: Optional[List[str]] = None
    # mapping
    target_key: Optional[EPGControlKey] = None
    mapping: Optional[AffineMapping] = None
    default_value: Any = None

@dataclass(frozen=True)
class CurrentChangeEffects:
    """Actions performed when CURRENT_TYPE switches to a given state."""
    set_engineering: Dict[EPGControlKey, Any]
    reset_default_controls: List[str]

@dataclass(frozen=True)
class DefaultControl:
    """A single entomologist (default view) control defintion."""
    name: str
    label: str
    current_types: Dict[CurrentType, DefaultCurrentTypeConfig]
    on_change: Dict[CurrentType, CurrentChangeEffects] = field(default_factory=dict)


# Relations (dependency rules)
@dataclass(frozen=True)
class RelationTargetFormula:
    key: EPGControlKey
    formula: str  # expression string to eval

@dataclass(frozen=True)
class ControlRelation:
    name: str
    triggers: List[EPGControlKey]
    targets: List[RelationTargetFormula]  # supports multiple outputs per relation


@dataclass(frozen=True)
class EPGSettingsSpec:
    """Container for the entire parsed spec file."""
    engineering_controls: Dict[EPGControlKey, EngineeringControl]
    engineering_relations: List[ControlRelation]
    engineering_ui_order: List[EPGControlKey]
    default_controls: Dict[str, DefaultControl]
    default_ui_order: List[str]
    mappings: Dict[str, AffineMapping] = field(default_factory=dict)


# ========== Helpers ==========
_TYPE_MAP: Dict[str, Type] = {"int": int, "float": float, "str": str}

_CURRENT_TYPE_MAP: Dict[str, CurrentType] = {
    "AC": CurrentType.AC,
    "DC": CurrentType.DC,
}

def _coerce_type(name: str) -> Type:
    try:
        return _TYPE_MAP[name]
    except KeyError:
        raise ValueError(f"Unknown type '{name}'. Expected one of {list(_TYPE_MAP)}.")
    
def _coerce_key(name: str) -> EPGControlKey:
    try:
        return EPGControlKey[name]
    except KeyError:
        raise ValueError(f"Unknown EPGControlKey '{name}' in spec.")

def _coerce_current_type(name: str) -> CurrentType:
    try:
        return _CURRENT_TYPE_MAP[name]
    except KeyError:
        raise ValueError(f"Unknown current type '{name}'. Expected 'AC' or 'DC'.")
    

def _validate_slider_block(node: Dict[str, Any], where: str) -> None:
    """Throws an error if a slider node from the spec is invalid."""
    if "min_value" not in node or "max_value" not in node:
        raise ValueError(f"{where}: slider requires 'min_value' and 'max_value'.")
    if not isinstance(node["min_value"], (int, float)) or not isinstance(node["max_value"], (int, float)):
        raise ValueError(f"{where}: 'min_value'/'max_value' must be numeric.")
    if node["min_value"] > node["max_value"]:
        raise ValueError(f"{where}: min_value > max_value.")

def _validate_combo_block(node: Dict[str, Any], where: str) -> None:
    """Throws an error if a combo box node from the spec is invalid."""
    if "choices" not in node or not isinstance(node["choices"], list) or not node["choices"]:
        raise ValueError(f"{where}: combo_box requires non-empty 'choices' array.")

def _default_unit(s: Optional[str]) -> str:
    """Ensures `unit` is a string."""
    return s if s is not None else ""

# ---------- Parsers ----------
def _parse_affine_mapping(d: Dict[str, Any]) -> AffineMapping:
    """Parses a mapping dict from the spec into an AffineMapping object."""
    # YAML anchors are already resolved by yaml.safe_load
    return AffineMapping(
        a=float(d.get("a", 1.0)),
        b=float(d.get("b", 0.0)),
        round_to_int=bool(d.get("round_to_int", False)),
    )

def _parse_mappings(doc: Dict[str, Any]) -> Dict[str, AffineMapping]:
    out: Dict[str, AffineMapping] = {}
    for name, m in (doc.get("MAPPINGS") or {}).items():
        out[name] = _parse_affine_mapping(m)
    return out

def _parse_engineering_controls(doc: Dict[str, Any]) -> Dict[EPGControlKey, EngineeringControl]:
    raw = doc["ENGINEERING_CONTROL_SPEC"]
    result: Dict[EPGControlKey, EngineeringControl] = {}

    for key_str, node in raw.items():
        key = _coerce_key(key_str)
        widget_type: WidgetType = node["widget_type"]
        pytype = _coerce_type(str(node["type"]))
        label = str(node.get("label", key_str.replace("_", " ").title())) # default to title-case w/ spaces
        unit = _default_unit(node.get("unit", ""))

        if widget_type == "slider":
            _validate_slider_block(node, f"ENGINEERING_CONTROL_SPEC.{key_str}")
        elif widget_type == "combo_box":
            _validate_combo_block(node, f"ENGINEERING_CONTROL_SPEC.{key_str}")
        elif widget_type == "toggle_switch":
            pass # only present in default view
        else:
            raise ValueError(f"ENGINEERING_CONTROL_SPEC.{key_str}: unknown widget_type '{widget_type}'.")

        ctrl = EngineeringControl(
            key=key,
            label=label,
            unit=unit,
            pytype=pytype,
            widget_type=widget_type,
            default_value=node.get("default_value"),
            min_value=node.get("min_value"),
            max_value=node.get("max_value"),
            step_size=node.get("step_size"),
            decimal_places=int(node.get("decimal_places", 0)),
            choices=node.get("choices"),
        )
        result[key] = ctrl

    return result

def _parse_relation_item(node: Dict[str, Any]) -> ControlRelation:
    """
    Parse a single ENGINEERING_RELATIONS item:
      - name: str
      - triggers: [EPG key names...]
      - targets: [{key: EPG_KEY, formula: "expr"}, ...]
    """
    name = str(node.get("name", ""))
    if not name:
        raise ValueError(f"ENGINEERING_RELATIONS item missing 'name': {node}")

    triggers = [_coerce_key(x) for x in (node.get("triggers") or [])]

    targets_raw = node.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError(f"{name}: 'targets' must be a non-empty list.")

    targets: List[RelationTargetFormula] = []
    for t in targets_raw:
        if not isinstance(t, dict):
            raise ValueError(f"{name}: each item in 'targets' must be a mapping.")
        key = _coerce_key(t["key"])
        formula = t.get("formula")
        if not isinstance(formula, str) or not formula.strip():
            raise ValueError(f"{name}: target {key.name} missing non-empty 'formula' string.")
        targets.append(RelationTargetFormula(key=key, formula=formula))

    return ControlRelation(name=name, triggers=triggers, targets=targets)

def _parse_engineering_relations(doc: Dict[str, Any]) -> List[ControlRelation]:
    """
    Parse the ENGINEERING_RELATIONS section of the spec.
    """
    items = doc.get("ENGINEERING_RELATIONS") or []
    if not isinstance(items, list):
        raise ValueError("ENGINEERING_RELATIONS must be a list of mappings.")
    return [_parse_relation_item(n) for n in items]

def _parse_engineering_ui_order(doc: Dict[str, Any]) -> List[EPGControlKey]:
    order = doc.get("ENGINEERING_UI_ORDER") or []
    return [_coerce_key(x) for x in order]

def _parse_current_type_toggle(node: dict) -> DefaultControl:
    """Parse the effects of changing the current type between AC and DC."""
    label = node.get("label", "Current Type")

    # Parse on_change effects
    on_change_cfg: Dict[CurrentType, CurrentChangeEffects] = {}
    on_change_effect = node.get("on_change") or {}

    for type_str, eff in on_change_effect.items():
        type = _coerce_current_type(type_str)
        set_eng_raw = eff.get("set_engineering", {}) or {}
        set_eng = {_coerce_key(k): v for k, v in set_eng_raw.items()}
        set_view_defaults = list(eff.get("reset_default_controls", []) or [])
        on_change_cfg[type] = CurrentChangeEffects(
            set_engineering=set_eng,
            reset_default_controls=set_view_defaults,
        )

    return DefaultControl(name="CURRENT_TYPE", label=label, current_types={}, on_change=on_change_cfg)

def _parse_default_controls(doc: Dict[str, Any], mappings: Dict[str, AffineMapping]) -> Dict[str, DefaultControl]:
    raw = doc.get("ENTOMOLOGIST_CONTROL_SPEC") or {}
    result: Dict[str, DefaultControl] = {}

    for name, node in raw.items():
        # CURRENT_TYPE is special (toggle), no change on current type change
        if name == "CURRENT_TYPE":
            result[name] = _parse_current_type_toggle(node)
            continue

        label = node.get("label", name.replace("_", " ").title()) # default to title-case w/ spaces if not present
        current_types_node = node.get("current_types") or {}
        current_types: Dict[CurrentType, DefaultCurrentTypeConfig] = {}

        for current_type_str, cfg in current_types_node.items():
            current_type = _coerce_current_type(current_type_str)
            widget_type: WidgetType = cfg["widget_type"]
            pytype = _coerce_type(str(cfg["type"]))
            unit = _default_unit(cfg.get("unit", ""))

            if widget_type == "slider":
                _validate_slider_block(cfg, f"ENTOMOLOGIST_CONTROL_SPEC.{name}.{current_type_str}")
            elif widget_type == "combo_box":
                _validate_combo_block(cfg, f"ENTOMOLOGIST_CONTROL_SPEC.{name}.{current_type_str}")
            elif widget_type == "toggle_switch":
                pass # handled in _parse_current_type_toggle
            else:
                raise ValueError(f"ENTOMOLOGIST_CONTROL_SPEC.{name}.{current_type_str}: unknown widget_type '{widget_type}'.")

            # mapping / target
            target_key = None
            mapping_obj = None
            if "target" in cfg and cfg["target"] is not None:
                t = cfg["target"]
                if "key" in t:
                    target_key = _coerce_key(t["key"])
                if "mapping" in t and t["mapping"] is not None:
                    mapping_node = t["mapping"]
                    mapping_obj = _parse_affine_mapping(mapping_node)

            current_types[current_type] = DefaultCurrentTypeConfig(
                unit=unit,
                pytype=pytype,
                widget_type=widget_type,
                min_value=cfg.get("min_value"),
                max_value=cfg.get("max_value"),
                step_size=cfg.get("step_size"),
                decimal_places=int(cfg.get("decimal_places", 0)),
                choices=cfg.get("choices"),
                target_key=target_key,
                mapping=mapping_obj,
                default_value=cfg.get("default_value"),
            )

        result[name] = DefaultControl(name=name, label=label, current_types=current_types)

    return result


def _parse_default_ui_order(doc: Dict[str, Any]) -> List[str]:
    return list(doc.get("ENTOMOLOGIST_UI_ORDER") or [])


# ---------- Public loader ----------
def load_spec(path_or_str: Union[str, Path]) -> EPGSettingsSpec:
    """
    Load a YAML spec (file path or YAML string) into typed dataclasses.
    Raises ValueError on invalid shapes/unknown keys.
    """
    if isinstance(path_or_str, (str, Path)) and Path(str(path_or_str)).exists():
        text = Path(str(path_or_str)).read_text(encoding="utf-8")
    else:
        text = str(path_or_str)

    doc = yaml.safe_load(text) or {}
    if not isinstance(doc, dict):
        raise ValueError("Spec root must be a YAML mapping (dictionary).")

    # parse sections
    mappings = _parse_mappings(doc)
    engr_controls = _parse_engineering_controls(doc)
    engr_relations = _parse_engineering_relations(doc)
    engr_order = _parse_engineering_ui_order(doc)
    def_controls = _parse_default_controls(doc, mappings)
    def_order = _parse_default_ui_order(doc)

    # Sanity-check orders reference existing items
    missing_in_engr = [k for k in engr_order if k not in engr_controls]
    if missing_in_engr:
        raise ValueError(f"ENGINEERING_UI_ORDER references unknown keys: {missing_in_engr}")
    missing_in_default = [name for name in def_order if name not in def_controls]
    if missing_in_default:
        raise ValueError(f"ENTOMOLOGIST_UI_ORDER references unknown controls: {missing_in_default}")

    return EPGSettingsSpec(
        engineering_controls=engr_controls,
        engineering_relations=engr_relations,
        engineering_ui_order=engr_order,
        default_controls=def_controls,
        default_ui_order=def_order,
        mappings=mappings,
    )

if __name__ == "__main__":
    import os
    import sys
    import pprint

    spec = load_spec(root_dir + r"\epg_board\DR3ControlSpec.yaml")
    pprint.ppprint(spec.engineering_relations)

