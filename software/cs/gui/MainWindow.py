import os
import re
import sys
import ctypes
import datetime

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QMessageBox,
    QMenuBar,
    QMenu,
    QTabWidget,
    QPushButton
)
from PyQt6.QtCore import pyqtSignal, QObject, QEvent, Qt, QTimer
from PyQt6.QtGui import QIcon, QFontDatabase


from label_view.DataWindow import DataWindow
from utils.ResourcePath import resource_path
from EPGData import EPGData
from FileSelector import FileSelector
#from label_view.Labeler import Labeler
from settings import settings

from FileSelector import FileSelector
#from utils.UploadFileDialog import UploadFileDialog
from settings.SettingsWindow import SettingsWindow

from live_view.LiveViewTab import LiveViewTab
from label_view.LabelViewTab import LabelViewTab
from utils.AboutDialog import AboutDialog


class MainWindow(QMainWindow):
    #start_labeling = pyqtSignal()

    def __init__(self, recording_settings = None, file = None, channel_index = None) -> None:
        if os.name == "nt":  # windows
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "company.app.1"  # needed to set taskbar icon on windows
            )
        super().__init__()

        self.epgdata = EPGData()
        
        if file:
            self.epgdata.current_file = file
        else:
            file = self.epgdata.current_file
        
        self.channel_index = channel_index
        if file and self.channel_index:
            self.epgdata.load_data(file, self.channel_index)

        if recording_settings:
            self.live_view_tab = LiveViewTab(recording_settings, parent=self)
        else:
            self.live_view_tab = LiveViewTab(parent=self)
        self.label_tab = LabelViewTab(self)

        self.settings_window = SettingsWindow(self)
        self.about_window = AboutDialog(self)

        if settings.get("default_recording_directory") is None:
            settings.set("default_recording_directory", os.getcwd())
        if settings.get("backup_recording_directory") is None:
            settings.set("backup_recording_directory", os.getcwd())

        self.initUI()

    def initUI(self):
        # Supervised Classification of Insect Data and Observations
        self.setWindowTitle("SCIDO EPG Labeler")
        icon_path = resource_path("SCIDO.ico")
        self.setWindowIcon(QIcon(icon_path))

        main_geometry = QApplication.instance().primaryScreen().geometry()
        self.move(main_geometry.left(), main_geometry.top())

        # === Menu Bar ===
        menubar = QMenuBar(self)
        file_menu = QMenu("File", self)
        self.file_open = file_menu.addAction("Open")
        self.file_open.triggered.connect(
            # self.open_upload_dialog
            lambda: FileSelector.load_new_data(self.epgdata, self.label_tab.datawindow)
        )
        file_menu.addSeparator()

        save_csv = file_menu.addAction("Save to CSV")
        save_csv.triggered.connect(self.save_data)
        self.export_to_txt = file_menu.addAction("Export to TXT")
        self.export_to_txt.triggered.connect(self.export_waveforms_to_txt)
        export_comment_csv = file_menu.addAction("Export Comments")
        export_comment_csv.triggered.connect(self.export_comments_from_current_tab)

        
        file_menu.addSeparator()
        file_menu.addAction("Settings", self.open_settings)
        file_menu.addSeparator()
        file_menu.addAction("Exit App", self.close)

        edit_menu = QMenu("Edit", self)
        placeholder = edit_menu.addAction("Nothing here yet!")
        placeholder.setEnabled(False)

        view_menu = QMenu("View", self)
        placeholder = view_menu.addAction("Nothing here yet!")
        placeholder.setEnabled(False)


        help_menu = QMenu("Help", self)
        help_menu.addAction("About", self.open_about)

        # TODO add menu functionality
        menubar.addMenu(file_menu)
        menubar.addMenu(edit_menu)
        menubar.addMenu(view_menu)
        menubar.addMenu(help_menu)

        self.setMenuBar(menubar)

        # === Tab Widget ===
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.handle_tab_change)
        QTimer.singleShot(0, self.set_initial_focus)

        self.tabs.setStyleSheet("""
            QTabBar::tab {
                font-size: 14px;
                font-weight: bold;
                padding: 8px 20px 10px 20px;
                border: none;
                margin-right: 0px;
            }
            QTabBar::tab:selected {
                background: #404AA8FF;
                padding-bottom: 8px;
                border-bottom: 3px solid #4aa8ff;
            }

            QTabBar::tab:!selected {
                background: #33000000;
                border-bottom: none;
            }
                                
            QTabWidget::pane {
                border: 1px solid palette(mid);
                border-radius: 0px;
                top: -1px;
            }
        """)
        self.tabs.tabBar().setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCentralWidget(self.tabs)

        # Add tabs
        self.tabs.addTab(self.live_view_tab, "Live View")       
        self.tabs.addTab(self.label_tab, "Label View")


    # def open_upload_dialog(self):
    #     upload_dialog = UploadFileDialog()
    #     if upload_dialog.exec(): # open modally
    #         upload_file_path = upload_dialog.get_file_path()
    #         self.launchMainWindowFile.emit(upload_file_path)
    #         self.accept() # Accept and close the AppLauncherDialog


    def open_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def open_about(self):
        self.about_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def export_comments_from_current_tab(self):
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, LiveViewTab) or isinstance(current_widget, LabelViewTab):
            current_widget.datawindow.export_comments()
        else:
            msg = QMessageBox(self)
            msg.setWindowTitle("Cannot Export Comments")
            msg.setText("Current tab does not support exporting comments.")
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

    def export_waveforms_to_txt(self):
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, LabelViewTab):
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            current_widget.datawindow.export_txt()
            QApplication.restoreOverrideCursor()


    def save_data(self):
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, LiveViewTab):
            current_widget.datawindow.export_df()
        elif isinstance(current_widget, LabelViewTab):
            # for now all saving is exporting
            current_widget.datawindow.export_df()
        else:
            msg = QMessageBox(self)
            msg.setWindowTitle("Cannot Save Data")
            msg.setText("Current tab does not support saving data.")
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

    def set_initial_focus(self):
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, LiveViewTab):
            current_widget.datawindow.setFocus()
        elif isinstance(current_widget, LabelViewTab):
            current_widget.datawindow.setFocus()
    
    def handle_tab_change(self, index: int):
        widget = self.tabs.widget(index)

        # Set focus
        if isinstance(widget, (LiveViewTab, LabelViewTab)):
            widget.datawindow.setFocus()

        # Enable/disable "Open" action
        if isinstance(widget, LiveViewTab):
            self.file_open.setEnabled(False)
            self.export_to_txt.setEnabled(False)
        else:
            self.file_open.setEnabled(True)
            self.export_to_txt.setEnabled(True)
    
    # def start_labeling(self):
    #     task = LabelingTask(self.labeler, self.epgdata, self.datawindow)
    #     self.threadpool.start(task)

    # To stop labeling, the labeling cannot run in the same thread as the GUI, which it currently this.
    # This resolves that, but does introduce the possibility of multithreading bugs.
    # def update_progress(self, current, total):
    #     percentage = int((current / total) * 100)
    #     self.progressBar.setValue(percentage)

    # def switch_cursor_state(self):
    #     self.datawindow.cursor_state = not self.datawindow.cursor_state
    #     if self.datawindow.cursor_state == 0:
    #         self.baselineCursorButton.setText("Change to Baseline Cursor")
    #     elif self.datawindow.cursor_state == 1:
    #         self.baselineCursorButton.setText("Change to Normal Cursor")

    def openSliders(self):
        is_visible = self.slider_panel.isVisible()
        self.slider_panel.setVisible(not is_visible)

    def closeEvent(self, event = None):
        label_dw = self.label_tab.datawindow
        live_dw = self.live_view_tab.datawindow
        bt_io = self.live_view_tab.device_panel.bt_io

        label_view_unsaved = not label_dw.checkForUnsavedChanges()
        live_view_unsaved = live_dw.data_modified

        def confirm_exit_box(tab_name: str):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("SCIDO - Confirm Exit")
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setText(f'<b>Do you want to save changes to the <u>{tab_name}</u> before closing?</b>')
            msg_box.setInformativeText("Your changes will be lost if you don't save them.")

            save_btn = QPushButton("Save")
            dont_save_btn = QPushButton("Don't save")
            cancel_btn = QPushButton("Cancel")

            save_btn.setStyleSheet("font-weight: bold;")

            msg_box.addButton(save_btn, QMessageBox.ButtonRole.AcceptRole)
            msg_box.addButton(dont_save_btn, QMessageBox.ButtonRole.DestructiveRole)
            msg_box.addButton(cancel_btn, QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(save_btn)

            reply = msg_box.exec()

            if msg_box.clickedButton() == save_btn:
                export_successful = label_dw.export_df() 
                if not export_successful:
                    # export_df cancelled by the user, so cancel closing application
                    event.ignore()
                    return
            elif msg_box.clickedButton() == dont_save_btn:
                pass # proceed with closing w/o save
            else:
                event.ignore()
                return

        if label_view_unsaved:
            confirm_exit_box("Label View")
        if live_view_unsaved:
            confirm_exit_box("LIve View")
  
           
        if live_dw.plot_update_timer.isActive():
            live_dw.plot_update_timer.stop()
        if live_dw.save_timer.isActive():
            live_dw.save_timer.stop()
        if bt_io._thread.isRunning():
            bt_io.stop()


        if not live_dw.backup_renamed:
            # store the active filenames, updated after each save with utc time stamp
            
            # delete waveform and comments csv because no data uploaded
            os.remove(live_dw.waveform_backup_path)
            os.remove(live_dw.comments_backup_path)

        three_days_ago_utc = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
        
        # --- DELETE OLD BACKUPS --- 
        if os.path.exists(live_dw.periodic_backup_dir):
            for fname in os.listdir(live_dw.periodic_backup_dir):
                fpath = os.path.join(live_dw.periodic_backup_dir, fname)
                if not os.path.isfile(fpath):
                    continue

                match = re.search(r'(\d{8}_\d{6})', fname)
                if match:
                    timestamp_str = match.group(1)
                    file_utc_time = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S").replace(tzinfo=datetime.timezone.utc)
                            
                    if file_utc_time < three_days_ago_utc: # Check if file time is BEFORE 3 days ago
                        try:
                            os.remove(fpath)
                            print(f"DEBUG: Deleted old timestamped backup: {fpath} (Created: {file_utc_time})")
                        except OSError as e:
                            print(f"ERROR: Could not delete old {fpath}: {e}")
        
        settings.save_all()
        super().closeEvent(event)




class GlobalMouseTracker(QObject):
    """
    Global event filter that updates the cursor hover position inside popups
    (e.g., menus) by tracking global mouse coordinates and mapping them to
    DataWindow viewbox space.
    """

    def __init__(self, mainwindow: MainWindow):
        super().__init__()
        self.mainwindow: MainWindow = mainwindow
        self.datawindow: DataWindow = mainwindow.label_tab.datawindow

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseMove:
            global_pos = (
                event.globalPosition()
            )  # pos relative to top left corner of screen
            local_pos = self.mainwindow.mapFromGlobal(
                global_pos
            )  # pos rel. to top left of application
            self.datawindow.last_cursor_pos = local_pos

            view_pos = self.datawindow.window_to_viewbox(
                local_pos
            )  # pos rel. to origin of plot
            selection = self.datawindow.selection
            selection.hovered_item = selection.get_hovered_item(
                view_pos.x(), view_pos.y()
            )
        return super().eventFilter(obj, event)