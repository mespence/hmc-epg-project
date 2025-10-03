from pyqtgraph import (
    PlotWidget, InfiniteLine, mkPen
)
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QLabel, QDialog, QTextEdit, QToolTip, QDialogButtonBox, QGraphicsTextItem
from PyQt6.QtSvgWidgets import QGraphicsSvgItem
from PyQt6.QtGui import QMouseEvent, QColor
from PyQt6.QtCore import Qt, QPointF, QTimer

from settings import settings
from utils.TextEdit import TextEdit
from utils.HoverableSvgItem import HoverableSvgItem
from utils.ResourcePath import resource_path



class CommentMarker:
    """
    Represents a comment marker in a PyQtGraph plot.

    Each marker consists of:
    - A vertical dashed line at the specified timestamp.
    - An SVG icon that can be hovered to preview the comment.
    - An interactive dialog for editing, deleting, or moving the comment
      when selecting the icon.

    This class is designed to integrate with a PlotWidget and respond to 
    zoom, pan, and user interactions in the waveform editor.
    """
    def __init__(self, time: float, text: str, datawindow: PlotWidget,
                 icon_path: str = resource_path("resources/icons/message.svg")):
        """
        Initializes the comment marker with a vertical line and icon.

        Parameters:
            time (float): The time location on the x-axis for the marker.
            text (str): The comment text associated with this marker.
            datawindow (PlotWidget): The parent plot widget this marker belongs to.
            icon_path (str): Path to the SVG icon for the comment.
                             Defaults to 'resources/icons/message.svg'.
        """
        self.time = time
        self.text = text
        self.datawindow = datawindow
        self.scene = self.datawindow.scene()
        self.viewbox = self.datawindow.getPlotItem().getViewBox()
        self.icon_path = icon_path
        self.moving = False

        self.marker = InfiniteLine(
            pos = self.time,
            angle = 90,
            pen = mkPen('black', style = Qt.PenStyle.DashLine, width=3),
            movable = False,
        )
        self.viewbox.addItem(self.marker)
        
        self.icon_item = HoverableSvgItem(self)
        self.icon_item.setScale(1)
        self.icon_item.setZValue(10)
        self.scene.addItem(self.icon_item)

        self.update_position()
        self.viewbox.sigTransformChanged.connect(self.update_position) 
        # self.viewbox.sigXRangeChanged.connect(self.update_position)
        self.icon_item.mousePressEvent = self.show_comment_editor

        self.update_color()

    def update_position(self):
        """
        Updates the position of the icon to remain anchored to the
        specified time.

        Automatically hides the marker if it's outside the current 
        view range. This is called during panning, zooming, or any
        transform on the ViewBox.
        """
        if self.moving:
            return
        
        bottom_offset = 25
        line_scene_x = self.viewbox.mapViewToScene(QPointF(self.time, 0))
        scene_rect = self.viewbox.sceneBoundingRect()

        icon_x = line_scene_x.x()
        icon_y = scene_rect.bottom() - bottom_offset

        self.icon_item.setPos(icon_x, icon_y)
        
        # don't show past viewbox range
        icon_scene_rect = self.icon_item.mapRectToScene(self.icon_item.boundingRect())
        icon_scene_x = icon_scene_rect.right()
        icon_right_x = self.viewbox.mapSceneToView(QPointF(icon_scene_x, 0)).x()
        x_min, x_max = self.viewbox.viewRange()[0]
        icon_visible= bool(x_min <= self.time <= x_max and icon_right_x <= x_max) # cast to bulit-in bool rather than numpy.bool
        marker_visible = bool(x_min <= self.time <= x_max)
        self.icon_item.setVisible(icon_visible)
        self.marker.setVisible(marker_visible)

    def show_comment_editor(self, event: None):
        """
        Opens a dialog to edit, move, or delete the comment.

        Options in the dialog:
        - Save: Update the comment text
        - Discard: Delete the comment
        - Move: Initiate moving the marker to a new location
        - Cancel: Close the dialog with no changes
        """

        self.datawindow.comment_editing = True

        dialog = QDialog()
        dialog.setWindowTitle(f"Edit Comment @ {self.time:.2f}s")

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Edit Comment:"))

        text_edit = TextEdit()
        text_edit.setText(self.text) # show old text

        # have cursor at end of old text
        cursor = text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        text_edit.setTextCursor(cursor)
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox()

        save_btn = QPushButton("Save")
        discard_btn = QPushButton("Discard")
        move_btn = QPushButton("Move")
        cancel_btn = QPushButton("Cancel")

        buttons.addButton(save_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(discard_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(move_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        def save():
            self.set_text(text_edit.toPlainText())
            dialog.accept()

        def discard():
            self.datawindow.delete_comment(self.time)
            dialog.accept()

        def move():
            self.set_visible(False)
            self.moving = True
            # delay move comment so that it doesn't register the dialog mouse press event
            QTimer.singleShot(0, lambda: self.datawindow.move_comment_helper(self))
            dialog.accept()

        text_edit.returnPressed.connect(save)
            
        save_btn.clicked.connect(save)
        move_btn.clicked.connect(move)
        discard_btn.clicked.connect(discard)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.setModal(True)
        dialog.exec()

    def set_text(self, new_text: str):
        """
        Updates the internal comment text and notifies the datawindow.

        Parameters:
            new_text (str): The updated comment string.
        """
        self.text = new_text
        self.datawindow.edit_comment(self, new_text)
        self.icon_item.refresh_text(new_text)

    def set_visible(self, visible: bool):
        """
        Sets visibility of both the line and icon associated with
        the marker.

        Parameters:
            visible (bool): Whether the marker should be shown or hidden.
        """
        self.marker.setVisible(visible)
        self.icon_item.setVisible(visible)

    def update_color(self):
        self.marker.setPen(settings.get("plot_theme")["FOREGROUND"], style = Qt.PenStyle.DashLine, width=3)

    def remove(self):
        """
        Removes all graphical components related to this comment marker from
        the plot, including:
        - The vertical InfiniteLine marker from the viewbox
        - The icon representing the comment from the scene
        - The preview box associated with hovering the icon

        Also disconnects the viewbox's sigTransformChanged signal handler
        used for updating the marker's position to prevent calls on a 
        deleted object.
        """
        self.viewbox.removeItem(self.marker)
        # remove hover preview
        self.icon_item.remove()
        self.scene.removeItem(self.icon_item)

        try:
            self.viewbox.sigTransformChanged.disconnect(self.update_position)
        except Exception as e:
            print(f"[ERROR CommentMarker] disconnecting viewbox change signal: {e}")


    # def hoverIconEvent(self, event):
    #     QToolTip.showText(event.screenPos().toPoint(), self.text)