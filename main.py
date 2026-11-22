"""TriExplorer application entry point.

Run this file directly or import Explorer from explorer.py for embedding/tests.
"""
from explorer import Explorer
import sys
import app_icons
from PySide6.QtWidgets import QApplication
from appcore import APP
import stylesheet

if __name__ == "__main__":
    app_icons.register_taskbar_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName("KHzz")
    app.setWindowIcon(app_icons.app_icon())
    app.setStyle("Fusion")
    palette = stylesheet.import_palette()
    app.setPalette(palette)
    app.setStyleSheet(stylesheet.STYLE)
    window = Explorer()
    window.show()
    sys.exit(app.exec())