"""Consistent PULSE window, taskbar and executable branding."""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

APP_ID = "PulseOptimizer.PULSE"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "pulse.ico"
log = logging.getLogger(__name__)


def set_windows_app_id():
    """Call before creating QApplication so Windows groups PULSE separately."""
    if sys.platform != "win32":
        return False
    try:
        function = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_int32  # HRESULT, including on 64-bit Windows.
        result = function(APP_ID)
        if result < 0:
            log.warning("Cannot set taskbar application ID: HRESULT 0x%08X", result & 0xffffffff)
            return False
        return True
    except (OSError, AttributeError):
        log.warning("Windows taskbar application ID is unavailable", exc_info=True)
        return False


def application_icon():
    from PySide6.QtGui import QIcon
    result = QIcon(str(ICON_PATH))
    if result.isNull():
        # Keep the same logo if an incomplete source copy is missing its assets.
        from .widgets import ORANGE, icon
        return icon("bolt", ORANGE, 64)
    return result
