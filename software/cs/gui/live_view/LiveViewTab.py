import numpy as np
import os, sys
import json
import time
import threading
from queue import Empty
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QMetaObject, Q_ARG
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QToolButton, QHBoxLayout, QVBoxLayout, QLabel
)

from live_view.LiveDataWindow import LiveDataWindow
from live_view.device_panel.DevicePanel import DevicePanel
from live_view.slider_panel.SliderPanel import SliderPanel
from live_view.socket.EPGSocket import SocketClient, SocketServer
from epg_board.EPGStateManager import get_spec, get_state
from utils.ResourcePath import resource_path
from utils.SVGIcon import svg_to_colored_pixmap


class LiveViewTab(QWidget):
    def __init__(self, recording_settings = None, parent=None):
        super().__init__(parent)
    
        self.initial_timestamp: float = None  # unix timestamp of the first data point in a recording
        self.total_pause_time: float = 0  # the cumulative length of any pauses
        self.pause_start_time: float = None # unix timestamp of the most recent pause

        if recording_settings:
            self.datawindow = LiveDataWindow(recording_settings, parent=self)
        else:
            self.datawindow = LiveDataWindow(parent=self)
        self.datawindow.getPlotItem().hideButtons()

        self._spec = get_spec()
        self.epg_settings = get_state(parent=self)  # parent is ignored if already created

        # === Socket ===
        self.socket_server = SocketServer()
        self.socket_server.start()

        self.socket_client = SocketClient(client_id='CS', parent=self)
        #self.socket_client.peerConnectionChanged.connect(self.update_button_state)
        self.socket_client.connect()      

        self.receive_loop = threading.Thread(target=self._socket_recv_loop, daemon=True)
        self.receive_loop.start()

        

        self.pause_live_button = QPushButton("Pause Live View", self)
        self.pause_live_button.setCheckable(True)
        self.pause_live_button.setChecked(True)
        self.pause_live_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_live_button.setStyleSheet("""
            QPushButton {
                background-color: #49a6fe;
                color: white;
                border-radius: 3px;
                padding: 5px;
                outline: none;
                width: 100px;
            } QPushButton:disabled {
                background-color: gray;
                color: white;
                border-radius: 3px;
                padding: 5px;
                outline: none;
            } QPushButton:focus {
                border: 3px solid #4aa8ff;
                padding: 2px;
            }
        """)

        self.add_comment_button = QPushButton("Add Comment", self)
        self.add_comment_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_comment_button.setToolTip("Add Comment at Current Time")
        self.add_comment_button.setStyleSheet("""
            QPushButton {
                background-color: #49a6fe;
                color: white;
                border-radius: 3px;
                padding: 5px;
                outline: none;
                width: 100px;
            } QPushButton:disabled {
                background-color: gray;
                color: white;
                border-radius: 3px;
                padding: 5px;
                outline: none;
            } QPushButton:focus {
                border: 3px solid #4aa8ff;
                padding: 2px;
            }
        """)
        self.add_comment_button.clicked.connect(self.call_add_comment)
        self.add_comment_button.setEnabled(False)
        
        self.pause_live_button.setCheckable(True)
        self.pause_live_button.setChecked(True)
        self.pause_live_button.setToolTip("Pause Live View")
        self.pause_live_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_live_button.clicked.connect(self.toggle_live)
        self.pause_live_button.setEnabled(False)
        


        self.slider_panel = SliderPanel(parent=self)
        #self.slider_panel.off_button.clicked.connect(self.end_recording)
        #self.slider_panel.setEnabled(False) # TODO: update to auto close if no device connected

        self.slider_button = QToolButton(parent=self)
        self.slider_button.setText("EPG Controls")
        icon_path = resource_path("resources/icons/sliders.svg")
        colored_icon = QIcon(svg_to_colored_pixmap(icon_path, "#DDDDDD", 24))
        self.slider_button.setIcon(colored_icon)
        self.slider_button.setIconSize(QSize(24, 24))
        self.slider_button.setToolTip("Open control sliders")
        self.slider_button.setAutoRaise(True)
        self.slider_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.slider_button.clicked.connect(self.toggleSliderPanel)
        self.slider_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.slider_button.setStyleSheet("""
            QToolButton {
                outline: none;
            } QToolButton:disabled {
                color: gray;               
                qproperty-icon: none;
            }
            QToolButton:focus {
                outline 3px solid #4aa8ff;
            }
        """)

        #self.update_button_state(True)

        top_controls = QHBoxLayout()

        if sys.platform.startswith("win"):
            self.device_panel = DevicePanel(parent=self)
            self.device_button = QToolButton(parent=self)
            self.device_button.setText("EPG Devices")
            icon_path = resource_path("resources/icons/bug.svg")
            colored_icon = QIcon(svg_to_colored_pixmap(icon_path, "#DDDDDD", 24))
            self.device_button.setIcon(colored_icon)
            self.device_button.setIconSize(QSize(24, 24))
            self.device_button.setToolTip("Open EPG devices")
            self.device_button.setAutoRaise(True)
            self.device_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.device_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.device_button.clicked.connect(self.toggleDevicePanel)
            self.device_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.device_button.setStyleSheet("""
                QToolButton {                    
                    outline: none;
                } QToolButton:disabled {
                    color: gray;               
                    qproperty-icon: none;
                }
                QToolButton:focus {
                    outline 3px solid #4aa8ff;
                }
            """)
            top_controls.addWidget(self.device_button)

        top_controls.addStretch()  # push slider button to right
        top_controls.addWidget(self.slider_button)

        top_controls_widget = QWidget()
        top_controls_widget.setLayout(top_controls)
        top_controls_widget.setStyleSheet("""
            QWidget {
                border-bottom: 1px solid #808080;
            }
        """)

        

        bottom_controls = QHBoxLayout()
        bottom_controls.addStretch()
        bottom_controls.addWidget(self.pause_live_button)
        bottom_controls.addWidget(self.add_comment_button)
        bottom_controls.addStretch()

        bottom_controls_widget = QWidget()
        bottom_controls_widget.setLayout(bottom_controls)
        bottom_controls_widget.setStyleSheet("""
            QWidget {
                border-top: 1px solid #808080;
            }
        """)

        center_layout = QVBoxLayout()
        center_layout.addWidget(top_controls_widget)
        center_layout.addWidget(self.datawindow)
        center_layout.addWidget(bottom_controls_widget)

        main_layout = QHBoxLayout()
        if sys.platform.startswith("win"):
            main_layout.addWidget(self.device_panel, 2)
        main_layout.addLayout(center_layout, 15)
        main_layout.addWidget(self.slider_panel, 5)

        # can't figure out the 2 random tabs --> this logic below doesnt work either
        # self.setTabOrder(self.pause_live_button, self.add_comment_button)
        # self.setTabOrder(self.add_comment_button, self.slider_button)
        # self.setTabOrder(self.slider_button, self.pause_live_button)

        self.setLayout(main_layout)

    def toggleDevicePanel(self):
        if not self.device_button.isEnabled():
            return
        is_visible = self.device_panel.isVisible()
        self.device_panel.setVisible(not is_visible)

        if is_visible:
            self.slider_button.setToolTip("Open EPG devices")
        else:
            self.slider_button.setToolTip("Hide EPG devices")

    def toggleSliderPanel(self):
        if not self.slider_button.isEnabled():
            return
        is_visible = self.slider_panel.isVisible()
        self.slider_panel.setVisible(not is_visible)

        if is_visible:
            self.slider_button.setToolTip("Open control sliders")
        else:
            self.slider_button.setToolTip("Hide control sliders")

    def update_button_state(self, is_connected: bool):
        """
        Handles enabling/disabling the slider button and panel when the EPG is not connected.
        """      
        self.slider_button.setEnabled(is_connected)
        self.slider_panel.setVisible(is_connected)
        tooltip = "Open control sliders" if is_connected else "Connect to Engineering UI to enable controls"
        self.slider_button.setToolTip(tooltip)            

    def toggle_live(self):
        live_mode = self.pause_live_button.isChecked()

        self.pause_live_button.setText("Pause Live View" if live_mode else "Resume Live View")
        self.datawindow.set_live_mode(live_mode)

    def call_add_comment(self):
        self.datawindow.add_comment_live()

    def start_recording(self):
        dw = self.datawindow
        dw.live_mode = True
        self.pause_live_button.setEnabled(True)
        self.pause_live_button.setToolTip("Pause Live View")
        self.add_comment_button.setEnabled(True)
        self.add_comment_button.setToolTip("Add Comment at Current Time")

        self.slider_panel.start_button.setEnabled(False)
        self.slider_panel.pause_button.setEnabled(True)
        self.slider_panel.stop_button.setEnabled(True)


        dw.xy_data = [np.array([]), np.array([])]
        dw.curve.clear()
        dw.scatter.clear()
        dw.buffer_data.clear()
        self.socket_client.recv_queue.queue.clear()

        self.initial_timestamp = time.time()
        dw.plot_update_timer.start()


    def pause_recording(self):
        dw = self.datawindow
        dw.plot_update_timer.stop()
        dw.live_mode = False

        self.pause_live_button.setEnabled(False)
        self.pause_live_button.setToolTip("Resume Recording to Toggle Live View")

        dw.integrate_buffer_to_np()

        self.pause_start_time = time.time()


    def resume_recording(self):
        # with self.socket_client.recv_queue.mutex:
        #     self.socket_client.recv_queue.queue.clear()

        dw = self.datawindow

        if self.pause_start_time is not None:
            pause_duration = time.time() - self.pause_start_time
            self.total_pause_time += pause_duration
            self.pause_start_time = None  # reset

        dw.live_mode = True 
        self.pause_live_button.setEnabled(True)
        self.pause_live_button.setToolTip("Pause Live View")


        self.datawindow.plot_update_timer.start()



    def stop_recording(self):
        self.datawindow.plot_update_timer.stop()
        self.datawindow.buffer_data.clear()

        self.datawindow.live_mode = False
        self.pause_live_button.setEnabled(False)
        self.pause_live_button.setToolTip("Connect to Engineering UI to enable live mode")
        self.add_comment_button.setEnabled(False)
        self.add_comment_button.setToolTip("Connect to Engineering UI to enable commenting")

        self.slider_panel.start_button.setEnabled(True)
        self.slider_panel.pause_button.setEnabled(False)
        self.slider_panel.stop_button.setEnabled(False)



    def _socket_recv_loop(self):
        acknowledged = False # whether the client has been acknowledged by the server
        while self.socket_client.connected:
            try:
                raw_message = self.socket_client.recv_queue.get(timeout=0.01)

                # process server acknowledgement
                if not acknowledged:
                    if isinstance(raw_message, str) and raw_message.strip() == "ack":
                        acknowledged = True 
                    continue                

            
                # parse message into individual commands
                # NOTE: message can include multiple commands/data, i.e. "{<command1>}\n{<command2>}\n"
                if isinstance(raw_message, dict):
                    messages = [raw_message]
                else:
                    # Multiple newline-separated JSON strings
                    message_list = raw_message.strip().split("\n")
                    messages = [
                        json.loads(s) for s in message_list if s.strip()
                    ]
            
                # delegate message response
                for message in messages:
                    if message["source"] == self.socket_client.client_id:
                        continue # skip messages from this client

                    message_type = message['type']

                    if message_type == 'data':
                        # skip when not plotting
                        try:
                            if not self.datawindow.plot_update_timer.isActive():
                                continue
                        except RuntimeError:
                            break  # app closed

                        result = self.process_data_message(message)
                        if result is None:
                            continue
                    elif message_type == "control":
                        self.process_control_message(message)
                    elif message_type == "state_sync":
                        self.process_state_sync(message)
                        
            except Empty:
                continue  # restart the loop

            except Exception as e:
                self.datawindow.live_mode = False
                print("[CS RECIEVE LOOP ERROR]", e)



    def process_data_message(self, message: str):
        """
        Returns `None` if `continue` should be given to the while loop of _socket_recv_loop,
        returns `True` otherwise.
        """
        if self.initial_timestamp is None:
            return None

        device_time = float(message['value'][0])
        timestamp = device_time - self.initial_timestamp - self.total_pause_time
        volt = float(message['value'][1])

        if timestamp < 0 or device_time < self.initial_timestamp:
            return None # skip stale/negative data (if any)

        with self.datawindow.buffer_lock:
            self.datawindow.buffer_data.append((round(timestamp, 4), volt))
            
        self.datawindow.current_time = timestamp
        return True
        
    
    def process_control_message(self, message: str):
        name = message["name"]
        value = message["value"]
        source = message.get("source")

        if name == "ddsa": # ignore, use d0 value instead
            return
        if name == "d0":
            name = "ddsa"
            value = -4.207 * float(value) + 1075.51 # Formula from Pierce
        if name == "ddso":
            value = -1*value



        # Workaround to get set_control_value to run in the GUI thread
        # Might be cleaner to use signals, but this works for now
        QMetaObject.invokeMethod(
            self.slider_panel,
            "set_control_value",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, name),
            Q_ARG(object, value),
            Q_ARG(str, source)
        )

    def process_state_sync(self, message: str):
        value = message["value"]

        QMetaObject.invokeMethod(
            self.slider_panel,
            "set_all_controls",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(dict, value),
        )