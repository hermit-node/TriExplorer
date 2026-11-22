"""Windows taskbar identity and project icon."""
from __future__ import annotations

import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from runtime_paths import bundled_asset

APP_ID = "KHzz.TriExplorer"
ICON_PATH = bundled_asset("assets", "triexplorer.ico")


def app_icon():
    icon = QIcon(str(ICON_PATH))
    if icon.isNull():
        raise FileNotFoundError(f"Missing TriExplorer icon: {ICON_PATH}")
    return icon


def register_taskbar_identity():
    """Use a stable Windows AppUserModelID instead of Python's process identity."""
    if sys.platform == "win32":
        import ctypes
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result:
            raise OSError(f"Windows taskbar identity registration failed: HRESULT {result}")
