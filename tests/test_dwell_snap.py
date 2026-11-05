import math
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from dwell_click import snap


class NativeFunction:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


@pytest.mark.parametrize("state,action,expected", [
    (0, True, (110, 90, 20, 20)), (1, True, None),
    (0x8000, True, None), (0x10000, True, None), (0, False, None),
])
def test_windows_native_hit_test_releases_com_objects(monkeypatch, state, action, expected):
    c = snap.C
    table = (c.c_void_p * 28)(*range(1, 29))
    obj = c.pointer(c.cast(table, c.POINTER(c.c_void_p)))
    released = []
    cleared = []
    calls = []

    def lookup(point, out, child):
        calls.append((point.x, point.y))
        out._obj.value = c.cast(obj, c.c_void_p).value
        child._obj.vt = 3
        child._obj.integer = 0
        return 0

    def get_state(obj, child, out):
        out._obj.vt = 3
        out._obj.integer = state
        return 0

    def get_action(obj, child, out):
        out._obj.value = 123 if action else None
        return 0

    def location(obj, x, y, width, height, child):
        for out, value in zip((x, y, width, height), (110, 90, 20, 20)):
            out._obj.value = value
        return 0

    functions = {
        3: lambda obj: released.append("object"),
        15: get_state, 21: get_action, 23: location,
    }
    libraries = {
        "ole32": SimpleNamespace(
            CoInitializeEx=NativeFunction(lambda *args: 0),
            CoUninitialize=NativeFunction(lambda: released.append("COM")),
        ),
        "oleacc": SimpleNamespace(AccessibleObjectFromPoint=NativeFunction(lookup)),
        "oleaut32": SimpleNamespace(
            SysStringLen=NativeFunction(lambda pointer: 5),
            SysFreeString=NativeFunction(lambda pointer: released.append("string")),
            VariantClear=NativeFunction(lambda value: cleared.append(value._obj.vt)),
        ),
    }
    monkeypatch.setattr(c, "WinDLL", lambda name: libraries[name], raising=False)
    monkeypatch.setattr(c, "WINFUNCTYPE",
                        lambda *args: lambda address: functions[address], raising=False)
    with snap._windows_hit_test() as hit:
        assert hit((120, 100)) == expected
    assert calls == [(120, 100)]
    assert released[-2:] == ["object", "COM"]
    assert len(cleared) == 2
    assert ("string" in released) == (state == 0 and action)


def test_windows_uia_hit_test_finds_toggle_pattern_and_releases_com_objects(monkeypatch):
    c = snap.C
    methods = {}
    released = []
    cleared = []

    def make_object(methods_for_object):
        table = (c.c_void_p * 20)()
        for index, function in methods_for_object.items():
            address = len(methods) + 1
            table[index] = address
            methods[address] = function
        obj = c.pointer(c.cast(table, c.POINTER(c.c_void_p)))
        return obj

    pattern = make_object({2: lambda obj: released.append("pattern")})

    def element_property(obj, property_id, value):
        variant = value._obj
        if property_id == 30010:
            variant.vt = 11
            variant.boolean = -1
        else:
            variant.vt = 0x2005
            variant.array = 123
        return 0

    def get_pattern(obj, pattern_id, value):
        if pattern_id == 10015:
            value._obj.value = c.cast(pattern, c.c_void_p).value
            return 0
        value._obj.value = None
        return -1

    element = make_object({2: lambda obj: released.append("element"),
                           10: element_property, 16: get_pattern})
    walker = make_object({
        2: lambda obj: released.append("walker"),
        3: lambda obj, current, parent: setattr(parent._obj, "value", None) or 0,
    })

    def point_lookup(obj, point, value):
        value._obj.value = c.cast(element, c.c_void_p).value
        return 0

    automation = make_object({
        2: lambda obj: released.append("automation"),
        7: point_lookup,
        14: lambda obj, value: setattr(
            value._obj, "value", c.cast(walker, c.c_void_p).value,
        ) or 0,
    })

    class NativeFunction:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    libraries = {
        "ole32": SimpleNamespace(
            CoInitializeEx=NativeFunction(lambda *args: 0),
            CoUninitialize=NativeFunction(lambda: released.append("COM")),
            CoCreateInstance=NativeFunction(
                lambda clsid, outer, context, iid, value: setattr(
                    value._obj, "value", c.cast(automation, c.c_void_p).value,
                ) or 0
            ),
        ),
        "oleaut32": SimpleNamespace(
            VariantClear=NativeFunction(lambda value: cleared.append(value._obj.vt) or 0),
            SafeArrayGetDim=NativeFunction(lambda array: 1),
            SafeArrayGetLBound=NativeFunction(
                lambda array, dimension, value: setattr(value._obj, "value", 0) or 0
            ),
            SafeArrayGetUBound=NativeFunction(
                lambda array, dimension, value: setattr(value._obj, "value", 3) or 0
            ),
            SafeArrayGetElement=NativeFunction(
                lambda array, index, value: setattr(
                    value._obj, "value", (110.0, 90.0, 20.0, 20.0)[index._obj.value],
                ) or 0
            ),
        ),
    }
    monkeypatch.setattr(c, "WinDLL", lambda name: libraries[name], raising=False)
    monkeypatch.setattr(c, "WINFUNCTYPE",
                        lambda *args: lambda address: methods[address], raising=False)

    with snap._windows_uia_hit_test() as hit:
        assert hit((120, 100)) == (110.0, 90.0, 20.0, 20.0)
    assert released == ["pattern", "element", "walker", "automation", "COM"]
    assert cleared == [11, 0x2005]


@pytest.mark.parametrize("enabled,press,expected", [
    (True, True, (110, 90, 20, 20)), (False, True, None), (True, False, None),
])
def test_macos_native_hit_test_and_resource_cleanup(monkeypatch, enabled, press, expected):
    values = {1: "system", 2: "element", 3: enabled, 4: (110, 90), 5: (20, 20), 6: []}
    released = []

    def key(pointer):
        return pointer.value if isinstance(pointer, snap.C.c_void_p) else pointer

    def string(allocator, data, encoding):
        index = len(values) + 1
        values[index] = data.decode()
        return index

    def lookup(system, x, y, out):
        out._obj.value = 2
        return 0

    def attribute(obj, name, out):
        attr = {"AXEnabled": 3, "AXPosition": 4, "AXSize": 5}
        index = attr.get(values[key(name)])
        if index is None:
            return -25205
        out._obj.value = index
        return 0

    def actions(obj, out):
        out._obj.value = 6
        values[6] = [index for index, value in values.items() if value == "AXPress"] if press else []
        return 0

    def get_value(pointer, kind, out):
        out[0], out[1] = values[key(pointer)]
        return True

    cf = SimpleNamespace(**{name: NativeFunction(function) for name, function in {
        "CFRelease": lambda pointer: released.append(key(pointer)),
        "CFStringCreateWithCString": string,
        "CFArrayGetCount": lambda pointer: len(values[key(pointer)]),
        "CFArrayGetValueAtIndex": lambda pointer, index: values[key(pointer)][index],
        "CFEqual": lambda left, right: values[key(left)] == values[key(right)],
        "CFBooleanGetValue": lambda pointer: values[key(pointer)],
    }.items()})
    ax = SimpleNamespace(**{name: NativeFunction(function) for name, function in {
        "AXIsProcessTrusted": lambda: True,
        "AXUIElementCreateSystemWide": lambda: 1,
        "AXUIElementCopyElementAtPosition": lookup,
        "AXUIElementCopyAttributeValue": attribute,
        "AXUIElementCopyActionNames": actions,
        "AXValueGetValue": get_value,
        "AXUIElementSetMessagingTimeout": lambda *args: 0,
    }.items()})
    monkeypatch.setattr(snap.C, "CDLL", lambda name: cf if "CoreFoundation" in name else ax)
    with snap._macos_hit_test() as hit:
        assert hit((120, 100)) == expected
    assert 1 in released and 2 in released and 3 in released
    assert all(index in released for index, value in values.items() if isinstance(value, str))


def test_nearest_target_uses_nearest_actionable_centre():
    def hit(point):
        x, y = point
        if 110 <= x <= 130 and 90 <= y <= 110:
            return 110, 90, 20, 20
        if 60 <= x <= 80 and 90 <= y <= 110:
            return 60, 90, 20, 20
        return None

    assert snap.nearest_target((100, 100), hit) == (120, 100)


@pytest.mark.parametrize("rect", [
    None, (0, 0, 0, 20), (0, 0, 20, -1), (0, 0, math.nan, 20),
    (0, 0, math.inf, 20), (0, 0, 1000, 1000), (100, 100, 20, 20),
])
def test_missing_invalid_or_distant_targets_are_ignored(rect):
    assert snap.nearest_target((0, 0), lambda point: rect) is None


def test_radius_boundary_and_negative_monitor_coordinates():
    assert snap.nearest_target((-100, -100), lambda point: (-62, -110, 20, 20)) == (-52, -100)
    assert snap.nearest_target((-100, -100), lambda point: (-61, -110, 20, 20)) is None


def test_radius_scales_for_native_pixels():
    hit = lambda point: (60, 0, 20, 20)
    assert snap.nearest_target((0, 0), hit) is None
    assert snap.nearest_target((0, 0), hit, radius=96) == (70, 10)


@pytest.mark.parametrize("platform,backend,backend_name", [
    ("win32", "_windows_uia_hit_test", "uia"),
    ("win32", "_windows_hit_test", "msaa"),
    ("darwin", "_macos_hit_test", "uia"),
    ("linux", "_linux_hit_test", "uia"),
])
def test_dispatch_and_backend_cleanup(monkeypatch, platform, backend, backend_name):
    closed = []

    @contextmanager
    def detector():
        try:
            yield lambda point: (10, 10, 20, 20)
        finally:
            closed.append(True)

    monkeypatch.setattr(snap.sys, "platform", platform)
    monkeypatch.setattr(snap, backend, detector)
    assert snap.find_snap_target((0, 0), backend_name=backend_name) == (20, 20)
    assert closed == [True]


def test_wayland_has_actionable_error(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with pytest.raises(RuntimeError, match="X11"):
        with snap._linux_hit_test():
            pytest.fail("Wayland must not start an accessibility query")


def test_missing_atspi_has_actionable_error(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(snap.ctypes.util, "find_library", lambda name: None)
    with pytest.raises(RuntimeError, match="libatspi2.0-0"):
        with snap._linux_hit_test():
            pytest.fail("A missing AT-SPI library must be reported")


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or os.environ.get("OPENHEADMOUSE_TEST_ATSPI") != "1",
    reason="Requires an isolated X11 desktop and accessibility D-Bus session",
)
def test_native_atspi_detects_only_actionable_enabled_controls():
    code = """
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel
from PySide6.QtCore import QTimer
app = QApplication([])
window = QWidget()
window.setGeometry(200, 200, 300, 200)
button = QPushButton("Snap target", window)
button.setGeometry(20, 20, 80, 40)
disabled = QPushButton("Disabled", window)
disabled.setGeometry(140, 20, 80, 40)
disabled.setEnabled(False)
label = QLabel("Static text", window)
label.setGeometry(20, 100, 80, 40)
window.show()
window.activateWindow()
button.setFocus()
QTimer.singleShot(500, lambda: print("READY", flush=True))
app.exec()
"""
    env = dict(os.environ, QT_QPA_PLATFORM="xcb", QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1",
               XDG_SESSION_TYPE="x11")
    child = subprocess.Popen([sys.executable, "-c", code], env=env,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "READY"
        assert snap.find_snap_target((250, 230)) == (260, 240)
        assert snap.find_snap_target((370, 230)) is None
        assert snap.find_snap_target((250, 310)) is None
    finally:
        child.terminate()
        child.wait(timeout=5)
