import random
import threading
import time
from PyQt6.QtWidgets import QWidget

class data_simulator(QWidget):
    def __init__(self, device_window, parent=None, daemon=True):
        super().__init__(parent)
        self.device_window = device_window
        thread = threading.Thread(target= self.simulation)
        thread.start()
        
    def simulation(self, max_time=2000):
        print ("running simulation!", flush=True)
        t = 0
        while t <= max_time:
            self.device_window.buffer_data.append((t, random.uniform(-1, 1)))
            t+=1
