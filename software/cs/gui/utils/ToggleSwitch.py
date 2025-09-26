import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, pyqtProperty, QSize, 
    QRectF, pyqtSignal
)
from PyQt6.QtGui import QPainter, QColor


class SwitchTrack(QWidget):
    def __init__(self, scale=0.75, parent=None):
        super().__init__(parent)
        self.scale = scale
        self._checked = False
        self._thumb_x = 3

        self._animation = QPropertyAnimation(self, b"thumb_pos")
        self._animation.setDuration(120)

        base_width = 60
        base_height = 30
        self.base_size = QSize(base_width, base_height)
        padding = 4  # pixels
        scaled_width = int(base_width * scale)
        scaled_height = int(base_height * scale) + padding
        self.setFixedSize(scaled_width, scaled_height)  # scaled widget size

        def sizeHint(self):
            return self.size()

    def setChecked(self, checked: bool):
        if self._checked == checked:
            return
        self._checked = checked

        margin = 3
        thumb_diameter = 30 - 2 * margin  # base size before scale
        start = self._thumb_x
        end = 60 - thumb_diameter - margin if checked else margin

        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

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
        scale = self.scale
        painter.scale(scale, scale)

        dx = (self.width() / scale - self.base_size.width()) / 2
        dy = (self.height() / scale - self.base_size.height()) / 2
        painter.translate(dx, dy)

        w = self.base_size.width()
        h = self.base_size.height()
        margin = 3
        thumb_diameter = h - 2 * margin

        track_color = QColor("#4aa8ff") #if self._checked else QColor("#ccc")
        painter.setBrush(track_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, h / 2, h / 2)

        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(self._thumb_x, margin, thumb_diameter, thumb_diameter))
        

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(int)

    def __init__(self, left_text, right_text, parent=None, gap=6):
        super().__init__(parent)
        self.gap = gap

        self.dc_label = QLabel(left_text, self)
        self.ac_label = QLabel(right_text, self)
        self.track = SwitchTrack(scale=0.75, parent=self)

        # Consistent font and alignment
        font = self.dc_label.font()
        font.setPointSize(10)
        self.dc_label.setFont(font)
        self.ac_label.setFont(font)

        self.dc_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ac_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.dc_label.setStyleSheet("margin: 0px; padding: 0px;")
        self.ac_label.setStyleSheet("margin: 0px; padding: 0px;")

        self.track.mousePressEvent = self.toggle_state
        self.update_label_styles()

    def resizeEvent(self, event):
        # Get track size
        track_size = self.track.size()
        center_x = self.width() // 2
        center_y = self.height() // 2

        # Center the track
        self.track.move(center_x - track_size.width() // 2, center_y - track_size.height() // 2)

        # Set label sizes and positions
        label_height = track_size.height()
        label_width = 24  # fixed width

        # Left (DC)
        dc_x = self.track.x() - self.gap - label_width
        self.dc_label.setGeometry(dc_x, self.track.y(), label_width, label_height)

        # Right (AC)
        ac_x = self.track.x() + track_size.width() + self.gap
        self.ac_label.setGeometry(ac_x, self.track.y(), label_width, label_height)

    def isChecked(self):
        return self.track._checked

    def toggle_state(self, event = None):
        self.track.setChecked(not self.track._checked)
        self.update_label_styles()
        self.toggled.emit(1 if self.track._checked else 0)

    def update_label_styles(self):
        if self.track._checked:
            self.ac_label.setStyleSheet("font-weight: bold; margin: 0px; padding: 0px;")
            self.dc_label.setStyleSheet("font-weight: normal; margin: 0px; padding: 0px;")
        else:
            self.dc_label.setStyleSheet("font-weight: bold; margin: 0px; padding: 0px;")
            self.ac_label.setStyleSheet("font-weight: normal; margin: 0px; padding: 0px;")

    def sizeHint(self):
        # Enough to fit both labels and track with gap
        track_w = self.track.width()
        label_w = 24
        return QSize(track_w + 2 * (self.gap + label_w), self.track.height())







class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC/DC Toggle Example")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.toggle = ToggleSwitch()
        layout.addWidget(self.toggle)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(250, 100)
    win.show()
    sys.exit(app.exec())
