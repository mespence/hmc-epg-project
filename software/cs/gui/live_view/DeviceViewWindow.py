import time

from numpy.typing import NDArray
import sys
import numpy as np
import threading
import random


from pyqtgraph import PlotWidget, PlotItem, ScatterPlotItem, PlotDataItem, mkPen, InfiniteLine, TextItem

from PyQt6.QtCore import QTimer, Qt, QPointF
from PyQt6.QtGui import QWheelEvent, QMouseEvent, QCursor, QKeyEvent, QGuiApplication
from PyQt6.QtWidgets import QApplication
from pyqtgraph import AxisItem

from utils.PanZoomViewBox import PanZoomViewBox
from settings import settings

class DeviceViewWindow(PlotWidget):
    """
    Widget for constantly visualizing real-time waveform data streams with live updating.
    Designed for device monitoring.

    Features:
    - Displays continuous data from an incoming stream.
    - Horizontal range customizable through settings 
    - Vertical Zooming and panning via custom PanZoomViewBox.
    """

    def __init__(self, data_source = None, parent = None):
        """ Initalizes the DeviceViewWindow object.

            Sets up plotting area/custom viwebox and UI elements,
            thread-safe data buffers, live rendering timer at ~60 Hz"""
        super().__init__(parent=parent)
        self.window_type = "device_view" # unique definition for PanZoomViewBox check


        # --- GENERAL INIT ITEMS ---

        # Create custom horizontal axis ("Seconds Ago")
        fixed_axis = FixedSecondsAgoAxis(device_window=self, orientation='bottom')

        self.viewbox = PanZoomViewBox(datawindow=self)
        self.plot_item: PlotItem = PlotItem(viewBox=self.viewbox, axisItems={'bottom': fixed_axis})
        self.plot_item.hideButtons()
        self.setCentralItem(self.plot_item)

        self.viewbox.menu = None
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
        self.plot_item.setTitle("<b>EPG Device Monitor<b>", color="black", size="12pt")
        self.viewbox.setBorder(mkPen("black", width=3))

        self.plot_item.addItem(self.curve)
        self.plot_item.addItem(self.scatter)
        self.plot_item.setLabel("bottom", "<b>Seconds Ago[s]</b>", color="black")
        self.plot_item.setLabel("left", "<b>Voltage [V]</b>", color="black")
        self.plot_item.showGrid(x=settings.get("show_v_grid"), y=settings.get("show_h_grid"))
        self.plot_item.layout.setContentsMargins(30, 30, 30, 20)
        self.plot_item.disableAutoRange() # no autoscaling
        self.plot_item.setYRange(-1, 1)

        self.leading_line: InfiniteLine = InfiniteLine(pos=0, angle=90, movable=False, pen=mkPen("red", width=3))
        self.addItem(self.leading_line)

        # device mode button
        self.device_mode = True
        self.current_time = 0
        # for device view, follow only amount of visible data determined by settings
        self.default_scroll_window = settings.get("dm_range")
        self.auto_scroll_window = settings.get("dm_range")

        # set initial x-rang: Display range from 0 to auto_scroll_window in absolute time
        self.viewbox.setXRange(0, self.auto_scroll_window, padding=0)
        self.viewbox.setLimits(xMin=0, xMax=None, yMin=-1, yMax=1)
       

        # --- DATA STORAGE ---

        # holds all historical data
        self.xy_data: list[NDArray] = [np.array([]), np.array([])]
        self.xy_rendered: list[NDArray] = [np.array([]), np.array([])]
        self.last_rendered_x_range = (0,0)

        # connect to the live view tab's data buffer
        self.data_source = data_source

        if self.data_source is not None:
            # share buffer and lock from the tab
            self.buffer_data = self.data_source.buffer_data
            self.buffer_lock = self.data_source.buffer_lock
        else:
            # fallback if no data_source passed
            self.buffer_data = []
            self.buffer_lock = threading.Lock()
        
        self.data_modified = False
        
        ### REMOVE LATER: FOR TESTING ###
        # Start data simulation thread inside the device window itself
        self._start_simulator()

        # Start a timer for periodic updates (~60 fps)
        self.plot_update_timer = QTimer()
        self.plot_update_timer.setInterval(int(1000/60))
        self.plot_update_timer.timeout.connect(self.timed_plot_update)
        self.plot_update_timer.start(50)

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
        self.plot_item.addItem(self.baseline_preview)
        self.baseline_preview.setVisible(False)
        self.baseline_preview_enabled: bool = False
    
    def _start_simulator(self):
        def simulation():
            t = 0
            while True:
                with self.buffer_lock:
                    self.buffer_data.append((t, random.uniform(-1, 1)))
                t += 1
                time.sleep(0.5)

        threading.Thread(target=simulation, daemon=True).start()

    def update_plot_theme(self):
        plot_theme = settings.get("plot_theme") 
        self.setBackground(plot_theme["BACKGROUND"])

        self.plot_item.setTitle("<b>Live Waveform Viewer</b>", size="12pt", color=plot_theme["FONT_COLOR_1"])
        self.plot_item.setLabel("bottom", "<b>Seconds Ago [s]</b>", color=plot_theme["FONT_COLOR_1"])
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
        elif key == "dm_range":
            self.auto_scroll_window = value
            self.default_scroll_window = value
            
            # set new x-range 
            self.update_plot()
            
            self.viewbox.update()
            self.plot_item.getAxis('bottom').update()
        

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

        self.current_time = new_xy_data[-1,0]

    def timed_plot_update(self):
        """
        Periodically triggers a refresh the plot.

        - Moves data from the buffer to full storage.
        - Calls update_plot()
        """
        self.integrate_buffer_to_np()
        self.update_plot()

    
    def downsample_visible(
        self, full_xy_data: NDArray, x_range: tuple[float, float] = None, max_points=4000, method = 'peak'
    ) -> None:
        """
        Downsamples waveform data in the visible x range using the selected method.
        Modifies self.xy_rendered and sorts it (efficient for comment insertion idx
        finding).

        Parameters:
            xy (NDArray): full xy data array.
            x_range (tuple[float, float]): Optional x-axis range to downsample.
            max_points (int): Max number of points to plot.
            method (str): 'subsample', 'mean', or 'peak' downsampling method.
        
        NOTE: 
            `subsample` samples the first point of each bin (fastest)
            `mean` averages each bin
            `peak` returns the min and max point of each bin (slowest, best looking)
        """
        x, y = full_xy_data

        # Filter to x_range if provided
        if x_range is not None:
            x_min, x_max = x_range

            left_idx = np.searchsorted(x, x_min, side="left")
            right_idx = np.searchsorted(x, x_max, side="right")

            if right_idx - left_idx <= 250: 
                # render additional point on each side at very high zooms
                left_idx = max(0, left_idx - 1)
                right_idx = min(len(x), right_idx + 1)
  
  
            x_sliced = x[left_idx:right_idx].copy()
            y_sliced = y[left_idx:right_idx].copy()   
        else:
            x_sliced = x.copy()
            y_sliced = y.copy()   
    
        num_points = len(x_sliced)

        if num_points <= max_points:  # no downsampling needed
            # referencing self.xy_data
            self.xy_rendered[0] = x_sliced
            self.xy_rendered[1] = y_sliced
            return

        if method == 'subsampling': 
            stride = num_points // max_points
            x_out = x_sliced[::stride].copy()
            y_out = y_sliced[::stride].copy()

        elif method == 'mean':
            stride = num_points // max_points
            num_windows = num_points // stride
            start_idx = stride // 2
            x_out = x_sliced[start_idx : start_idx + num_windows * stride : stride].copy()
            y_out = y_sliced[:num_windows * stride].reshape(num_windows, stride).mean(axis = 1)
        elif method == 'peak':
            stride = max(1, num_points // (max_points // 2))  # each window gives 2 points
            num_windows = num_points // stride

            x_win = x_sliced[stride // 2 : stride // 2 + num_windows * stride : stride]
            y_reshaped = y_sliced[: num_windows * stride].reshape(num_windows, stride)

            # create output arrays for peaks
            x_out = np.empty(num_windows * 2)
            y_out = np.empty(num_windows * 2)

            # assign peaks
            y_out[::2] = y_reshaped.max(axis=1)
            y_out[1::2] = y_reshaped.min(axis=1)
            x_out[::2] = x_win
            x_out[1::2] = x_win

        else:
            raise ValueError(
                'Invalid "method" arugment. ' \
                'Please select either "subsampling", "mean", or "peak".'
            )

        self.xy_rendered[0] = x_out
        self.xy_rendered[1] = y_out

        # sort xy_rendered for ability to add comment to past and move comment
        sort_idx = np.argsort(self.xy_rendered[0])
        self.xy_rendered[0] = self.xy_rendered[0][sort_idx]
        self.xy_rendered[1] = self.xy_rendered[1][sort_idx]

    def update_plot(self):
        """
        Redraws the waveform on the screen if live mode is enabled
        or the user manually adjusts the viewbox.
        """
        current_x_range, _ = self.viewbox.viewRange()

        rerender = False
        if self.device_mode:
            rerender = True
        else:
            if current_x_range != self.last_rendered_x_range or current_x_range[1] > self.current_time:
                rerender = True

        if not rerender:
            # no change in viewbox, just update leading line to follow live data
            self.leading_line.setPos(self.current_time)
            self.viewbox.update()
            return

        # rerender needed
        self.viewbox.setLimits(xMin=None, xMax=None, yMin=None, yMax=None) # clear stale data (avoids warning)

        if self.device_mode:
            end = self.current_time
            start = end - self.auto_scroll_window
            offset = 0.1 # when zoomed in, leading line lags with plotting so need offset to keep hidden
            self.viewbox.setXRange(start, end, padding=0)
            self.downsample_visible(self.xy_data, x_range=(start, end))
            self.leading_line.setPos(end+offset)

            self.leading_line.setPos(self.current_time + offset)

        else:
            self.downsample_visible(self.xy_data, x_range=current_x_range)
            self.leading_line.setPos(self.current_time)


        # SCATTER
        time_span = current_x_range[1] - current_x_range[0]
        plot_width = self.viewbox.geometry().width() * self.devicePixelRatioF()
        pix_per_second = plot_width / time_span if time_span != 0 else float("inf")
        default_pix_per_second = plot_width / self.default_scroll_window
        self.zoom_level = pix_per_second / default_pix_per_second

        # scatter if zoom is greater than 300%
        self.scatter.setVisible(bool(self.zoom_level >= 3))
        if self.scatter.isVisible():
            self.scatter.setData(self.xy_rendered[0], self.xy_rendered[1])
        else:
            self.scatter.setData([], []) # clear scatter data when not visible

        self.curve.setData(self.xy_rendered[0], self.xy_rendered[1])
        self.viewbox.update()

        # update last rendered range
        self.last_rendered_x_range = current_x_range
        
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
        Handles mouse press events for setting baselines.

        Parameters:
            event (QMouseEvent): The mouse press event.
        """
        super().mousePressEvent(event)

        point = self.window_to_viewbox(event.position())
        y = point.y()

        # any press event outside of graph box shouldn't register
        if not self.getViewBox().sceneBoundingRect().contains(event.scenePosition()):
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self.baseline_preview_enabled:
                self.set_baseline(y)
            self.update_plot()
    
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handles key press events for shortcuts.

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
        elif event.key() == Qt.Key.Key_S and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.save_df()
        
        self.viewbox.update()
    
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Handles mouse move events to update baseline.
        Shows preview lines if enabled and updates plot accordingly.

        Parameters:
            event (QMouseEvent): The mouse move event.
        """
        super().mouseMoveEvent(event)

        point = self.window_to_viewbox(event.position())
        y = point.y()

        (y_min, y_max) = self.viewbox.viewRange()[1]

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

class FixedSecondsAgoAxis(AxisItem):
    def __init__(self, device_window, *args, **kwargs): 
        super().__init__(*args, **kwargs) 
        self.device_window = device_window
        # Enable minor ticks to be drawn
        self.setStyle(showValues=True)
    
    def tickValues(self, minVal, maxVal, size):
        """
        Override to return fixed tick positions.
        This controls WHERE ticks appear on the axis.
        """
        current_time = self.device_window.current_time
        
        # Get the time range being displayed
        time_span = maxVal - minVal
        
        # Determine tick spacing based on visible range
        if time_span <= 10:
            self.major_spacing = 1  
        elif time_span <= 40:
            self.major_spacing = 2  
        elif time_span <= 60:
            self.major_spacing = 5  
        else:
            self.major_spacing = 30  
        self.minor_spacing = self.major_spacing/10
        
        # Calculate "seconds ago" at the edges of visible range
        min_sec_ago = current_time - maxVal  # Leftmost edge (highest "seconds ago")
        max_sec_ago = current_time - minVal  # Rightmost edge (lowest "seconds ago", closest to 0)
        
        # Generate MAJOR ticks at integer multiples of major_spacing
        # Find the first major tick position >= min_sec_ago
        if min_sec_ago >= 0:
            start_major = int(np.ceil(min_sec_ago / self.major_spacing)) * self.major_spacing
        else:
            start_major = 0
        
        # Find the last major tick position <= max_sec_ago
        end_major = int(np.floor(max_sec_ago / self.major_spacing)) * self.major_spacing
        
        major_ticks = []
        sec_ago = start_major
        while sec_ago <= end_major:
            # Convert "seconds ago" to actual time position
            tick_position = current_time - sec_ago
            if minVal <= tick_position <= maxVal:
                major_ticks.append(tick_position)
            sec_ago += self.major_spacing
        
        # Generate MINOR ticks at integer multiples of minor_spacing
        # Skip positions where major ticks exist
        minor_ticks = []
        if self.minor_spacing > 0:
            if min_sec_ago >= 0:
                start_minor = int(np.ceil(min_sec_ago / self.minor_spacing)) * self.minor_spacing
            else:
                start_minor = 0
            
            end_minor = int(np.floor(max_sec_ago / self.minor_spacing)) * self.minor_spacing
            
            sec_ago = start_minor
            while sec_ago <= end_minor:
                # Only add if it's NOT a major tick position
                if sec_ago % self.major_spacing != 0:
                    tick_position = current_time - sec_ago
                    if minVal <= tick_position <= maxVal:
                        minor_ticks.append(tick_position)
                sec_ago += self.minor_spacing
        
        # Return format: [(spacing_value, [tick_positions]), ...]
        # PyQtGraph draws major ticks with labels, minor ticks without
        return [
            (self.major_spacing, major_ticks),
            (self.minor_spacing, minor_ticks)
        ]
    
    def tickStrings(self, values, scale, spacing): 
        """
        Override to convert tick positions to "seconds ago" labels.
        This controls WHAT TEXT appears at each tick.
        """
        current_time = self.device_window.current_time 
        labels = [] 
        for v in values: 
            sec_ago = current_time - v
            if sec_ago % self.major_spacing == 0:
                labels.append(f"{int(round(sec_ago))}")  
            else: 
                labels.append("") 
        return labels

if __name__ == "__main__":
    app = QApplication([])
    window = DeviceViewWindow()
    window.resize(1000, 500)
    window.show()

    sys.exit(app.exec())