"""Native permission checks that do not import platform-only Python packages."""

import ctypes
import sys


def require_screen_capture() -> None:
    if sys.platform != "darwin":
        return
    quartz = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    preflight = quartz.CGPreflightScreenCaptureAccess
    preflight.argtypes = []
    preflight.restype = ctypes.c_bool
    if not preflight():
        raise RuntimeError(
            "Screen capture needs Screen Recording permission in System Settings > "
            "Privacy & Security. Allow OpenHeadMouse (or the Python executable), then restart it."
        )
