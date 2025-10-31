"""
Provides a single shared spec and control state for the EPG application.

Loads the YAML-defined EPGSettingsSpec once and exposes a singleton
EPGControlState as the application's ground truth. Use get_spec() and
get_state() to access them; call reset_state() only for testing/reset.
"""

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QObject

from .SpecLoader import load_spec
from .EPGControlState import EPGControlState
from .RelationsEngine import RelationsEngine

_SPEC = None
_STATE: Optional[EPGControlState] = None
_ENGINE: Optional[RelationsEngine] = None

def get_spec():
    """Return the single app-wide `EPGSettingsSpec` (created on first call)."""
    global _SPEC
    if _SPEC is None:
        spec_path = Path(__file__).with_name("epg_control_spec.yaml")
        _SPEC = load_spec(spec_path)
    return _SPEC

def get_state(parent: QObject | None = None) -> EPGControlState:
    """Return the single app-wide `EPGControlState` (created on first call)."""
    global _STATE, _ENGINE
    if _STATE is None:
        _STATE = EPGControlState(get_spec(), parent=parent)
        _ENGINE = RelationsEngine(get_spec(), _STATE, parent=parent)  # hook only once
    return _STATE

def reset_state():
    """Drop current instance so tests or a 'factory reset' can recreate it."""
    global _STATE, _ENGINE
    _STATE = None
    _ENGINE = None