import re
import os
import sys
import csv
import datetime
import threading
import numpy as np
import pandas as pd
from pandas import DataFrame
from numpy.typing import NDArray

from pyqtgraph import PlotWidget, PlotItem, ScatterPlotItem, PlotDataItem, mkPen, InfiniteLine, TextItem

from PyQt6.QtCore import QTimer, Qt, QPointF
from PyQt6.QtGui import QWheelEvent, QMouseEvent, QCursor, QKeyEvent, QGuiApplication
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QMessageBox, QFileDialog

from utils.PanZoomViewBox import PanZoomViewBox
from utils.CommentMarker import CommentMarker
from utils.TextEdit import TextEdit
from utils.ResourcePath import resource_path
from settings import settings

class LiveDataWindow(PlotWidget):
    """
    Widget for visualizing real-time waveform data streams with live updating.

    Features:
    - Displays continuous data from an incoming stream.
    - Supports live auto-scrolling or paused manual scrolling.
    - Interactive baseline setting and comment annotations.
    - Zooming and panning via custom PanZoomViewBox.
    - Data downsampling for performance rendering.
    - Periodic auto-backup of waveform and comments.
    - Export functionality for waveform data and comments.
    """
    def __init__(self, recording_settings = None, parent = None):
        """
        Initializes the LiveDataWindow widget.

        Sets up plotting area/custom viwebox and UI elements,
        thread-safe data buffers, live rendering timer at ~60 Hz, 
        periodiic auto-backup.
        """
        # --- GENERAL INIT ITEMS ---
        super().__init__(parent = parent, viewBox=PanZoomViewBox(datawindow=self))
        self.setMinimumWidth(300)

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

        # --- INITIAL SETTINGS ---
        if recording_settings:
            self.recording_filename = recording_settings.get("filename")
            self.min_voltage = recording_settings.get("min_voltage")
            self.max_voltage = recording_settings.get("max_voltage")
        else:
            self.recording_filename = None
            self.min_voltage = settings.get("default_min_voltage")
            self.max_voltage = settings.get("default_max_voltage")
        self.viewbox.setYRange(self.min_voltage, self.max_voltage, padding=0)
        self.base_data_directory = settings.get("default_recording_directory")

        # --- PERIODIC BACKUP SETTINGS ---
        # backups stored wherever .exe opened
        self.periodic_backup_dir = settings.get("backup_recording_directory")

        # base names for the backup files
        self.waveform_backup_base = "waveform_backup.csv"
        self.comments_backup_base = "comments_backup.csv"

        # store the active filenames, updated after each save with utc time stamp
        self.waveform_backup_path = os.path.join(self.periodic_backup_dir, self.waveform_backup_base)
        self.comments_backup_path = os.path.join(self.periodic_backup_dir, self.comments_backup_base)

        self.backup_renamed = False

        self.last_saved_data_index = 0 # track how much waveform data has been saved for backup

        # initialize CSV headers if files don't exist
        if not os.path.exists(self.waveform_backup_path):
            pd.DataFrame(columns=['time', 'voltage']).to_csv(self.waveform_backup_path, index=False)
        if not os.path.exists(self.comments_backup_path):
            pd.DataFrame(columns=['time', 'comment']).to_csv(self.comments_backup_path, index=False)

        self.save_lock = threading.Lock() # to prevent concurrent writes
        self.is_saving = False # flag for ongoing background save

        self.data_modified = False # flag for new data to append

        # timer for periodic background data saving (~5 sec)
        self.save_timer = QTimer(self)
        self.save_timer.setInterval(5000)
        self.save_timer.timeout.connect(self.trigger_periodic_save)
        self.save_timer.start()


        # --- DATA STORAGE ---
        # holds all historical data
        self.epgdata = self.parent().parent().epgdata
        self.xy_data: list[NDArray] = [np.array([]), np.array([])]

        # temporary buffer for incoming data, to be added to full xy_data every plot update
        self.buffer_data: list[tuple[float, float]] = []
        self.buffer_lock = threading.Lock() # lock to prevent data loss

        # store currently rendered data (downsampled for display)
        self.xy_rendered: list[NDArray] = [np.array([]), np.array([])]

        # track last rendered state to optimize plot updates
        self.last_rendered_x_range: tuple[float, float] = (0, 0)
        
        # timer for plot updates (~60 fps)
        self.plot_update_timer = QTimer(self)
        self.plot_update_timer.setInterval(int(1000/60))
        self.plot_update_timer.timeout.connect(self.timed_plot_update)

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
        self.setTitle("<b>Live Waveform Viewer<b>", color="black", size="12pt")
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
        # for live view, follow only 10 seconds of visible data
        self.default_scroll_window = 10
        self.auto_scroll_window = 10

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

        # --- COMMENTS ---
        self.comments: dict[float, CommentMarker] = {} # the dict of Comments
        self.comment_editing = False

        # comment preview only for moving comment
        self.comment_preview: InfiniteLine = InfiniteLine(
            angle = 90, movable = False,
            pen=mkPen("gray", style = Qt.PenStyle.DashLine, width = 3),
        )
        self.addItem(self.comment_preview)
        self.comment_preview.setVisible(False)
        self.comment_preview_enabled: bool = False
        self.moving_comment: CommentMarker = None
        
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.update_plot_theme()
        self.update_plot()


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

    def trigger_periodic_save(self):
        """
        Periodically triggers a background save of waveform and comment data.

        Actual saving is offloaded to a background thread.
        """
        if not self.is_saving and self.data_modified:
            save_thread = threading.Thread(target=self.periodic_save_in_background, daemon=True)
            save_thread.start()

    def periodic_save_in_background(self):
        """
        Performs a periodic backup save in a background thread.

        Saves:
            - Waveform data to CSV (appends new data)
            - Comment data to separate CSV (rewrites each time)

        Ensures filenames are updated with recent UTC timestamp.
        """
        
        with self.save_lock:
            self.is_saving = True
            
            # want stable snapshot
            times = self.xy_data[0].copy()
            volts = self.xy_data[1].copy()
            comments = self.comments.copy()
        
            try:
                current_utc_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
                df_to_append = pd.DataFrame({'time': times[self.last_saved_data_index:], 'voltage': volts[self.last_saved_data_index:]})
                comments_list = [{'time': t, 'comment': c.text} for t, c in comments.items()]

                if not df_to_append.empty:
                    df_to_append.to_csv(self.waveform_backup_path, mode='a', header=False, index=False)
                    new_waveform_filename = os.path.join(
                        self.periodic_backup_dir, f"{self.waveform_backup_base}_{current_utc_time}.csv"
                    )
                    os.rename(self.waveform_backup_path, new_waveform_filename)
                    self.waveform_backup_path = new_waveform_filename
                    self.last_saved_data_index = len(times)
                    if not self.backup_renamed:
                        self.backup_renamed = True
                
                if comments_list:
                    comments_df = pd.DataFrame(comments_list, columns=['time', 'comment'])
                    comments_df.to_csv(self.comments_backup_path, mode='w', header=True, index=False)

                    # rename file and path
                    
                    new_comments_filename = os.path.join(
                        self.periodic_backup_dir, f"{self.comments_backup_base}_{current_utc_time}.csv"
                    )
                    os.rename(self.comments_backup_path, new_comments_filename)
                    self.comments_backup_path = new_comments_filename

                self.data_modified = False
                
            except Exception as e:
                print(f"[PERIODIC SAVE ERROR] Could not save data: {e}")
                print(len(times), len(volts))
            finally:
                self.is_saving = False

    def update_plot(self):
        """
        Redraws the waveform on the screen if live mode is enabled
        or the user manually adjusts the viewbox.

        - Computes the current view range from the ViewBox.
        - Downsamples data if zoomed out.
        - Updates the curve with new x and y values.
        - Enables scatter for zoom levels greater than 300%
        - Avoids unnecessary re-renders if the view hasn't changed.
        """
        current_x_range, _ = self.viewbox.viewRange()

        rerender = False
        if self.live_mode:
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

        if self.live_mode:
            end = self.current_time
            start = end - self.auto_scroll_window
            offset = 0.1 # when zoomed in, leading line lags with plotting so need offset to keep hidden
            self.viewbox.setXRange(start, end, padding=0)
            self.downsample_visible(self.xy_data, x_range=(start, end))
            self.leading_line.setPos(end+offset)
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

    def set_live_mode(self, enabled: bool):
        """
        Enables or disables live auto-scrolling mode.

        Parameters:
            enabled (bool): True to enable live mode; False to pause.
        """
        self.live_mode = enabled
        self.update_plot()
        return
    
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

            # if receive another mismatched shape error try this
                # # Safely calculate number of full windows
                # stride = max(1, num_points // (max_points // 2))
                # num_windows = num_points // stride
                # total_pts = num_windows * stride

                # # Slice to full window size
                # x_window = x[:total_pts]
                # y_window = y[:total_pts]

                # x_win = x_window[stride // 2::stride][:num_windows]  # in case of rounding issues
                # y_reshaped = y_window.reshape(num_windows, stride)

                # # Now generate x and y downsampled
                # x_out = np.repeat(x_win, 2)
                # y_out = np.empty(num_windows * 2)
                # y_out[::2] = y_reshaped.max(axis=1)
                # y_out[1::2] = y_reshaped.min(axis=1)
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

    def add_comment_dialog(self, comment_time: float) -> str | None:
        """
        Opens a modal dialog to input a new comment for the given time.

        Parameters:
            comment_time (float): Timestamp at which to add the comment.

        Returns:
            str or None: The entered comment text if non-empty and accepted;
                         otherwise None if cancelled or empty.
        """

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Add Comment @ {comment_time:.2f}s")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Add Comment:"))
        text = TextEdit()
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        # enter pressed, dialog accepts
        text.returnPressed.connect(dialog.accept)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # if the text was just spaces/an empty comment, then don't create a comment
        text = text.toPlainText().strip()
        return text if text else None

    def add_comment_at_click(self, click_time: float) -> None:
        """
        Adds a comment at the nearest valid past time to the clicked time.
        Opens a dialog for text input, then creates a CommentMarker on
        acceptance.

        Parameters:
            click_time (float): The clicked timestamp to add comment near.
        """
        comment_time = self.find_nearest_time(click_time)

        text = self.add_comment_dialog(comment_time)
        if text is None:
            return
    
        # create comment
        new_marker = CommentMarker(comment_time, text, self)

        self.comments[comment_time] = new_marker
        self.update_plot()

        self.data_modified = True
    
    def add_comment_live(self) -> None:
        """
        Adds a comment at the current live time position. Opens a dialog
        to enter comment text and creates a CommentMarker if accepted.
        """
        # called when click add comment button or shift+Space when in live/paused mode

        # have live view paused in background
        comment_time = self.current_time
        text = self.add_comment_dialog(comment_time)
        if text is None:
            return
    
        # create comment
        # commentmarker handles viisbility out of range
        new_marker = CommentMarker(comment_time, text, self)
        self.comments[comment_time] = new_marker

        self.update_plot()

        self.data_modified = True

    def move_comment_helper(self, marker: CommentMarker):
        """
        Prepares the interface for moving a comment marker.
        Displays a preview vertical line at the mouse cursor position
        and sets internal state to enable moving mode.

        Parameters:
            marker (CommentMarker): The comment marker to move.
        """
        self.moving_comment = marker
        self.comment_preview_enabled = True
        self.comment_preview.setVisible(True)

        x_pos = self.viewbox.mapSceneToView(self.mapToScene(self.mapFromGlobal(QCursor.pos()))).x()
        self.comment_preview.setPos(x_pos)
        
        self.viewbox.update()
        return
    
    def move_comment(self, marker: CommentMarker, click_time: float) -> None:
        """
        Moves an existing comment marker to a new time near the clicked
        position. Removes the old marker and creates a new one at the
        updated timestamp.

        Parameters:
            marker (CommentMarker): The comment marker to move.
            click_time (float): The new timestamp to move the comment to.
        """
        old_time = marker.time
        text = self.comments[old_time].text

        # update marker in viewbox
        # update comments dict
        old_marker = self.comments.pop(old_time)
        old_marker.remove()
        new_time = self.find_nearest_time(click_time)
        new_marker = CommentMarker(new_time, text, self)
        self.comments[new_time] = new_marker

        self.comment_preview_enabled = False
        self.comment_preview.setVisible(False)

        marker.moving = False
        self.data_modified = True
        return
    
    def edit_comment(self, marker: CommentMarker, new_text: str) -> None:
        """
        Updates the text of an existing comment marker.

        Parameters:
            marker (CommentMarker): The marker to update.
            new_text (str): The new comment text.
        """
        time = marker.time
        marker = self.comments[time]
        marker.text = new_text
        self.data_modified = True
        return
    
    def delete_comment(self, time: float) -> None:
        """
        Deletes the comment marker at the specified time.
        Removes the marker from internal storage and the plot.

        Parameters:
            time (float): The timestamp of the comment to delete.
        """
        # update dict
        marker = self.comments.pop(time)
        # remove marker from viewbox
        marker.remove()
        self.data_modified = True
        return
    
    def find_nearest_time(self, time: float) -> float:
        """
        Finds the nearest valid time point to the specified time in the data.
        Used for snapping comments to existing data points.

        Parameters:
            time (float): The target timestamp.

        Returns:
            float: The nearest timestamp available in the data.
        """
        # xy rendered sorted in downsampling
        # find insertion point
        x = self.xy_rendered[0]
        idx = np.searchsorted(x, time)

        # find nearest point
        if idx == 0:
            nearest_idx = 0
        elif idx >= len(x):
            nearest_idx = len(x) - 1
        else:
            left = x[idx - 1]
            right = x[idx]
            if abs(right - time) < abs(time - left):
                nearest_idx = idx
            else:
                nearest_idx = idx - 1

        nearest_time = x[nearest_idx]
        return float(nearest_time)

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

    def plot_recording(self, file: str):
        """
        Loads the time series and comments from a file and displays it.

        Parameters:
            file (str): File identifier.
        """
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self.file = file
        times, volts = self.epgdata.get_recording(self.file)
        self.xy_data[0] = times
        self.xy_data[1] = volts
        self.downsample_visible(self.xy_data)
        #init_x, init_y = self.xy_data[0].copy(), self.xy_data[1].copy()
        self.curve.setData(self.xy_data[0], self.xy_data[1])
        #self.initial_downsampled_data = [init_x, init_y]
        self.df = self.epgdata.dfs[file]  

        self.viewbox.setRange(
            xRange=(np.min(self.xy_data[0]), np.max(self.xy_data[0])), 
            yRange=(np.min(self.xy_data[1]), np.max(self.xy_data[1])), 
            padding=0
        )

        # create a comments column if doesn't yet exist in df
        if 'comments' not in self.df.columns:
            self.df['comments'] = None

        self.current_time = self.df['time'].iloc[-1]
        
        self.update_plot()
        self.plot_comments(file)
        QGuiApplication.processEvents()
        QGuiApplication.restoreOverrideCursor()


    def plot_comments(self, file: str) -> None:
        """
        Adds existing comment markers from the data file to the viewbox.

        Parameters:
            file (str): File identifier.
        """
        if file is not None:
            self.file = file

        for marker in self.comments:
            marker.remove()
        self.comments.clear()
        
        comments_df = self.df[~self.df["comments"].isnull()]
        icon_path = resource_path("resources/icons/message.svg")
        for time, text in zip(comments_df["time"], comments_df["comments"]):
            marker = CommentMarker(time, text, self, icon_path=icon_path)
            self.comments[time] = marker
        
        return



    def export_comments(self):
        """
        Exports all current comments to a CSV file. Prompts user to select
        a file location and writes comment times and texts to the file.
        Shows a message box if no comments exist.
        """
        
        if not self.comments:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No Comments")
            msg_box.setText("There are no comments to export from this live viewing.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            return

        filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption="Export Comments As",
            filter="CSV Files (*.csv);;All Files (*)"
        )

        if filename:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['comment_time', 'comment_text'])
                for time, marker in self.comments.items():
                    writer.writerow([time, marker.text])
        
        return
        
    def export_df(self) -> bool:
        """
        Exports the current waveform data and associated comments to a
        CSV file. Prompts the user to select a file location. If no
        data is available, shows a message box informing the user.
        """
        self.integrate_buffer_to_np()

        if not len(self.xy_data[0]) > 0:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No Data")
            msg_box.setText("There is no data to export from this live viewing.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            return False
        
        filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption="Export Data As",
            directory=self.recording_filename,
            filter="CSV Files (*.csv);;All Files (*)"
        )

        if not filename:
            return False

        times = np.round(self.xy_data[0], 4)
        volts = self.xy_data[1]
        
        df = DataFrame({
            "time": times,
            "voltage": volts, # may need to change based on what engineers plot
            "comments": [None] * len(times)
        })

        # add current comments to df
        for comment_time, comment in self.comments.items():
            snapped_time = self.find_nearest_time(comment_time)
            df.loc[df['time'] == snapped_time, 'comments'] = comment.text

        df.to_csv(filename)
        self.data_modified = False
        
        return True

    def save_df(self) -> bool:
        """
        Saves the current waveform data and associated comments to
        CSV file. Uses default file location selected for new recording. If no
        data is available, shows a message box informing the user.
        Returns a bool if the save was successful.
        """
        self.integrate_buffer_to_np()

        if not len(self.xy_data[0]) > 0:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No Data")
            msg_box.setText("There is no data to save from this live viewing.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            return False

        if not self.recording_filename: # if no filename, prompt a filename and save loc
            filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption="Save Data As",
            filter="CSV Files (*.csv);;All Files (*)"
            )

            if not filename:
                return False

            self.recording_filename = filename # set filename so save only occurs once
        
        times = np.round(self.xy_data[0], 4)
        volts = self.xy_data[1]
        
        df = DataFrame({
            "time": times,
            "voltage": volts,
            "comments": [None] * len(times)
        })

        # add current comments to df
        for comment_time, comment in self.comments.items():
            snapped_time = self.find_nearest_time(comment_time)
            df.loc[df['time'] == snapped_time, 'comments'] = comment.text

        try:
            df.to_csv(self.recording_filename) 
            self.data_modified = False
            print(f"Data successfully saved to: {self.recording_filename}")
            return True
        except Exception as e:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Save Error")
            msg_box.setText(f"An error occurred while saving the data: {e}")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            print(f"Error saving DataFrame: {e}")
            return False

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
            elif self.comment_preview_enabled and self.moving_comment is not None:
                self.move_comment(self.moving_comment, x)
                self.moving_comment = None
            self.update_plot()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handles key press events for shortcuts.

        - Shift+Space to add a comment at the current time.
        - "B" key to toggle baseline preview mode.

        Parameters:
            event (QKeyEvent): The key press event.
        """
        if event.key() == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.add_comment_live()
        elif event.key() == Qt.Key.Key_B:
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
        elif self.comment_preview_enabled:
            if x_min <= x <= x_max:
                self.comment_preview.setPos(x)
                self.comment_preview.setVisible(True)
            else:
                self.comment_preview.setVisible(False)

        #self.update_plot()
        return

    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        Forwards mouse wheel scroll events to the custom PanZoomViewBox for
        zooming behavior.

        Parameters:
            event (QWheelEvent): The wheel scroll event.
        """
        self.viewbox.wheelEvent(event)
        
if __name__ == "__main__":
    app = QApplication([])
    window = LiveDataWindow()
    window.resize(1000, 500)
    window.show()
    sys.exit(app.exec())