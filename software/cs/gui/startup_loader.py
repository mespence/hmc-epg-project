# startup_loader.py
import os
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication
from LoadingScreen import LoadingScreen
from utils.ResourcePath import resource_path

def load_fonts():
    fonts = [
        "resources/fonts/Inter-Regular.otf",
        "resources/fonts/Inter-Bold.otf",
        "resources/fonts/Inter-Italic.otf",
        "resources/fonts/Inter-BoldItalic.otf"
    ]
    for font in fonts:
        QFontDatabase.addApplicationFont(resource_path(font))

def start_main_application(app_instance, recording_settings = None, file=None, channel_index=None):
    load_fonts()

    splash = LoadingScreen()
    splash.show()
    app_instance.processEvents()

    from pyqtgraph import setConfigOptions
    from MainWindow import MainWindow

    if os.name == "nt":
        setConfigOptions(useOpenGL=True)

    window = MainWindow(recording_settings=recording_settings, file=file, channel_index=channel_index)

    window.showMaximized()
    window.raise_()
    window.activateWindow()

    app_instance.processEvents()
    splash.close()

    return window
