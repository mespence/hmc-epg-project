import sys
import os
import numpy as np
import threading
sys.path.insert(0, os.path.abspath("/Users/clairewang/Documents/GitHub/hmc-epg-project/software/cs/gui"))


from pyqtgraph import PlotWidget, PlotItem, ScatterPlotItem, PlotDataItem, mkPen, InfiniteLine, TextItem

from device_view_tester import data_simulator

from PyQt6.QtCore import QTimer, Qt, QPointF
from PyQt6.QtGui import QWheelEvent, QMouseEvent, QCursor, QKeyEvent, QGuiApplication
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QMessageBox, QFileDialog

from utils.PanZoomViewBox import PanZoomViewBox
from utils.ResourcePath import resource_path
from settings import settings

class DeviceViewWindow(PlotWidget):
    """
    Widget for constantly visualizing real-time waveform data streams with live updating.

    Features:
    - Displays continuous data from an incoming stream.
    - Supports live auto-scrolling or paused manual scrolling.
    - Zooming and panning via custom PanZoomViewBox.
    """

    def __init__(self, parent = None):
        """ Initalizes the DeviceViewWindow object.

            Sets up plotting area/custom viwebox and UI elements,
            thread-safe data buffers, live rendering timer at ~60 Hz"""
        # --- GENERAL INIT ITEMS ---
        super().__init__(parent = parent, viewBox=PanZoomViewBox(datawindow=self))
        self.plot_item: PlotItem = self.getPlotItem()
        self.viewbox: PanZoomViewBox = self.plot_item.getViewBox() # the plotting area (no axes, etc.)
        self.viewbox.datawindow = self
        self.viewbox.menu = None  # disable default menu

        settings.settingChanged.connect(self.on_setting_changed)

        self.compression = 0
        self.compression_text = TextItem(
            text=f"Compression: {self.compression: .1f}", color="black", anchor=(0, 0)
        )
        self.compression_text.setPos(QPointF(80, 15))
        self.scene().addItem(self.compression_text)

         # --- UI ELEMENTS ---
        self.curve: PlotDataItem = PlotDataItem(pen=mkPen(settings.get("data_line_color"), width=2))
        self.scatter: ScatterPlotItem = ScatterPlotItem(
            symbol="o", size=4, brush="blue"
        )  # the discrete points shown at high zooms
        self.zoom_level: float = 1
        self.chart_width: int = 400
        self.chart_height: int = 400
        self.setGeometry(0, 0, self.chart_width, self.chart_height)
        self.setBackground("white")
        self.setTitle("<b>EPG Device Monitor<b>", color="black", size="12pt")
        self.viewbox.setBorder(mkPen("black", width=3))

        self.plot_item.addItem(self.curve)
        self.plot_item.addItem(self.scatter)
        self.plot_item.setLabel("bottom", "<b>Time [s]</b>", color="black")
        self.plot_item.setLabel("left", "<b>Voltage [V]</b>", color="black")
        self.plot_item.showGrid(x=settings.get("show_v_grid"), y=settings.get("show_h_grid"))
        self.plot_item.layout.setContentsMargins(30, 30, 30, 20)
        self.plot_item.disableAutoRange() # no autoscaling

        self.leading_line: InfiniteLine = InfiniteLine(pos=0, angle=90, movable=False, pen=mkPen("red", width=3))
        self.addItem(self.leading_line)

        # Live mode button
        self.live_mode = True
        self.current_time = 0
        # for device view, follow only 10 seconds of visible data
        self.default_scroll_window = 10
        self.auto_scroll_window = 10
       
        # --- BUFFER ---
        # temporary buffer for incoming data, to be added to full xy_data every plot update
        self.buffer_data: list[tuple[float, float]] = []
        self.buffer_lock = threading.Lock() # lock to prevent data loss

        # --- BASELINE ---
        self.baseline: InfiniteLine = InfiniteLine(
            angle = 0, movable=True, pen=mkPen("gray", width = 3)
        )
        self.plot_item.addItem(self.baseline)
        self.baseline.setVisible(False)
        self.baseline_preview: InfiniteLine = InfiniteLine(
            angle = 0, movable = False,
            pen=mkPen("gray", style = Qt.PenStyle.DashLine, width = 3),
        )
        self.addItem(self.baseline_preview)
        self.baseline_preview.setVisible(False)
        self.baseline_preview_enabled: bool = False

    def update_plot_theme(self):
        plot_theme = settings.get("plot_theme") 
        self.setBackground(plot_theme["BACKGROUND"])

        self.setTitle("<b>Live Waveform Viewer</b>", size="12pt", color=plot_theme["FONT_COLOR_1"])
        self.plot_item.setLabel("bottom", "<b>Time [s]</b>", color=plot_theme["FONT_COLOR_1"])
        self.plot_item.setLabel("left", "<b>Voltage [V]</b>", color=plot_theme["FONT_COLOR_1"])

        self.compression_text.setColor(plot_theme["FONT_COLOR_1"])

        self.plot_item.getAxis("left").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("bottom").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("right").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("top").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis('top').setTicks([[]])  # disable ticks
        self.plot_item.getAxis('right').setTicks([[]])

        for _, comment in self.comments.items():
            comment.update_color()

    def on_setting_changed(self, key: str, value):
        if key == "show_h_grid":
            self.plotItem.showGrid(y=value)
        elif key == "show_v_grid":
            self.plotItem.showGrid(x=value)
        elif key == "show_comments":
            for _, comment in self.comments.items():
                comment.set_visible(value)
        elif key == "plot_theme":
            self.update_plot_theme()
        elif key in ("data_line_color", "data_line_width"):
            color = settings.get("data_line_color")
            width = settings.get("data_line_width")
            pen = mkPen(color=color, width=width)
            self.curve.setPen(pen)
            self.scatter.setPen(pen)

    def window_to_viewbox(self, point: QPointF) -> QPointF:
        """
        Converts between window (screen) coordinates and data (viewbox) coordinates.

        Parameters:
            point (QPointF): Point in global coordinates.

        Returns:
            QPointF: The corresponding point in data coordinates.
        """      
        scene_pos = self.mapToScene(point.toPoint())
        data_pos = self.viewbox.mapSceneToView(scene_pos)
        return data_pos
    
    def integrate_buffer_to_np(self):
        """
        Transfers all buffered data into the full waveform dataset.

        Thread-safe method that locks the buffer to prevent loss
        during integration. Intended to be called during plot updates
        or before closing the window.
        """
        with self.buffer_lock:
            if not self.buffer_data:
                return
            # create local copy of buffer and clear orig, to release lock
            data_to_process = self.buffer_data.copy()
            self.buffer_data.clear()

        # convert data to np array
        new_xy_data = np.array(data_to_process, dtype=float)
        self.data_modified = True

        self.xy_data[0] = np.concatenate((self.xy_data[0], new_xy_data[:, 0]))
        self.xy_data[1] = np.concatenate((self.xy_data[1], new_xy_data[:, 1]))

    def timed_plot_update(self):
        """
        Periodically triggers a refresh the plot.

        - Moves data from the buffer to full storage.
        - Calls update_plot()
        """
        self.integrate_buffer_to_np()
        self.update_plot()

    def update_compression(self) -> None:
        """
        Calculates the compression level based on the current zoom level and 
        updates the compression readout.
        A compresion of 1 corresponds to 0.2 sec/division.

        NOTE: WinDaq also has 'negative' compression levels for
        high levels of zooming out. We do not implement those here.
        """
        if not self.isVisible():
            return  # don't run prior to initialization

        # Get the pixel distance of one second
        plot_width = self.viewbox.geometry().width() * self.devicePixelRatioF()

        (x_min, x_max), _ = self.viewbox.viewRange()
        time_span = x_max - x_min

        if time_span == 0:
            return float("inf")  # Avoid division by zero

        pix_per_second = plot_width / time_span
        second_per_pix = 1 / (pix_per_second)

        # Convert to compression based on formula derived from experimenting with WinDaq
        self.compression = second_per_pix * 125
        if self.compression < 1:
            compression_str = round(self.compression, 2)
            compression_str = 1.0 if compression_str == "1.00" else compression_str
        else:
            compression_str = round(self.compression, 1)
        
        self.compression_text.setText(f"Compression Level: {compression_str}")  
    
    def set_baseline(self, y_pos: float):
        """
        Sets the baseline at the y-position clicked by the user.
        Displays the baseline line and disables baseline preview.

        Parameters:
            event (QMouseEvent): The mouse click event used to set baseline.
        """
        self.baseline.setPos(y_pos)
        self.baseline.setVisible(True)

        self.baseline_preview_enabled = False
        self.baseline_preview.setVisible(False)


    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        Handles mouse press events for interactions such as moving comments
        and setting baselines.

        Parameters:
            event (QMouseEvent): The mouse press event.
        """
        super().mousePressEvent(event)

        point = self.window_to_viewbox(event.position())
        x, y = point.x(), point.y()

        # any press event outside of graph box shouldn't register
        if not self.getViewBox().sceneBoundingRect().contains(event.scenePosition()):
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # for moving comment
            if self.baseline_preview_enabled:
                self.set_baseline(y)
            self.update_plot()
    
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handles key press events for shortcuts.

        - Shift+Space to add a comment at the current time.
        - "B" key to toggle baseline preview mode.

        Parameters:
            event (QKeyEvent): The key press event.
        """
        
        if event.key() == Qt.Key.Key_B:
            if self.baseline_preview_enabled:
                # Turn it off
                self.baseline_preview_enabled = False
                self.baseline_preview.setVisible(False)
            else:
                # prepare to set baseline
                self.baseline.setVisible(False)
                self.baseline_preview_enabled = True
                self.baseline_preview.setVisible(True)
                y_pos = self.viewbox.mapSceneToView(self.mapToScene(self.mapFromGlobal(QCursor.pos()))).y()
                self.baseline_preview.setPos(y_pos)
        elif event.key() == Qt.Key.Key_Escape and self.baseline_preview_enabled:
            self.baseline.setVisible(False)
            self.baseline_preview_enabled = False
            self.baseline_preview.setVisible(False)
        elif event.key() == Qt.Key.Key_Up or event.key() == Qt.Key.Key_Down or event.key() == Qt.Key.Key_Left or event.key() == Qt.Key.Key_Right:
            self.viewbox.keyPressEvent(event)
        self.viewbox.update()
    
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Handles mouse move events to update baseline and comment previews.
        Shows preview lines if enabled and updates plot accordingly.

        Parameters:
            event (QMouseEvent): The mouse move event.
        """
        super().mouseMoveEvent(event)

        point = self.window_to_viewbox(event.position())
        x, y = point.x(), point.y()

        (x_min, x_max), (y_min, y_max) = self.viewbox.viewRange()

        if self.baseline_preview_enabled:
            if y_min <= y <= y_max:
                self.baseline_preview.setPos(y)
                self.baseline_preview.setVisible(True)
            else:
                self.baseline_preview.setVisible(False)

        return
    
    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        Forwards mouse wheel scroll events to the custom PanZoomViewBox for
        zooming behavior.

        Parameters:
            event (QWheelEvent): The wheel scroll event.
        """
        self.viewbox.wheelEvent(event)

import time

if __name__ == "__main__":
    app = QApplication([])
    window = DeviceViewWindow()
    window.resize(1000, 500)
    window.show()
    time.sleep(2)
    data_simulator(window)
    sys.exit(app.exec())