import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, 
    QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, pyqtProperty, QSize,
    QRectF, pyqtSignal
)
from PyQt6.QtGui import QPainter, QColor, QFontMetrics


class SwitchTrack(QWidget):
    """
    Track + thumb. When _checked == True (right), track is active_color.
    When _checked == False (left), track is:
      - inactive_color if grey_left is True
      - active_color   if grey_left is False
    """
    def __init__(self, *, scale=0.75, disabled_left=True,
                 active_color="#4aa8ff", inactive_color="#ccc", parent=None):
        super().__init__(parent)
        self.scale = scale
        self._checked = False
        self._thumb_x = 3

        self.grey_left = disabled_left
        self.active_color = QColor(active_color)
        self.inactive_color = QColor(inactive_color)

        self._animation = QPropertyAnimation(self, b"thumb_pos")
        self._animation.setDuration(120)

        base_width, base_height = 60, 30
        self.base_size = QSize(base_width, base_height)
        padding = 4
        scaled_width = int(base_width * scale)
        scaled_height = int(base_height * scale) + padding
        self.setFixedSize(scaled_width, scaled_height)

    def sizeHint(self):
        return self.size()

    def setChecked(self, checked: bool):
        if self._checked == checked:
            return
        self._checked = checked
        margin = 3
        thumb_diameter = 30 - 2 * margin
        start = self._thumb_x
        end = 60 - thumb_diameter - margin if checked else margin
        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setGreyLeft(self, grey: bool):
        """Control whether left (unchecked) state greys out the track."""
        if self.grey_left != grey:
            self.grey_left = grey
            self.update()

    def setColors(self, *, active: str | QColor = None, inactive: str | QColor = None):
        if active is not None:
            self.active_color = QColor(active)
        if inactive is not None:
            self.inactive_color = QColor(inactive)
        self.update()

    def get_thumb_pos(self):
        return self._thumb_x

    def set_thumb_pos(self, value):
        self._thumb_x = int(value)
        self.update()

    thumb_pos = pyqtProperty(int, fget=get_thumb_pos, fset=set_thumb_pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # center the scaled content
        s = self.scale
        painter.scale(s, s)
        dx = (self.width() / s - self.base_size.width()) / 2
        dy = (self.height() / s - self.base_size.height()) / 2
        painter.translate(dx, dy)

        w, h = self.base_size.width(), self.base_size.height()
        margin = 3
        thumb_diameter = h - 2 * margin

        # Decide track color based on state + grey_left flag
        if self._checked:
            track_color = self.active_color
        else:
            track_color = self.inactive_color if self.grey_left else self.active_color

        painter.setBrush(track_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, h / 2, h / 2)

        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(self._thumb_x, margin, thumb_diameter, thumb_diameter))



class ToggleSwitch(QWidget):
    toggled = pyqtSignal(int)

    def __init__(self, left_label_text: str, right_label: str, *,
                 disabled_left: bool = True,
                 active_color: str = "#4aa8ff",
                 inactive_color: str = "#ccc",
                 parent=None, gap=6, label_padding_px=6, max_label_px: int | None = None):
        super().__init__(parent)
        self.gap = gap
        self.label_padding_px = label_padding_px
        self.max_label_px = max_label_px  # optional hard cap per side

        self.left_label = QLabel(left_label_text, self)
        self.right_label = QLabel(right_label, self)
        self._left_text = left_label_text
        self._right_text = right_label

        for label in (self.left_label, self.right_label):
            label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
            label.setWordWrap(False)               # single line
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            #label.setMinimumWidth(0)
            #label.setMaximumWidth(16777215)

        self.track = SwitchTrack(scale=0.75, disabled_left=disabled_left,
                                 active_color=active_color,
                                 inactive_color=inactive_color,
                                 parent=self)

        # Typography
        font = self.left_label.font()
        font.setPointSize(10)
        self.left_label.setFont(font)
        self.right_label.setFont(font)

        self.left_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.right_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Click to toggle
        self.track.mousePressEvent = lambda e: self.toggle_state()
        self.left_label.mousePressEvent  = lambda e: self.toggle_state()
        self.right_label.mousePressEvent = lambda e: self.toggle_state()
        self.update_label_styles()

    # Let the widget suggest enough width to fully show both labels when possible
    def sizeHint(self):
        fm  = QFontMetrics(self.left_label.font())
        pad = 2 * self.label_padding_px

        L = (fm.horizontalAdvance(self._left_text)  + pad) if self._left_text.strip()  else 0
        R = (fm.horizontalAdvance(self._right_text) + pad) if self._right_text.strip() else 0

        track_w = self.track.width()
        h = self.track.height()

        left_gap  = self.gap if L > 0 else 0
        right_gap = self.gap if R > 0 else 0

        return QSize(L + left_gap + track_w + right_gap + R, h)


    def minimumSizeHint(self):
        # Be generous so layouts don't force elision
        return self.sizeHint()


    def isChecked(self):
        return self.track.isChecked()
    
    def setChecked(self, checked: bool, *, emit_signal: bool = False):
        """Programmatically set state. By default, does not emit."""
        if self.track.isChecked() == checked:
            return
        self.track.setChecked(checked)
        self.update_label_styles()
        if emit_signal:
            self.toggled.emit(1 if checked else 0)

    def toggle_state(self, event=None):
        self.setChecked(not self.isChecked(), emit_signal=True)

    def setLeftText(self, text: str):
        self._left_text = text
        self.left_label.setText(text)
        self.update()

    def setRightText(self, text: str):
        self._right_text = text
        self.right_label.setText(text)
        self.update()

    def update_label_styles(self):
        if self.track.isChecked():
            self.right_label.setStyleSheet("font-weight: bold; margin: 0px; padding: 0px;")
            self.left_label.setStyleSheet("font-weight: normal; margin: 0px; padding: 0px;")
        else:
            self.left_label.setStyleSheet("font-weight: bold; margin: 0px; padding: 0px;")
            self.right_label.setStyleSheet("font-weight: normal; margin: 0px; padding: 0px;")

    def resizeEvent(self, event):
        track_size = self.track.size()
        cy = self.height() // 2

        fmL = QFontMetrics(self.left_label.font())
        fmR = QFontMetrics(self.right_label.font())
        pad = self.label_padding_px

        Lneed = (fmL.horizontalAdvance(self._left_text)  + 2*pad) if self._left_text.strip()  else 0
        Rneed = (fmR.horizontalAdvance(self._right_text) + 2*pad) if self._right_text.strip() else 0

        left_gap  = self.gap if Lneed > 0 else 0
        right_gap = self.gap if Rneed > 0 else 0

        track_x = Lneed + left_gap
        track_x = max(0, min(track_x, self.width() - track_size.width()))
        self.track.move(track_x, cy - track_size.height() // 2)

        left_space  = max(0, track_x - (left_gap if Lneed > 0 else 0))
        right_space = max(0, self.width() - (track_x + track_size.width() + (right_gap if Rneed > 0 else 0)))

        epsilon = 2
        left_display  = fmL.elidedText(self._left_text, Qt.TextElideMode.ElideRight, max(0, left_space  - 2*pad + epsilon))
        right_display = fmR.elidedText(self._right_text, Qt.TextElideMode.ElideRight, max(0, right_space - 2*pad + epsilon))

        self.left_label.setText(left_display)
        self.right_label.setText(right_display)
        self.left_label.setToolTip(self._left_text  if left_display  != self._left_text  else "")
        self.right_label.setToolTip(self._right_text if right_display != self._right_text else "")

        label_h = track_size.height()
        if Lneed > 0:
            self.left_label.setGeometry(track_x - left_gap - left_space, self.track.y(), left_space, label_h)
        else:
            self.left_label.setGeometry(0, 0, 0, 0)

        rx = track_x + track_size.width() + (right_gap if Rneed > 0 else 0)
        if Rneed > 0:
            self.right_label.setGeometry(rx, self.track.y(), right_space, label_h)
        else:
            self.right_label.setGeometry(0, 0, 0, 0)



# Demo
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Example 1: left greys out (default)
        self.toggle1 = ToggleSwitch("", "Debug View", disabled_left=True)
        layout.addWidget(self.toggle1)

        # Example 2: left stays colored (no grey)
        self.toggle2 = ToggleSwitch("DC", "AC", disabled_left=False, active_color="#00c853")
        layout.addWidget(self.toggle2)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(300, 140)
    win.show()
    sys.exit(app.exec())
