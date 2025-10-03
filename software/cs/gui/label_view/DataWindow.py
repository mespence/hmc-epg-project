import numpy as np
from numpy.typing import NDArray
import pandas as pd
import csv
import pandas as pd
from pandas import DataFrame

from pyqtgraph import (
    PlotWidget, ViewBox, PlotItem, setConfigOptions,
    TextItem, PlotDataItem, ScatterPlotItem, InfiniteLine,
    mkPen, mkBrush
)

from PyQt6.QtGui import (
    QKeyEvent, QWheelEvent, QMouseEvent, QColor, 
    QGuiApplication, QCursor, QAction
)
from PyQt6.QtCore import Qt, QPointF, QTimer, QObject, QEvent

from PyQt6.QtWidgets import (
    QVBoxLayout, QLabel, QDialog, QMessageBox, QMenu, QDialogButtonBox, QFileDialog
)

from settings import settings
from EPGData import EPGData
from utils.PanZoomViewBox import PanZoomViewBox
from label_view.LabelArea import LabelArea
from utils.CommentMarker import CommentMarker
from label_view.SelectionManager import Selection
from label_view.AddLabelManager import AddLabelManager
from utils.TextEdit import TextEdit
from utils.ResourcePath import resource_path

class DataWindow(PlotWidget):
    """
    Main widget for visualizing waveform recordings.

    Includes:
    - Zooming and panning (via `PanZoomViewBox`)
    - Interactive label areas
    - Transition lines
    - Baseline editing
    - Comment markers
    - Compression and zoom indicators

    Also handles data loading, rendering, and downsampling for performance.
    """
    def __init__(self, parent = None) -> None:
        """
        Initializes the DataWindow with plotting elements, UI overlays, and input handling.

        Parameters:
            epgdata (EPGData): The waveform and label data source.
        """
        # UI ITEMS
        super().__init__(parent = parent, viewBox=PanZoomViewBox())
        self.plot_item: PlotItem = self.getPlotItem() # the plotting canvas (axes, grid, data, etc.)
        self.plot_item.hideButtons()
        self.viewbox: PanZoomViewBox = self.plot_item.getViewBox() # the plotting area (no axes, etc.)
        self.viewbox.datawindow = self
        self.viewbox.menu = None  # disable default menu
        self.viewbox.sigRangeChanged.connect(self.update_plot)  # update plot on viewbox change

        settings.settingChanged.connect(self.on_setting_changed)

        # DATA
        self.epgdata: EPGData = self.parent().parent().epgdata
        self.file: str = None
        self.df = None
        self.init_df = None
        
        self.xy_data: list[NDArray] = [None, None]  # x and y data actually rendered to the screen
        self.curve: PlotDataItem = PlotDataItem(antialias=False, pen = settings.get("data_line_color")) 
        self.scatter: ScatterPlotItem = ScatterPlotItem(
            symbol="o", size=4, brush=settings.get("data_line_color")
        )  # the discrete points shown at high zooms
        self.initial_downsampled_data: list[NDArray, NDArray]  # cache of the dataset after the initial downsample
        self.zero_line = InfiniteLine(
            pos = 0,
            angle = 90,
            pen=mkPen(color='black', width=3),
            hoverPen=None,
            movable=False,
        )

        # CURSOR
        self.last_cursor_pos: QPointF = None # last cursor pos rel. to top left of application
        # self.cursor_mode: str = "normal"  # cursor state, e.g. normal, baseline selection

        # INDICATORS & LABELS
        self.compression: float = 0
        self.compression_text: TextItem = TextItem()
        self.zoom_level: float = 1
        self.zoom_text: TextItem = TextItem()
        #self.transitions: list[tuple[float, str]] = []   # the x-values of each label transition
        self.transition_mode: str = 'labels'
        self.labels: list[LabelArea] = []  # the list of LabelAreas

        # SELECTION
        self.selection: Selection = Selection(self)
        self.moving_mode: bool = False  # whether an interactive item is being moved
        self.edit_mode_enabled: bool = True  # whether the labels can be interacted with
        self.add_label_manager = AddLabelManager(self)

        # BASELINE
        self.baseline: InfiniteLine = InfiniteLine(
            angle = 0, movable=False, pen=mkPen("gray", width = 3)
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

        # COMMENTS
        self.comments: dict[float, CommentMarker] = {} # the dict of CommentMarkers
        self.comment_editing = False

        self.comment_preview: InfiniteLine = InfiniteLine(
            angle = 90, movable = False,
            pen=mkPen("gray", style = Qt.PenStyle.DashLine, width = 3),
        )

        self.addItem(self.comment_preview)
        self.comment_preview.setVisible(False)

        self.comment_preview_enabled: bool = False
        self.moving_comment: CommentMarker = None

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.initUI()

    def initUI(self) -> None:
        """
        Initializes plot appearance, UI layout, axes labels, and placeholder data.
        Called once during setup.
        """
        self.chart_width: int = 400
        self.chart_height: int = 400
        self.setGeometry(0, 0, self.chart_width, self.chart_height)

        #self.setBackground("white")
        #self.setTitle("<b>SCIDO Waveform Editor</b>", color="black", size="12pt")

        self.viewbox.setBorder(mkPen("black", width=3))

        self.plot_item.addItem(self.curve)
        self.plot_item.addItem(self.scatter)
        self.viewbox.addItem(self.zero_line)
        #self.plot_item.setLabel("bottom", "<b>Time [s]</b>", color="black")
        #self.plot_item.setLabel("left", "<b>Voltage [V]</b>", color="black")
        self.plot_item.showGrid(x=settings.get("show_v_grid"), y=settings.get("show_h_grid"))
        self.plot_item.layout.setContentsMargins(30, 30, 30, 20)
        self.plot_item.enableAutoRange(False)

        self.curve.setClipToView(False)  # already done in manual downsampling
        self.scatter.setVisible(False)
        self.curve.setZValue(-5)
        self.scatter.setZValue(-4)

        QTimer.singleShot(0, self.deferred_init)

        ## DEBUG/DEV TOOLS
        self.enable_debug = False
        self.debug_boxes = []

    def deferred_init(self) -> None:
        """
        Defers adding compression/zoom overlays until the scene is ready.
        """
        self.compression = 0
        self.compression_text = TextItem(
            text=f"Compression: {self.compression: .1f}", color="black", anchor=(0, 0)
        )
        self.compression_text.setPos(QPointF(80, 15))
        self.scene().addItem(self.compression_text)

        self.zoom_level = 1
        self.zoom_text = TextItem(
            text=f"Zoom: {self.zoom_level * 100}%", color="black", anchor=(0, 0)
        )
        self.zoom_text.setPos(QPointF(80, 30))
        self.scene().addItem(self.zoom_text)

        self.viewbox.setXRange(0,10)
        self.update_plot_theme()


    def update_plot_theme(self):
        plot_theme = settings.get("plot_theme") 
        self.setBackground(plot_theme["BACKGROUND"])

        self.setTitle("<b>Waveform Label Editor</b>", size="12pt", color=plot_theme["FONT_COLOR_1"])
        self.plot_item.setLabel("bottom", "<b>Time [s]</b>", color=plot_theme["FONT_COLOR_1"])
        self.plot_item.setLabel("left", "<b>Voltage [V]</b>", color=plot_theme["FONT_COLOR_1"])

        self.compression_text.setColor(plot_theme["FONT_COLOR_1"])
        self.zoom_text.setColor(plot_theme["FONT_COLOR_1"])

        self.plot_item.getAxis("left").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("bottom").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("right").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis("top").setPen(plot_theme["AXIS_COLOR"])
        self.plot_item.getAxis('top').setTicks([[]])  # disable ticks
        self.plot_item.getAxis('right').setTicks([[]])


        for label_area in self.labels:
            label_area.refreshColor()

        for _, comment in self.comments.items():
            comment.update_color()

        self.selection._update_default_style()

    def on_setting_changed(self, key: str, value):
        if key == "show_h_grid":
            self.plotItem.showGrid(y=value)
        elif key == "show_v_grid":
            self.plotItem.showGrid(x=value)
        elif key == "show_durations":
            for label in self.labels:
                label.set_duration_visible(value)
        elif key == "show_labels":
            for label_area in self.labels:
                label_area.setVisible(value)
                if value:
                    label_area.update_label_area()
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
        elif key == "label_colors":
            for label_area in self.labels:
                label_area.refreshColor()
                label_area.update_label_area()


        


    def checkForUnsavedChanges(self) -> bool:
        self.update_labels_column()
        if self.init_df is None:
            return True
        return self.init_df.equals(self.df)

    def closeEvent(self, event): # not using, use in main.py
        """
        Handles cleanup on window close.

        Ensures all pending data in the buffer is integrated into the
        full dataset, and saves a final backup of waveform and comment
        data before closing if the user hasn't saved since new data 
        was modified.

        Parameters:
            event (QCloseEvent): The close event triggered by the window system.
        """

        if not self.checkForUnsavedChanges(): # check if any new data or modifications
            print("self.df (should be new file), ", self.df.head())
            print("self.init_df (should be new file), ", self.init_df.head())
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Unsaved Changes in Label View")
            msg_box.setText("You have unsaved changes in Label View. Do you want to save them before exiting?")

            msg_box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            msg_box.setDefaultButton(QMessageBox.StandardButton.Save)

            reply = msg_box.exec()

            if reply == QMessageBox.StandardButton.Save:
                export_successful = self.export_df() 
                if not export_successful:
                    # export_df cancelled by the user, so cancel closing application
                    event.ignore()
                    return
            elif reply == QMessageBox.StandardButton.Discard:
                pass # proceed with closing w/o save
            else:
                event.ignore()
                return

        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        """
        Handles window resizing and updates compression indicator.
        """
        super().resizeEvent(event)
        if self.isVisible():
            self.update_plot()
        self.update_compression()

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

    def viewbox_to_window(self, point: QPointF) -> QPointF:
        """
        Converts between data (viewbox) coordinates and widget (screen) coordinates.

        Parameters:
            point (QPointF): Point in data coordinates.

        Returns:
            QPointF: The corresponding point in window coordinates.
        """
        return self.viewbox.mapViewToScene(point)
        # scene_pos = self.viewbox.mapViewToScene(QPointF(x, y))
        # return scene_pos.x(), scene_pos.y()
        # widget_pos = self.mapFromScene(scene_pos)
        # return widget_pos.x(), widget_pos.y()

    def reset_view(self) -> None:
        """
        Resets the plot to the full initial view, undoing all zoom/pan changes.
        """
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        
        if self.df is None:
            self.viewbox.setRange(xRange=(0,10))
            QGuiApplication.restoreOverrideCursor()
            return


        self.xy_data = [
            self.initial_downsampled_data[0].copy(), 
            self.initial_downsampled_data[1].copy()
        ]

        self.curve.setData(self.xy_data[0], self.xy_data[1])

    
        x_min, x_max = self.xy_data[0][0], self.xy_data[0][-1]
        y_min, y_max = np.min(self.xy_data[1]), np.max(self.xy_data[1])

        self.viewbox.setRange(
            xRange=(x_min, x_max), 
            yRange=(y_min, y_max), 
            padding=0
        )

        QGuiApplication.restoreOverrideCursor()

    def update_plot(self) -> None:
        """
        Redraws the waveform curve and label overlays after zoom/pan/data change.
        Also updates compression and zoom indicators.
        """
        if self.file is None or self.file not in self.epgdata.dfs:
            return  # no file displayed yet

        (x_min, x_max), _ = self.viewbox.viewRange()

        self.viewbox.setLimits(xMin=None, xMax=None, yMin=None, yMax=None) # clear stale data (avoids warning)

        self.downsample_visible(x_range=(x_min, x_max))

        x_data = self.xy_data[0]
        y_data = self.xy_data[1]
        self.curve.setData(x_data, y_data)
        if len(x_data) <= 500:
            self.scatter.setVisible(True)
            self.scatter.setData(x_data, y_data)
        else:
            self.scatter.setVisible(False)

        self.update_compression()
        self.update_zoom()
        for label_area in self.labels:
            # Cull to visible labels
            if label_area.start_time + label_area.duration < x_min:
                continue
            if label_area.start_time > x_max:
                continue

            # Don't render label areas <1 px wide
            # NOTE: this can lead to multiple sequential short labels all being
            # hidden, which can cause visible white regions, esp. when zoomed out.
            # Not sure if there is a good fix for this, but it's pretty minor
            left_px_loc = self.viewbox_to_window(QPointF(label_area.start_time,0)).x()
            right_px_loc = self.viewbox_to_window(QPointF(label_area.start_time + label_area.duration, 0)).x()
            label_width_px = right_px_loc - left_px_loc

            if label_width_px < 1:
                label_area.setVisible(False)     
                continue           

            label_area.setVisible(settings.get("show_labels"))
            label_area.update_label_area()

        self.viewbox.update()  # or anything that redraws

        if self.last_cursor_pos is not None:
            view_pos = self.window_to_viewbox(self.last_cursor_pos)
            self.selection.hover(view_pos.x(), view_pos.y())

        

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


    def update_zoom(self) -> None:
        """
        Updates the displayed zoom percentage based on current vs full-scale width.
        """
        plot_width = self.viewbox.geometry().width() * self.devicePixelRatioF()

        (x_min, x_max), _ = self.viewbox.viewRange()
        time_span = x_max - x_min

        pix_per_second = plot_width / time_span

        if time_span == 0:
            return float("inf")  # Avoid division by zero

        file_length_sec = self.df["time"].iloc[-1]
        default_pix_per_second = plot_width / file_length_sec

        self.zoom_level = pix_per_second / default_pix_per_second
        self.zoom_text.setText(f"Zoom: {self.zoom_level * 100: .0f}%")

        # self.zoom_level = 1 / float(self.compression) if self.compression != 0 else 1
        # if self.zoom_level < 0.5:
        #     precision = 2
        # if self.zoom_level < 1:
        #     precision = 1
        # else:
        #     precision = 3

        # value = round(self.zoom_level * 100, precision)
        # value = int(value) if value >= 1 else value
        # self.zoom_text.setText(f"Zoom: {value}%")

    def plot_recording(self, file: str) -> None:
        """
        Loads the time series and comments from a file and displays it.

        Parameters:
            file (str): File identifier.

        """
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))

        if self.labels: # clear previous labels, if any
            self.selection.deselect_all()
            for label_area in self.labels[::-1]:
                self.selection.delete_label_area(label_area)

        self.file = file
        times, volts = self.epgdata.get_recording(self.file)

        time, volt = self.epgdata.get_recording(self.epgdata.current_file)

        self.xy_data[0] = times
        self.xy_data[1] = volts

        self.downsample_visible()
        self.curve.setData(self.xy_data[0], self.xy_data[1])
        init_x, init_y = self.xy_data[0].copy(), self.xy_data[1].copy()
        self.initial_downsampled_data = [init_x, init_y]
        self.df = self.epgdata.dfs[file]  
    

        # create a comments column if doesn't yet exist in df
        if 'comments' not in self.df.columns:
            self.df['comments'] = None
            self.df['comments'] = self.df['comments'].astype(object)
        
        self.init_df = self.df.copy(deep = True)

        self.viewbox.setRange(
            xRange=(np.min(self.xy_data[0]), np.max(self.xy_data[0])), 
            yRange=(np.min(self.xy_data[1]), np.max(self.xy_data[1])), 
            padding=0
        )

        self.update_plot()
        self.plot_transitions(file)
        self.plot_comments(file)
        QGuiApplication.processEvents()
        QGuiApplication.restoreOverrideCursor()

    def plot_comments(self, file: str) -> None:
        """
        Adds existing comment markers from the data file to the viewbox.

        Parameters:
            file (str): File identifier.
        """
        
        comments_df = self.df[~self.df["comments"].isnull()]
        icon_path = resource_path("resources/icons/message.svg")
        for time, text in zip(comments_df["time"], comments_df["comments"]):
            marker = CommentMarker(time, text, self, icon_path=icon_path)
            self.comments[time] = marker

        return

    def add_comment_at_click(self, click_time: float) -> None:
        """
        Adds via a dialog popup.

        Parameters:
            event (QMouseEvent): The click event triggering comment placement.
        """

        # find nearest time clicked
        nearest_idx, comment_time = self.find_nearest_idx_time(click_time)
        existing = self.df.at[nearest_idx, 'comments']

        if pd.isna(existing) or str(existing).strip().lower() == "nan":
            existing = False

        
        # if there's already a comment at the time clicked, give an option to replace
        if existing and str(existing).strip():
            confirm = QMessageBox.question(
                self,
                "Overwrite Comment?",
                f"A comment already exists at {self.df.at[nearest_idx, 'time']:.2f}s:\n\n\"{existing}\"\n\nReplace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
            if confirm == QMessageBox.StandardButton.No:
                return

        # Create the dialog popup
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
            return None

        text = text.toPlainText().strip()
    
        # create a new comment
        self.df.at[nearest_idx, 'comments'] = text
        marker = self.comments.get(comment_time)
        if marker:
            # if overwriting, edit text
            marker.set_text(text)
        else:
            # new comment
            new_marker = CommentMarker(comment_time, text, self)
            self.comments[comment_time] = new_marker
        return

    def move_comment_helper(self, marker: CommentMarker):
        self.moving_comment = marker
        self.comment_preview_enabled = True
        self.comment_preview.setVisible(True)

        x_pos = self.viewbox.mapSceneToView(self.mapToScene(self.mapFromGlobal(QCursor.pos()))).x()
        self.comment_preview.setPos(x_pos)
        
        self.selection.deselect_all()
        self.selection.unhighlight_item(self.selection.hovered_item)
        self.viewbox.update()
        return
    
    def move_comment(self, marker: CommentMarker, click_time: float) -> None:
        new_idx, new_time = self.find_nearest_idx_time(click_time)
        old_time = marker.time
        text = self.comments[old_time].text

        # update df
        self.df.loc[self.df['time'] == old_time, 'comments'] = None
        self.df.at[new_idx, 'comments'] = text

        # update comments dict
        old_marker = self.comments.pop(old_time)
        old_marker.remove()
        new_marker = CommentMarker(new_time, text, self)
        self.comments[new_time] = new_marker

        marker.moving = False
        self.comment_preview_enabled = False
        self.comment_preview.setVisible(False)
        return

    def edit_comment(self, marker: CommentMarker, new_text: str) -> None:
        # chck func
        nearest_idx = self.find_nearest_idx_time(marker.time)[0]

        # update df
        self.df.at[nearest_idx, 'comments'] = new_text

        # update comments dict
        time = marker.time
        marker = self.comments[time]
        marker.text = new_text
        return

    def delete_comment(self, time: float) -> None:
        # update df
        self.df.loc[self.df["time"] == time, "comments"] = None

        # update dict
        marker = self.comments.pop(time)
        marker.remove()
        return

    def find_nearest_idx_time(self, time: float) -> tuple[int, float]:
        """ EDIT 
        returns tuple of int for idx and float for time 
        """ 
        nearest_idx = (self.df['time'] - time).abs().idxmin()
        comment_time = float(self.df.at[nearest_idx, 'time'])
        return (nearest_idx, comment_time)
    
    def export_txt(self):
        if not self.labels:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No Waveform labels")
            msg_box.setText("There are no waveforms labels to export from this viewing.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption="Export Waveforms As",
            filter="TXT Files (*.txt);;All Files (*)"
        )
        
        # BUG: if there is a blank area before the first label area, we need to append
        # the tbf for that with a blank label (maybe?). Right now the export just
        # makes it look like it starts with the first label, even if theres a gap.

        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                for label_area in self.labels:
                    tbf_str = f"{label_area.start_time + label_area.duration:.3f}"
                    indent = " " * (12 - len(tbf_str))
                    label = label_area.label
                    padding = " " * (12 - len(label) - 1)

                    f.write(f"\"{label}{padding}\"\n")
                    f.write(f"{indent}{tbf_str}\n")  # 4 spaces, 3 decimal places
    
    def export_comments(self):
        """ export comments in csv format """
        
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
        
    def downsample_visible(
        self, x_range: tuple[float, float] = None, max_points=4000, method = 'peak'
    ) -> tuple[NDArray, NDArray]:
        """
        Downsamples waveform data in the visible range using the selected method. Modifies self.xy_data in-place.

        Parameters:
            x_range (tuple[float, float]): Optional x-axis range to downsample.
            max_points (int): Max number of points to plot.
            method (str): 'subsample', 'mean', or 'peak' downsampling method.
        
        NOTE: 
            `subsample` samples the first point of each bin (fastest)
            `mean` averages each bin
            `peak` returns the min and max point of each bin (slowest, best looking)
        """
        x, y = self.epgdata.get_recording(self.file)

        # Filter to x_range if provided
        if x_range is not None:
            x_min, x_max = x_range

            left_idx = np.searchsorted(x, x_min, side="left")
            right_idx = np.searchsorted(x, x_max, side="right")

            if right_idx - left_idx <= 250: 
                # render additional point on each side at very high zooms
                left_idx = max(0, left_idx - 1)
                right_idx = min(len(x), right_idx + 1)
  
            x = x[left_idx:right_idx]
            y = y[left_idx:right_idx]   

    
        num_points = len(x)

        if num_points <= max_points or num_points < 2:  # no downsampling needed
            self.xy_data[0] = x
            self.xy_data[1] = y
            return
        

        if method == 'subsampling': 
            stride = num_points // max_points
            x_out = x[::stride]
            y_out = y[::stride]
        elif method == 'mean':
            stride = num_points // max_points
            num_windows = num_points // stride
            start_idx = stride // 2
            x_out = x[start_idx : start_idx + num_windows * stride : stride] 
            y_out = y[:num_windows * stride].reshape(num_windows,stride).mean(axis=1)
        elif method == 'peak':
            stride = max(1, num_points // (max_points // 2))  # each window gives 2 points
            num_windows = num_points // stride

            start_idx = stride // 2  # Choose a representative x (near center) for each window
            x_win = x[start_idx : start_idx + num_windows * stride : stride]
            x_out = np.repeat(x_win, 2)  # repeated for (x, y_min), (x, y_max)

            y_reshaped = y[: num_windows * stride].reshape(num_windows, stride)

            y_out = np.empty(num_windows * 2)
            y_out[::2] = y_reshaped.max(axis=1)
            y_out[1::2] = y_reshaped.min(axis=1)
        else:
            raise ValueError(
                'Invalid "method" arugment. ' \
                'Please select either "subsampling", "mean", or "peak".'
            )

        self.xy_data[0] = x_out
        self.xy_data[1] = y_out

    def plot_transitions(self, file: str) -> None:
        """
        Plots labeled regions from label transition data as colored areas on the plot.

        Parameters:
            file (str): File identifier.
        """
        # clear old labels if present
        for label_area in self.labels:
            self.plot_item.removeItem(label_area.area)
            self.plot_item.removeItem(label_area.transition_line)
            self.plot_item.removeItem(label_area.label_text)
            self.plot_item.removeItem(label_area.label_background)
            self.plot_item.removeItem(label_area.duration_text)
            self.plot_item.removeItem(label_area.duration_background)
            if self.enable_debug:
                self.plot_item.removeItem(label_area.label_debug_box)
                self.plot_item.removeItem(label_area.duration_debug_box)
            
        self.labels = []

        # load data
        times, _ = self.epgdata.get_recording(self.file)
        transitions = self.epgdata.get_transitions(self.file, self.transition_mode)

        # only continue if the label column contains labels
        if self.epgdata.dfs[file][self.transition_mode].isna().all():
            return
        
        durations = []  # elements of (label_start_time, label_duration, label)
        for i in range(len(transitions) - 1):
            time, label = transitions[i]
            next_time, _ = transitions[i + 1]
            durations.append((time, next_time - time, label))
        durations.append((transitions[-1][0], max(times) - transitions[-1][0], transitions[-1][1]))

        for i, (time, dur, label) in enumerate(durations):
            if label == None:
                continue
            label_area = LabelArea(time, dur, label, self) # init. also adds items to viewbox
            self.labels.append(label_area)
        
        self.update_right_transition_lines()
        self.update_plot()

    def update_right_transition_lines(self):
        """
        Shows all right transition lines of LabelAreas without a right neighbor,
        hides it otherwise.
        """
        for i, label_area in enumerate(self.labels):
            label_area.remove_right_transition_line()

            end_time = label_area.start_time + label_area.duration

            # Check if next label starts at this one's end
            has_adjacent_right = (
                i + 1 < len(self.labels) and 
                abs(self.labels[i + 1].start_time - end_time) < 1e-6  # within float error
            )

            if not has_adjacent_right:
                label_area.add_right_transition_line()


    def change_label_color(self, label: str, color: QColor) -> None:
        """
        Updates all label regions with the specified label to the new background color.

        Parameters:
            label (str): Label type to update.
            color (QColor): Color to set the label areas to.
        """
        for label_area in self.labels:
            if label_area.label == label:
                label_area.area.setBrush(mkBrush(color))
                label_area.update_label_area()

    def change_line_color(self, color: QColor) -> None:
        """
        Changes the waveform curve and scatter plot line color.

        Parameters:
            color (QColor): Color to set the curve and scatter plot to.
        """
        self.curve.setPen(mkPen(color))
        self.scatter.setPen(mkPen(color))  

    def set_durations_visible(self, visible: bool):
        """
        Sets the visibility of all label area durations.

        Parameters:
            visible (bool): Whether to show or hide the durations.
        """
        for label_area in self.labels:
            label_area.set_duration_visible(visible)
         

    def composite_on_white(self, color: QColor) -> QColor:
        """
        Helper function to convert a color with alpha into a RGB (no A) color as if shown on white.

        Parameters:
            color (QColor): Semi-transparent color to composite with white. 
        """
        r, g, b, a = color.getRgb()
        a = a / 255

        new_r = round(r * a + 255 * (1 - a))
        new_g = round(g * a + 255 * (1 - a))
        new_b = round(b * a + 255 * (1- a))
        return QColor(new_r, new_g, new_b)
        
    def get_closest_transition(self, x: float) -> tuple[int, float]:
        """
        Finds the transition line (left or right) closest to the given x-coordinate.

        Parameters:
            x (float): ViewBox x-coordinate.
        Returns:
            (InfiniteLine, float): Closest transition line and pixel distance.
        """
        if not self.labels:
            return None, float('inf')  # no labels present
        
        candidates = []
        for label in self.labels:
            # Add left/start transition line
            if label.transition_line is not None:
                candidates.append((label.transition_line, label.start_time))

            # (maybe) add right/end transition line
            if label.right_transition_line is not None:
                end_time = label.start_time + label.duration
                candidates.append((label.right_transition_line, end_time))

        # Compute distances and find closest
        closest_line = None
        closest_dist_px = float('inf')

        for line, t in candidates:
            dist_px = abs(self.viewbox_to_window(QPointF(t, 0)).x() - self.viewbox_to_window(QPointF(x, 0)).x())
            if dist_px < closest_dist_px:
                closest_line = line
                closest_dist_px = dist_px

        return closest_line, closest_dist_px
        
        # transitions = np.array([label_area.start_time for label_area in self.labels])
        # idx = np.searchsorted(transitions, x)

        # zero_point = self.viewbox_to_window(QPointF(0,0)).x()
        
        # if idx == len(transitions):
        #     transition = self.labels[idx-1].transition_line
        #     dist = abs(transitions[idx-1] - x)
        #     dist_px = self.viewbox_to_window(QPointF(dist, 0)).x() - zero_point
        #     return transition, dist_px
        
        # else:
        #     # Check which of the two neighbors is closer
        #     dist_to_left = abs(x - transitions[idx-1])
        #     dist_to_right = abs(transitions[idx] - x)

        #     if dist_to_left <= dist_to_right:
        #         transition = self.labels[idx-1].transition_line
        #         dist_px = self.viewbox_to_window(QPointF(dist_to_left, 0)).x()- zero_point
        #         return transition, dist_px
        #     else:
        #         transition = self.labels[idx].transition_line
        #         dist_px = self.viewbox_to_window(QPointF(dist_to_right, 0)).x()- zero_point
        #         return transition, dist_px
            
    def get_baseline_distance(self, y: float) -> float:
        """
        Returns the baseline and the pixel distance to it from a y-coordinate.

        Parameters:
            y (float): ViewBox y-coordinate.
        Returns:
            (InfiniteLine, float): Baseline and pixel distance.
        """
        if self.baseline is None:
            return float('inf')
        zero_point = self.viewbox_to_window(QPointF(0,0)).y()
        viewbox_distance = abs(y - self.baseline.value())
        return self.baseline, zero_point - self.viewbox_to_window(QPointF(0, viewbox_distance)).y()
    
    def get_closest_label_area(self, x: float) -> LabelArea:
        """
        Returns the LabelArea covering the given x position, or None if out of bounds.

        Parameters:
            x (float): ViewBox x-coordinate.
        Returns:
            LabelArea: Label area the x-coordinate is part of.
        """
        if not self.labels:
            return None

        if x < self.labels[0].start_time or x > (self.labels[-1].start_time + self.labels[-1].duration):
            return None  # outside the labels
        label_ends = np.array([label.start_time + label.duration for label in self.labels])
        idx = np.searchsorted(label_ends, x)  # idk why this works
        if idx >= len(self.labels):
            return self.labels[-1]
        return self.labels[idx]

    def delete_all_label_instances(self, label: str) -> None:
        """
        TODO: implement
        """

    def set_baseline(self, y_pos: float):
        """
        Sets a baseline at the clicked y-position and shows the baseline.

        Parameters:
            event (QMouseEvent): The click event triggering baseline placement.
        """
        self.baseline.setPos(y_pos)
        self.baseline.setVisible(True)

        self.baseline_preview_enabled = False
        self.baseline_preview.setVisible(False)

    def update_labels_column(self) -> None:
        """
        Recomputes the 'labels' column in the DataFrame from the current LabelAreas.

        Each LabelArea is assigned to all time rows >= start and < end,
        except for the final LabelArea, which is inclusive of the end time.

        """
        if self.df is None:
            return

        times = self.df["time"].values
        labels_array = np.full(len(times), np.nan, dtype=object)

        for i, area in enumerate(self.labels):
            start_time = area.start_time
            end_time = start_time + area.duration
            label = area.label

            start_idx = np.searchsorted(times, start_time, side="left")

            is_last_label = (i == len(self.labels) - 1)
            if is_last_label:
                end_idx = np.searchsorted(times, end_time, side="right")  # inclusive
            else:
                end_idx = np.searchsorted(times, end_time, side="left")   # exclusive

            if start_idx < end_idx:
                labels_array[start_idx:end_idx] = label

            # Only clear exact edge on non-final labels
            if not is_last_label and end_idx < len(times) and np.isclose(times[end_idx], end_time):
                labels_array[end_idx] = np.nan

        self.df["labels"] = labels_array

    def export_df(self) -> bool:
        filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption="Export Data As",
            filter="CSV Files (*.csv);;All Files (*)"
        )
        if not filename:
            return False
        
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self.init_df = self.df
        df = self.df

        if isinstance(df.index, pd.RangeIndex):
            df.to_csv(filename, index=True)
        else:
            df.to_csv(filename, index=False)

        QGuiApplication.restoreOverrideCursor()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handles key shortcuts for setting baseline ("B").
        Also forwards key events to the selection manager.

        Parameters:
            event (QKeyEvent): The key press event.
        """
        if event.key() == Qt.Key.Key_R:
            self.reset_view()  
        elif event.key() == Qt.Key.Key_L:
            self.add_label_manager.toggle()
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
            self.selection.deselect_all()
            self.selection.unhighlight_item(self.selection.hovered_item)
        elif event.key() == Qt.Key.Key_Escape and self.baseline_preview_enabled:
            self.baseline.setVisible(False)
            self.baseline_preview_enabled = False
            self.baseline_preview.setVisible(False)
        elif event.key() == Qt.Key.Key_S and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.export_df()
        elif event.key() == Qt.Key.Key_Up or event.key() == Qt.Key.Key_Down or event.key() == Qt.Key.Key_Left or event.key() == Qt.Key.Key_Right:
            self.viewbox.keyPressEvent(event)

        self.selection.key_press_event(event)
        self.viewbox.update()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        return

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        Delegates interaction to selection, baseline, and comment handlers based on state.

        Parameters:
            event (QMouseEvent): The mouse click event.
        """

        super().mousePressEvent(event)

        point = self.window_to_viewbox(event.position())
        x, y = point.x(), point.y()

        (x_min, x_max), (y_min, y_max) = self.viewbox.viewRange()

        if event.button() == Qt.MouseButton.LeftButton:
            if self.baseline_preview_enabled:
                if x_min <= x <= x_max and y_min <= y <= y_max:
                    self.set_baseline(y)
            elif self.add_label_manager.active:
                x = self.window_to_viewbox(event.position()).x()
                self.add_label_manager.mouse_press(x)
                return
            elif self.comment_preview_enabled and self.moving_comment is not None:
                if x_min <= x <= x_max and y_min <= y <= y_max:
                    self.move_comment(self.moving_comment, x)
                    self.moving_comment = None
            else:
                self.selection.mouse_press_event(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """
        Delegates interaction to selection, baseline, and comment handlers based on state.
        Parameters:
            event (QMouseEvent): The mouse release event.
        """

        super().mouseReleaseEvent(event)

        self.selection.mouse_release_event(event)

        if self.moving_mode:
            # if transition line was released, update data transition line
            if isinstance(self.selected_item, InfiniteLine) and self.selected_item is not self.baseline:
                transitions = [(label_area.start_time, label_area.label) for label_area in self.labels]
                self.epgdata.set_transitions(self.file, transitions, self.transition_mode)
            return
        elif self.add_label_manager.active:
            x = self.window_to_viewbox(event.position()).x()
            self.add_label_manager.mouse_release(x)
            return

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        return
        if event.button() == Qt.MouseButton.LeftButton:
            self.handle_labels(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Delegates interaction to selection, baseline, and comment handlers based on state.

        Parameters:
            event (QMouseEvent): The mouse move event.
        """
        super().mouseMoveEvent(event)
        self.last_cursor_pos

        point = self.window_to_viewbox(event.position())
        x, y = point.x(), point.y()

        (x_min, x_max), (y_min, y_max) = self.viewbox.viewRange()

        if self.baseline_preview_enabled:
            if y_min <= y <= y_max:
                self.baseline_preview.setPos(y)
                self.baseline_preview.setVisible(True)
            else:
                self.baseline_preview.setVisible(False)
        elif self.add_label_manager.active:
            x = self.window_to_viewbox(event.position()).x()
            self.add_label_manager.mouse_move(x)
            return
        elif self.comment_preview_enabled:
            if x_min <= x <= x_max:
                self.comment_preview.setPos(x)
                self.comment_preview.setVisible(True)
            else:
                self.comment_preview.setVisible(False)
        else:
            self.selection.mouse_move_event(event)

        return
    

    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        Forwards a scroll event to the custom viewbox.
        """
        self.viewbox.wheelEvent(event)
        event.ignore()