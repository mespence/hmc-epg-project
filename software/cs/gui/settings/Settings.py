# settings/Settings.py

from PyQt6.QtCore import QObject, pyqtSignal, QSettings, QRandomGenerator
from PyQt6.QtGui import QColor
import os
import json

class Settings(QObject):
    settingChanged = pyqtSignal(str, object)

    # === Static Definitions ===

    PLOT_LIGHT = {
        "NAME": "LIGHT",
        "BACKGROUND": "#F5F5F6",
        "FOREGROUND": "#111132",
        "AXIS_COLOR": "#111132",
        "FONT_COLOR_1": "#111132",
        "TRANSITION_LINE_COLOR": "#464650",
    }

    PLOT_DARK = {
        "NAME": "DARK",
        "BACKGROUND": "#1E1E1E",
        "FOREGROUND": "#888888",
        "AXIS_COLOR": "#EBEBEB",
        "FONT_COLOR_1": "#EBEBEB",
        "TRANSITION_LINE_COLOR": "#909092",
    }

    DEFAULT_SETTINGS = {
        "plot_theme": PLOT_LIGHT,
        "label_colors": {},
        "data_line_color": QColor("#4A82E2"),
        "data_line_width": 2,
        "show_h_grid": False,
        "show_v_grid": True,
        "show_labels": True,
        "show_durations": True,
        "show_comments": True,
        "default_recording_directory": os.getcwd(),
        "backup_recording_directory": os.getcwd(),
        "dm_range": 10,
        "default_min_voltage": -1.0,
        "default_max_voltage": 1.0,
    }

    SETTINGS_TYPE_MAP = { 
        "plot_theme": dict,
        "label_colors": dict,
        "data_line_color": QColor,
        "data_line_width": int,
        "show_h_grid": bool,
        "show_v_grid": bool,
        "show_labels": bool,
        "show_durations": bool,
        "show_comments": bool,
        "default_recording_directory": str,
        "backup_recording_directory": str,
        "dm_range": int,
        "default_min_voltage": float,
        "default_max_voltage": float,
    }

    def __init__(self):
        super().__init__()
        self._qsettings = QSettings("USDA", "SCIDO")

        self.load()


    def get(self, key):
        return getattr(self, key)
   
    def set(self, key, value):
        setattr(self, key, value)
        self.save(key, value)
        self.settingChanged.emit(key, value)

    def save(self, key, value):
        stored = self._prepare_for_storage(value, key)
        self._qsettings.setValue(key, stored)
        self._qsettings.sync()

    def save_all(self):
        for key in self.SETTINGS_TYPE_MAP:
            self.save(key, getattr(self, key))

    def load(self):
        for key, type_ in self.SETTINGS_TYPE_MAP.items():
            if self._qsettings.contains(key):
                raw = self._qsettings.value(key)
            else:
                raw = self.DEFAULT_SETTINGS.get(key)

            # Handle stringified nulls or empty values
            if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("none", "null", "")):
                raw = self.DEFAULT_SETTINGS.get(key)

            if type_ is QColor:
                val = QColor(raw)
            elif type_ is bool:
                val = str(raw).lower() in ("true", "1", "yes", "on")
            elif key == "plot_theme":
                theme_name = raw.get("NAME", "LIGHT").upper()
                val = self.PLOT_LIGHT if theme_name == "LIGHT" else self.PLOT_DARK
            elif key == "label_colors":
                val = raw or {}
            else:
                val = type_(raw)

            setattr(self, key, val)

    def reset(self):
        """
        Reset all settings to their hardcoded defaults.
        Clears QSettings, resets in-memory values, and re-emits all change signals.
        """
        self._qsettings.clear()
        self._qsettings.sync()

        for key, default_value in self.DEFAULT_SETTINGS.items():
            setattr(self, key, default_value)
            self._qsettings.setValue(key, self._prepare_for_storage(default_value))
            self.settingChanged.emit(key, default_value)


    def _prepare_for_storage(self, value, key=None):
        """
        Converts complex types (like QColor) into a format suitable for QSettings.
        """
        if isinstance(value, QColor):
            return value.name()
        if key == "label_colors" and isinstance(value, dict):
            return {
                label: {
                    "LIGHT": v["LIGHT"],
                    "DARK": v["DARK"]
                } for label, v in value.items()
            }
        return value

    # === Label Color Logic ===

    def get_label_color(self, label: str) -> QColor:
        label = label.upper()
        if label not in self.label_colors:
            self.label_colors[label] = self.generate_label_color_dict()
        theme = self.plot_theme["NAME"]
        return QColor(self.label_colors[label][theme])

    def set_label_color(self, label: str, color: QColor):
        label = label.upper()
        base_rgb = color.getRgb()[:3]
        inverted_rgb = [
            max(0, min(255, c - 80)) if self.plot_theme["NAME"] == "LIGHT"
            else min(255, c + 80) for c in base_rgb
        ]

        self.label_colors[label] = {
            "LIGHT": color.name() if self.plot_theme["NAME"] == "LIGHT" else QColor(*inverted_rgb).name(),
            "DARK": color.name() if self.plot_theme["NAME"] == "DARK" else QColor(*inverted_rgb).name(),
        }
        self.set("label_colors", self.label_colors)

    def generate_label_color_dict(self):
        #TODO: use distinctipy?
        hue = QRandomGenerator.global_().bounded(0, 360)
        saturation_light = QRandomGenerator.global_().bounded(150, 256)
        saturation_dark = saturation_light - 60
        lightness_light = 220
        lightness_dark = 30

        light_color = QColor()
        light_color.setHsl(hue, saturation_light, lightness_light)
        dark_color = QColor()
        dark_color.setHsl(hue, saturation_dark, lightness_dark)

        return {
            "LIGHT": light_color.name(),
            "DARK": dark_color.name()
        }

    def rename_label(self, old: str, new: str):
        new = new.upper()
        self.label_colors[new] = self.label_colors.pop(old.upper())
        self.set("label_colors", self.label_colors)

    def delete_label(self, label: str):
        self.label_colors.pop(label.upper(), None)
        self.set("label_colors", self.label_colors)