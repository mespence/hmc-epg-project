import random
import threading
import time
from PyQt6.QtWidgets import QWidget

class data_simulator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        thread = threading.Thread(target= self.simulation)
        thread.start()
        thread.join()
        
    def simulation(self, max_time=2000):
        t = 0
        while t <= max_time:
            self.parent().buffer_data.append(tuple[t, random.uniform(-1, 1)])
            t+=1
            time.sleep(0.01)
        
