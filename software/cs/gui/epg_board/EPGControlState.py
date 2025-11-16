from typing import Any,  Dict

from PyQt6.QtCore import QObject, pyqtSignal

from epg_board.EPGControlKey import EPGControlKey
from epg_board.SpecLoader import EPGSettingsSpec


class EPGControlState(QObject):
    """
    A container for all of the ground-truth engineering values. Loaded from the spec at runtime. 
    No more than one should be created for each instance of SCIDO (until we add support for multiple devices).

    Emits (<EPGControlKey>, old_value, new_value) whenever a values changes, 
    and a dict of these changes if multiple values are changed at once.
    """
    changed = pyqtSignal(EPGControlKey, object, object)
    batch   = pyqtSignal(dict)
  
    def __init__(self, spec: EPGSettingsSpec, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._values: Dict[EPGControlKey, Any] = {k: s.default_value for k, s in spec.engineering_controls.items()}

    def get(self, key: EPGControlKey) -> Any:
        return self._values[key]

    def set(self, key: EPGControlKey, value: Any) -> None:
        spec = self._spec.engineering_controls[key]
        try:
            coerced = spec.pytype(value)
        except Exception as e:
            raise TypeError(f"{key.name}: cannot coerce {value!r} to {spec.pytype}") from e
        old = self._values.get(key)
        if old != coerced:
            self._values[key] = coerced
            self.changed.emit(key, old, coerced)

    def set_batch(self, updates: Dict[EPGControlKey, Any]) -> None:
        changed = {}
        for k, v in updates.items():
            spec = self._spec.engineering_controls[k]
            try:
                val = spec.pytype(v)
            except Exception as e:
                raise TypeError(f"{k.name}: cannot coerce {v!r} to {spec.pytype}") from e
            if self._values.get(k) != val:
                self._values[k] = val
                changed[k] = val
        if changed:
            self.batch.emit(changed)
            for k, v in changed.items():
                self.changed.emit(k, None, v)

    # NOTE: currently unused
    def to_dict(self) -> Dict[str, Any]:
        return {k.name: self._values[k] for k in self._spec.engineering_controls}

    # NOTE: currently unused
    def load_from_dict(self, data: Dict[str, Any]) -> None:
        updates = {}
        for name, val in data.items():
            try:
                updates[EPGControlKey[name]] = val
            except KeyError:
                pass
        if updates:
            self.set_batch(updates)

# NOTE: currently unused
def as_env(full: EPGSettingsSpec, state: EPGControlState) -> Dict[str, Any]:
    # e.g., {"PGA_1": 2, "PGA_2": 3, ...}
    return {k.name: state.get(k) for k in full.engineering_controls.keys()}

