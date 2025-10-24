from pyqtgraph import ViewBox, InfiniteLine

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QWheelEvent, QAction, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QMenu
from settings import settings
from label_view.LabelArea import LabelArea

class PanZoomViewBox(ViewBox):
    """
    Custom ViewBox that overrides default mouse/scroll behavior to support
    pan and zoom using wheel + modifiers. Supports live mode locking
    and boundary-limited zooming.

    Pan/Zoom behavior:
    - Ctrl + Scroll: horizontal/vertical zoom (with Shift)
    - Scroll only: pan (horizontal or vertical based on Shift)
    - Arrow keys: zoom (left/right: x, up/down : y)
    - Right-click: custom label/comment context menu
    - Drag: disabled (reserved for selection tools)
    """

    def __init__(self, datawindow = None) -> None:
        """
        Initialize the custom ViewBox.

        Parameters:
            datawindow: Reference to the parent DataWindow.
        """
        super().__init__()
        self.datawindow = None
        self.zoom_viewbox_limit: float = 0.5

    def wheelEvent(self, event: QWheelEvent, axis=None) -> None:
        """
        Handle mouse wheel scroll events for zooming and panning based
        on modifier keys.

        - Ctrl + Shift: vertical zoom
        - Ctrl: horizontal zoom
        - Shift: vertical pan
        - No modifiers: horizontal pan

        Parameters:
            event (QWheelEvent): The wheel event object.
            axis: Not used, kept for compatibility.
        """

        if self.datawindow is None:
            self.datawindow = self.parentItem().getViewWidget()
        
        delta = event.angleDelta().y()
        x_delta = event.angleDelta().x()
        modifiers = event.modifiers()
        live = getattr(self.datawindow, "live_mode", False)

        ctrl_held = modifiers & Qt.KeyboardModifier.ControlModifier
        shift_held = modifiers & Qt.KeyboardModifier.ShiftModifier

        if ctrl_held:
            # zoom
            zoom_factor = 1.001**delta
            center = self.mapToView(event.position())
    
            if shift_held: 
                # y zoom
                self.scaleBy((1, 1 / zoom_factor), center)
            else:
                # x zoom
                if getattr(self.datawindow, "window_type", "") == "device_view":
                    return #disable in device_view
                self.x_zoom(live, zoom_factor, center)
        else:
            # pan
            (x_min, x_max), (y_min, y_max) = self.viewRange()
            width, height = x_max - x_min, y_max - y_min

            if shift_held:
                # y pan
                v_zoom_factor = 5e-4
                dy = delta * v_zoom_factor * height
                self.translateBy(y=dy)
            elif abs(x_delta) > abs(delta):  # trackpad horizontal swipe
                if not live and getattr(self.datawindow, "window_type", "") != "device_view":
                    h_zoom_factor = 2e-4
                    dx = -x_delta * h_zoom_factor * width  # note: negative for natural direction

                    new_x_min = x_min + dx
                    new_x_max = x_max + dx
                    x_min_limit, x_max_limit = self.get_pan_limits(width)

                    if new_x_min < x_min_limit:
                        self.setXRange(x_min_limit, x_min_limit + width, padding=0)
                    elif new_x_max > x_max_limit:
                        self.setXRange(x_max_limit - width, x_max_limit, padding=0)
                    else:
                        self.translateBy(x=dx)
            else:
                # x pan (disabled during live mode)
                if not live:
                    h_zoom_factor = 2e-4
                    dx = delta * h_zoom_factor * width

                    new_x_min = x_min + dx
                    new_x_max = x_max + dx
                    x_min_limit, x_max_limit = self.get_pan_limits(width)

                    # prevent panning if x=0 moves past 80% from left edge
                    if new_x_min < x_min_limit:
                        self.setXRange(x_min_limit, x_min_limit + width, padding=0)
                    elif new_x_max > x_max_limit:
                        self.setXRange(x_max_limit - width, x_max_limit, padding=0)
                    else:
                        self.translateBy(x=dx)

        self.datawindow.update_plot()
        self.datawindow.update_compression() # if not done in plot already
        event.accept()

    def x_zoom(self, live, zoom_factor, center) -> None:
        """
        Perform horizontal zoom. Behavior differs between live
        and paused modes.

        Parameters:
            live (bool): Whether live mode is enabled.
            zoom_factor (float): The zoom multiplier.
            center (QPointF): The point (in data coordinates)
                              around which to zoom.
        """
        (x_min, x_max), _ = self.viewRange()
        current_span = x_max - x_min
        if live:
            new_span = current_span / zoom_factor
            self.datawindow.auto_scroll_window = new_span
        else:
            center_x = center.x()

            
            new_width = current_span / zoom_factor
            if new_width < 1: # dont zoom less than 1 sec
                return
            
            # ensure 0 stays within 80% of viewbox limit
            new_x_min = center_x - (center_x - x_min) / zoom_factor
            new_x_max = new_x_min + new_width
            x_min_limit, x_max_limit = self.get_pan_limits(new_width)

            # Enforce both pan limits
            if new_x_min < x_min_limit:
                self.setXRange(x_min_limit, x_min_limit + new_width, padding=0)
            elif new_x_max > x_max_limit:
                self.setXRange(x_max_limit - new_width, x_max_limit, padding=0)
            else:
                self.scaleBy((1 / zoom_factor, 1), center)

    def get_pan_limits(self, view_width: float) -> tuple[float, float]:
        """
        Compute the min and max x-range allowed based on pan constraints.

        Parameters:
            view_width (float): Width of the current view.

        Returns:
            (x_min_limit, x_max_limit): bounds for allowed panning
        """
        left_limit = -self.zoom_viewbox_limit * view_width 

        if self.datawindow:
            if hasattr(self.datawindow, "df"):
                df = self.datawindow.df
                if df is not None and df.shape[0] > 0:
                    data_max = self.datawindow.df["time"].iloc[-1]
                    right_limit = data_max + self.zoom_viewbox_limit * view_width
                else:
                    right_limit = float("inf")
            else:
                xy_data = self.datawindow.xy_data
                if xy_data[0].shape[0] > 0:
                    data_max = self.datawindow.xy_data[0][-1]    
                    right_limit = data_max + self.zoom_viewbox_limit * view_width
                else:
                    right_limit = float("inf")
        else:
            right_limit = float("inf")  # fail open

        return left_limit, right_limit

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handle keyboard input for zooming with arrow keys.

        Parameters:
            event (QKeyEvent): The key press event.
        """

        live = getattr(self.datawindow, "live_mode", False)
        center = self.viewRect().center()

        zoom_factor_in = 1.1
        zoom_factor_out = 0.9

        if event.key() == Qt.Key.Key_Up:
            # y zoom in
            self.scaleBy((1, 1 / zoom_factor_in), center)
        elif event.key() == Qt.Key.Key_Down:
            # y zoom out
            self.scaleBy((1, 1 / zoom_factor_out), center)
        elif event.key() == Qt.Key.Key_Left:
            # x zoom out
            self.x_zoom(live, zoom_factor_out, center)
        elif event.key() == Qt.Key.Key_Right:
            # x zoom in
            self.x_zoom(live, zoom_factor_in, center)

        self.datawindow.update_plot()

    def mouseDragEvent(self, event, axis=None) -> None:
        """
        Disables default drag-to-pan behavior (drag is used for selections).
        """
        event.ignore()

    def contextMenuEvent(self, event):
        """
        Custom context menu for label editing and comment creation.

        Features:
        - Change label type submenu
        - Add comment option

        Disabled in live mode and device view mode.
        """
        if self.datawindow is None:
            self.datawindow = self.parentItem().getViewWidget()

        live = getattr(self.datawindow, "live_mode", False)
        device_view = getattr(self.datawindow, "window_type", "") == "device_view"


        if live or device_view:
            event.ignore()
            return
        
        scene_pos = event.scenePos()
        data_pos = self.mapSceneToView(scene_pos)
        x = data_pos.x()  
        if hasattr(self.datawindow, "selection"):
            item = self.datawindow.selection.hovered_item

            if isinstance(item, InfiniteLine):
                print('Right-clicked InfiniteLine')
                return  # TODO: infinite line context menu not yet implemented

            elif isinstance(item, LabelArea):
                self.label_area_menu(event, item, x)
            
            elif item is None:
                self.default_menu(event, x)
        else:
            self.default_menu(event, x)


    def default_menu(self, event, x: float):
        menu = QMenu()
        add_comment = QAction("Add Comment", menu)

        menu.addAction(add_comment)

        selected_action = menu.exec(event.screenPos())           
        if selected_action == add_comment:
            self.datawindow.add_comment_at_click(x)
        else:
            pass
    

    def label_area_menu(self, event, label_area: LabelArea, x: float):
        menu = QMenu()

        add_comment = QAction("Add Comment", menu)

        label_type_dropdown = QMenu("Change Waveform Type", menu)
        label_names = list(settings.get("label_colors").keys())
        for label in label_names:            
            action = QAction(label, menu)
            action.setCheckable(True)

            if label_area.label == label:
                action.setChecked(True)
                
            action.triggered.connect(
                lambda checked, label_area=label_area, label=label:
                (self.datawindow.selection.change_label_type(label_area, label),
                 label_area.update_label_area())
            )
        
            label_type_dropdown.addAction(action)

        snap_left = QAction("Snap Waveform Left", menu)
        snap_left.setShortcut(QKeySequence("Ctrl+["))
        snap_right = QAction("Snap Waveform Right", menu)
        snap_right.setShortcut(QKeySequence("Ctrl+]"))

        # Find which (if any) snaps to disable
        idx = self.datawindow.labels.index(label_area)
        left_touching = False
        right_touching = False

        if idx > 0:
            left_label = self.datawindow.labels[idx - 1]
            left_end = left_label.start_time + left_label.duration
            if abs(left_end - label_area.start_time) < 1e-4:
                left_touching = True
        else:
            left_end = 0
            left_touching = False

        if idx < len(self.datawindow.labels) - 1:
            right_start = self.datawindow.labels[idx + 1].start_time
            this_end = label_area.start_time + label_area.duration
            if abs(right_start - this_end) < 1e-4:
                right_touching = True
        else:
            right_start = self.datawindow.df['time'].iloc[-1] if not self.datawindow.df.empty else None # end of data

        snap_left.setEnabled(not left_touching)
        snap_right.setEnabled(not right_touching)

        menu.addAction(add_comment)
        menu.addMenu(label_type_dropdown)
        menu.addAction(snap_left)
        menu.addAction(snap_right)

        selected_action = menu.exec(event.screenPos())           
        if selected_action == label_type_dropdown:
            print("label drop")
        elif selected_action == add_comment:
            self.datawindow.add_comment_at_click(x)
        elif selected_action == snap_left:
            label_area.set_transition_line("left", left_end)
            self.datawindow.selection._attempt_snap_and_merge(label_area.transition_line)
        elif selected_action == snap_right:
            label_area.set_transition_line("right", right_start)
            self.datawindow.selection._attempt_snap_and_merge(label_area.right_transition_line)
        else:
            pass
    
