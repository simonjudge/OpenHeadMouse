"""Windows pointer movement uses real input, independently of dwell clicking."""

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows mouse input")


@pytest.mark.parametrize("position", [(-1920, -200), (-1, 879), (0, 0), (1919, 879)])
def test_absolute_input_covers_virtual_desktop_without_pixel_drift(monkeypatch, position):
    from core import windows_mouse
    from pynput._util.win32 import INPUT

    metrics = {76: -1920, 77: -200, 78: 3840, 79: 1080}
    monkeypatch.setattr(ctypes.windll.user32, "GetSystemMetrics", metrics.__getitem__)
    events = []

    def send(count, event, size):
        assert count == 1 and size == ctypes.sizeof(INPUT)
        value = ctypes.cast(event, ctypes.POINTER(INPUT)).contents.value.mi
        events.append((value.dx, value.dy, value.dwFlags))
        return 1

    monkeypatch.setattr(windows_mouse, "SendInput", send)
    controller = windows_mouse.WindowsMouseController()
    echoed = []
    monkeypatch.setattr(controller, "_emit", lambda *args: echoed.append(args))
    controller.position = position

    dx, dy, flags = events[0]
    assert flags == 0xC001  # MOVE | ABSOLUTE | VIRTUALDESK
    assert dx * 3840 // 65536 - 1920 == position[0]
    assert dy * 1080 // 65536 - 200 == position[1]
    assert echoed == [("on_move", *position, True)]


def test_blocked_input_is_reported_and_not_echoed_as_a_success(monkeypatch):
    from core import windows_mouse

    monkeypatch.setattr(
        ctypes.windll.user32, "GetSystemMetrics",
        {76: 0, 77: 0, 78: 1920, 79: 1080}.__getitem__,
    )
    monkeypatch.setattr(windows_mouse, "SendInput", lambda *args: 0)
    controller = windows_mouse.WindowsMouseController()
    monkeypatch.setattr(controller, "_emit", lambda *args: pytest.fail("Input was blocked"))
    with pytest.raises(OSError, match="matching permissions"):
        controller.position = (100, 100)


def test_headmouse_uses_input_controller_by_default():
    from core.headmouse import HeadMouse, ScreenBounds
    from core.windows_mouse import WindowsMouseController

    headmouse = HeadMouse(ScreenBounds(1920, 1080))
    assert isinstance(headmouse._mouse, WindowsMouseController)


def test_invalid_desktop_geometry_is_not_used_for_input(monkeypatch):
    from core import windows_mouse

    monkeypatch.setattr(ctypes.windll.user32, "GetSystemMetrics", lambda metric: 0)
    monkeypatch.setattr(
        windows_mouse, "SendInput", lambda *args: pytest.fail("Invalid desktop geometry")
    )
    with pytest.raises(OSError, match="usable virtual desktop"):
        windows_mouse.WindowsMouseController().position = (100, 100)
