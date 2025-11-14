from dataclasses import dataclass
from typing import Any, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal


def clamp(x: float, low: float, high: float) -> float:
    """Clamp a numeric value between given lower and upper bounds."""
    return max(low, min(high, x))

@dataclass(frozen=True)
class SliderSpec:
    min_v: float
    max_v: float
    step: float
    decimals: int  # used only if spinbox is QDoubleSpinBox

class SliderRow(QWidget):
    """
    Row widget with a synchronized QSlider and numeric spinbox.

    Emits `valueChanged(object)` with coerced numeric values.
    """
    valueChanged = pyqtSignal(object)

    def __init__(self,
                 spec_min: float,
                 spec_max: float,
                 step: Optional[float],
                 pytype: type,
                 decimals: int = 0,
                 parent: Optional[QWidget] = None):
        """
        Initialize slider row with numeric bounds and step size.

        Args:
            spec_min: Minimum allowed value.
            spec_max: Maximum allowed value.
            step: Step size between ticks.
            pytype: int or float type for values.
            decimals: Number of decimal places if float.
        """
        super().__init__(parent)
        self.pytype = pytype
        self.decimals = int(decimals)
        self.min_v = float(spec_min)
        self.max_v = float(spec_max)
        self.step = float(step) if step else (1.0 if pytype is int else 0.1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Slider with quantized ticks based on step
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        steps = max(1, round((self.max_v - self.min_v) / self.step))
        self.slider.setRange(0, steps)

        # Spin box
        if self.pytype is int:
            spinbox: QSpinBox | QDoubleSpinBox = QSpinBox()
            spinbox.setRange(int(round(self.min_v)), int(round(self.max_v)))
            spinbox.setSingleStep(int(round(self.step)) if self.step >= 1 else 1)
        else:
            db = QDoubleSpinBox()
            db.setRange(self.min_v, self.max_v)
            db.setDecimals(self.decimals)
            db.setSingleStep(self.step)
            spinbox = db

        spinbox.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        spinbox.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spinbox.setMinimumWidth(90)

        self.spinbox = spinbox

        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spinbox, 0)

        # Signal wiring
        self.slider.valueChanged.connect(self._on_slider_raw)
        self.spinbox.valueChanged.connect(self._on_spin_changed)

        self._updating = False

    def _coerce(self, v: float) -> Any:
        """Coerce float to proper numeric type with rounding."""
        if self.pytype is int:
            return int(round(v))
        if self.pytype is float and self.decimals > 0:
            return float(f"{v:.{self.decimals}f}")
        return self.pytype(v)

    def _on_slider_raw(self, raw: int):
        """Handle slider value changes and update spinbox."""
        if self._updating:
            return
        v = self.min_v + raw * self.step
        v = clamp(v, self.min_v, self.max_v)
        coerced = self._coerce(v)
        self._updating = True
        try:
            self.spinbox.blockSignals(True)
            if isinstance(self.spinbox, QDoubleSpinBox):
                self.spinbox.setValue(float(coerced))
            else:  # QSpinBox
                self.spinbox.setValue(int(round(coerced)))
            self.spinbox.blockSignals(False)
        finally:
            self._updating = False
        self.valueChanged.emit(coerced)

    def _on_spin_changed(self, v: float):
        """Handle spinbox changes and update slider."""
        if self._updating:
            return
        v = clamp(float(v), self.min_v, self.max_v)
        coerced = self._coerce(v)
        # mirror slider without reentry
        raw = round((float(coerced) - self.min_v) / self.step)
        self._updating = True
        try:
            self.slider.blockSignals(True)
            self.slider.setValue(int(raw))
            self.slider.blockSignals(False)
        finally:
            self._updating = False
        self.valueChanged.emit(coerced)

    def setValue(self, v: Any):
        """Set both slider and spinbox."""
        try:
            x = float(v)
        except Exception:
            return
        x = clamp(x, self.min_v, self.max_v)
        coerced = self._coerce(x)
        raw = round((float(coerced) - self.min_v) / self.step)
        self._updating = True
        try:
            self.slider.blockSignals(True)
            self.spinbox.blockSignals(True)
            self.slider.setValue(int(raw))
            if isinstance(self.spinbox, QDoubleSpinBox):
                self.spinbox.setValue(float(coerced))
            else:  # QSpinBox
                self.spinbox.setValue(int(round(coerced)))
        finally:
            self.slider.blockSignals(False)
            self.spinbox.blockSignals(False)
            self._updating = False

    def configure(self, spec: SliderSpec) -> None:
        """Reconfigures the range/step/decimals to a new SliderSpec."""
        self._updating = True
        try:
            # update attributes
            self.min_v   = float(spec.min_v)
            self.max_v   = float(spec.max_v)
            self.step    = float(spec.step)
            self.decimals = int(spec.decimals)

            # recompute slider ticks from (max-min)/step
            steps = max(1, round((self.max_v - self.min_v) / self.step))
            self.slider.blockSignals(True)
            self.slider.setRange(0, steps)
            self.slider.blockSignals(False)

            # update spinbox bounds/step/decimals
            if isinstance(self.spinbox, QDoubleSpinBox):
                self.spinbox.setRange(self.min_v, self.max_v)
                self.spinbox.setDecimals(self.decimals)
                self.spinbox.setSingleStep(self.step)
            else:  # QSpinBox (int)
                self.spinbox.setRange(int(round(self.min_v)), int(round(self.max_v)))
                self.spinbox.setSingleStep(int(round(self.step)) if self.step >= 1 else 1)

        finally:
            self._updating = False
