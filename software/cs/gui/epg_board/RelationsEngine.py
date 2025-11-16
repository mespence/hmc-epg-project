from typing import Any, Dict

from PyQt6.QtCore import QObject

from epg_board.SpecLoader import EPGSettingsSpec
from epg_board.EPGControlState import EPGControlState, EPGControlKey

def clamp(x: float, low: float, high: float) -> float:
    """Clamp a numeric value between given lower and upper bounds."""
    return max(low, min(high, x))

# TODO: remake this using AST to complile into expressions once rather than eval'ing every time?

class RelationsEngine(QObject):
    """
    Watches EPGControlState changes and applies ENGINEERING_RELATIONS from 
    the spec to keep derived controls in sync.
    """
    def __init__(self, spec: EPGSettingsSpec, epg_state: EPGControlState, parent=None):
        super().__init__(parent)
        self._spec = spec
        self.epg_state = epg_state
        self._applying = False

        self.epg_state.changed.connect(self._on_changed)
        self.epg_state.batch.connect(self._on_batch)

    def _on_changed(self, key: EPGControlKey, _old: Any, _new: Any):
        """Handle single control value changes."""
        if self._applying:
            return
        self.apply_relations({key})

    def _on_batch(self, updates: Dict[EPGControlKey, Any]):
        """Handle batched control value changes."""
        if self._applying:
            return
        self.apply_relations(set(updates.keys()))

    def apply_relations(self, changed: set):
        """Evaluate and apply relations affected by changed controls."""
        # Collect relations whose triggers intersect changed
        pending = [rel for rel in self._spec.engineering_relations
                   if any(t in changed for t in rel.triggers)]
        if not pending:
            return

        # Prepare environment
        def make_env() -> Dict[str, Any]:
            """Return evaluation environment for relation formulas."""
            env = {k.name: self.epg_state.get(k) for k in self._spec.engineering_controls.keys()}
            env.update({"clamp": clamp})
            return env

        updates: Dict[EPGControlKey, Any] = {}

        # Evaluate each relation's targets in sequence
        for rel in pending:
            env = make_env()
            # Allow targets within the same relation to see preceding results
            for tf in rel.targets:
                try:
                    val = eval(tf.formula, env, {})
                except Exception as e:
                    print(f"[Relations] {rel.name}:{tf.key.name} formula error: {e}")
                    continue
                # stage update & update env for subsequent targets
                updates[tf.key] = val
                env[tf.key.name] = val

        if updates:
            self._applying = True
            try:
                self.epg_state.set_batch(updates)
            finally:
                self._applying = False