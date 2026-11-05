"""Find nearby actionable accessibility elements without activating or clicking them."""

from __future__ import annotations

import ctypes as C
import ctypes.util
import math
import os
import sys
from contextlib import contextmanager

from .engine import DEFAULT_SNAP_RADIUS_PX, Point, SNAP_BACKENDS


SNAP_RADIUS_PX = DEFAULT_SNAP_RADIUS_PX


def nearest_target(position: Point, hit_test, radius: float = SNAP_RADIUS_PX) -> Point | None:
    """Sample around the pointer; only accept small, nearby actionable rectangles."""
    candidates = set()
    offsets = [(0.0, 0.0)]
    for distance in (radius / 2, radius):
        offsets.extend(
            (distance * math.cos(angle * math.pi / 4),
             distance * math.sin(angle * math.pi / 4))
            for angle in range(8)
        )
    for dx, dy in offsets:
        rect = hit_test((position[0] + dx, position[1] + dy))
        if rect is None:
            continue
        x, y, width, height = rect
        if not all(math.isfinite(value) for value in rect) or width <= 0 or height <= 0:
            continue
        centre = (x + width / 2, y + height / 2)
        if math.dist(position, centre) <= radius:
            candidates.add(centre)
    return min(candidates, key=lambda point: (math.dist(position, point), point)) if candidates else None


def find_snap_target(
    position: Point,
    radius: float = SNAP_RADIUS_PX,
    backend_name: str = "uia",
) -> Point | None:
    if sys.platform == "win32":
        if backend_name == "uia":
            backend = _windows_uia_hit_test
        elif backend_name == "msaa":
            backend = _windows_hit_test
        else:
            raise ValueError(f"Unknown Windows Snap-to backend: {backend_name}")
    elif sys.platform == "darwin":
        backend = _macos_hit_test
    elif sys.platform.startswith("linux"):
        backend = _linux_hit_test
    else:
        raise RuntimeError("Snap to is not supported on this operating system.")
    with backend() as hit_test:
        return nearest_target(position, hit_test, radius)


@contextmanager
def _windows_uia_hit_test():
    """Use UI Automation point lookup and actionable control patterns."""
    class PointStruct(C.Structure):
        _fields_ = [("x", C.c_int32), ("y", C.c_int32)]

    class Guid(C.Structure):
        _fields_ = [
            ("data1", C.c_uint32), ("data2", C.c_uint16), ("data3", C.c_uint16),
            ("data4", C.c_ubyte * 8),
        ]

    class VariantValue(C.Union):
        _fields_ = [
            ("boolean", C.c_int16), ("array", C.c_void_p), ("wide", C.c_int64),
            ("record", C.c_ubyte * 16),
        ]

    class Variant(C.Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("vt", C.c_ushort), ("reserved", C.c_ushort * 3), ("value", VariantValue),
        ]

    def guid(value: str) -> Guid:
        import uuid

        raw = uuid.UUID(value).bytes_le
        return Guid.from_buffer_copy(raw)

    ole32 = C.WinDLL("ole32")
    oleaut = C.WinDLL("oleaut32")
    ole32.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint32]
    ole32.CoInitializeEx.restype = C.c_int32
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        C.POINTER(Guid), C.c_void_p, C.c_uint32, C.POINTER(Guid), C.POINTER(C.c_void_p),
    ]
    ole32.CoCreateInstance.restype = C.c_int32
    oleaut.VariantClear.argtypes = [C.POINTER(Variant)]
    oleaut.VariantClear.restype = C.c_int32
    oleaut.SafeArrayGetDim.argtypes = [C.c_void_p]
    oleaut.SafeArrayGetDim.restype = C.c_uint32
    oleaut.SafeArrayGetLBound.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.c_int32)]
    oleaut.SafeArrayGetLBound.restype = C.c_int32
    oleaut.SafeArrayGetUBound.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.c_int32)]
    oleaut.SafeArrayGetUBound.restype = C.c_int32
    oleaut.SafeArrayGetElement.argtypes = [C.c_void_p, C.POINTER(C.c_int32), C.c_void_p]
    oleaut.SafeArrayGetElement.restype = C.c_int32
    if ole32.CoInitializeEx(None, 0) < 0:
        raise RuntimeError("Snap to could not initialise Windows UI Automation.")

    automation = C.c_void_p()
    walker = C.c_void_p()

    def method(obj, index, result, *args):
        table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result, C.c_void_p, *args)(table[index])

    def release(obj):
        if obj:
            method(obj, 2, C.c_ulong)(obj)

    try:
        clsid = guid("FF48DBA4-60EF-4201-AA87-54103EEF594E")
        iid = guid("30CBE57D-D9D0-452A-AB13-7AC5AC4825EE")
        status = ole32.CoCreateInstance(
            C.byref(clsid), None, 1, C.byref(iid), C.byref(automation),
        )
        if status < 0 or not automation:
            raise RuntimeError("Snap to could not create the Windows UI Automation client.")
        status = method(automation, 14, C.c_int32, C.POINTER(C.c_void_p))(
            automation, C.byref(walker),
        )
        if status < 0 or not walker:
            raise RuntimeError("Snap to could not create a Windows UI Automation tree walker.")

        def property_value(obj, property_id):
            value = Variant()
            status = method(obj, 10, C.c_int32, C.c_int32, C.POINTER(Variant))(
                obj, property_id, C.byref(value),
            )
            if status < 0:
                oleaut.VariantClear(C.byref(value))
                return None
            return value

        def rect_from_variant(value):
            try:
                if value.vt != 0x2005 or not value.array:
                    return None
                array = value.array
                if oleaut.SafeArrayGetDim(array) != 1:
                    return None
                lower, upper = C.c_int32(), C.c_int32()
                if (
                    oleaut.SafeArrayGetLBound(array, 1, C.byref(lower)) < 0
                    or oleaut.SafeArrayGetUBound(array, 1, C.byref(upper)) < 0
                    or upper.value - lower.value + 1 != 4
                ):
                    return None
                values = []
                for index in range(lower.value, upper.value + 1):
                    coordinate = C.c_double()
                    index_arg = C.c_int32(index)
                    if oleaut.SafeArrayGetElement(
                        array, C.byref(index_arg), C.byref(coordinate),
                    ) < 0:
                        return None
                    values.append(coordinate.value)
                return tuple(values)
            finally:
                oleaut.VariantClear(C.byref(value))

        def has_action_pattern(obj):
            for pattern_id in (10000, 10005, 10010, 10015, 10018):
                pattern = C.c_void_p()
                status = method(obj, 16, C.c_int32, C.c_int32, C.POINTER(C.c_void_p))(
                    obj, pattern_id, C.byref(pattern),
                )
                if status >= 0 and pattern:
                    release(pattern)
                    return True
            return False

        def hit_test(point):
            element = C.c_void_p()
            status = method(automation, 7, C.c_int32, PointStruct, C.POINTER(C.c_void_p))(
                automation, PointStruct(round(point[0]), round(point[1])), C.byref(element),
            )
            if status == -2147024891:
                raise RuntimeError(
                    "Windows denied Snap to access. Run OpenHeadMouse with the same "
                    "permissions as the target application."
                )
            if status < 0 or not element:
                return None
            try:
                for _ in range(6):
                    enabled = property_value(element, 30010)
                    try:
                        is_enabled = enabled is not None and enabled.vt == 11 and enabled.boolean != 0
                    finally:
                        if enabled is not None:
                            oleaut.VariantClear(C.byref(enabled))
                    bounds = property_value(element, 30001)
                    rect = rect_from_variant(bounds) if bounds is not None else None
                    if is_enabled and rect is not None and has_action_pattern(element):
                        return rect
                    parent = C.c_void_p()
                    status = method(walker, 3, C.c_int32, C.c_void_p, C.POINTER(C.c_void_p))(
                        walker, element, C.byref(parent),
                    )
                    release(element)
                    element = parent
                    if status < 0 or not element:
                        break
                return None
            finally:
                release(element)

        yield hit_test
    finally:
        release(walker)
        release(automation)
        ole32.CoUninitialize()


@contextmanager
def _windows_hit_test():
    """Use MSAA point lookup, including controls exposed by some UIA-to-MSAA bridges."""
    class PointStruct(C.Structure):
        _fields_ = [("x", C.c_int32), ("y", C.c_int32)]

    class Value(C.Union):
        _fields_ = [("integer", C.c_int32), ("wide", C.c_int64),
                    ("pointer", C.c_void_p), ("record", C.c_void_p * 2)]

    class Variant(C.Structure):
        _anonymous_ = ("value",)
        _fields_ = [("vt", C.c_ushort), ("reserved", C.c_ushort * 3), ("value", Value)]

    ole32 = C.WinDLL("ole32")
    oleacc = C.WinDLL("oleacc")
    oleaut = C.WinDLL("oleaut32")
    ole32.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint32]
    ole32.CoInitializeEx.restype = C.c_int32
    ole32.CoUninitialize.restype = None
    oleacc.AccessibleObjectFromPoint.argtypes = [
        PointStruct, C.POINTER(C.c_void_p), C.POINTER(Variant),
    ]
    oleacc.AccessibleObjectFromPoint.restype = C.c_int32
    oleaut.SysStringLen.argtypes = [C.c_void_p]
    oleaut.SysStringLen.restype = C.c_uint32
    oleaut.SysFreeString.argtypes = [C.c_void_p]
    oleaut.SysFreeString.restype = None
    oleaut.VariantClear.argtypes = [C.POINTER(Variant)]
    oleaut.VariantClear.restype = C.c_int32
    if ole32.CoInitializeEx(None, 2) < 0:
        raise RuntimeError("Snap to could not initialise Windows accessibility.")

    def method(obj, index, result, *args):
        table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result, C.c_void_p, *args)(table[index])

    def hit_test(point):
        obj, child = C.c_void_p(), Variant()
        status = oleacc.AccessibleObjectFromPoint(
            PointStruct(round(point[0]), round(point[1])), C.byref(obj), C.byref(child),
        )
        if status == -2147024891:  # E_ACCESSDENIED
            raise RuntimeError(
                "Windows denied Snap to access. Run OpenHeadMouse with the same "
                "permissions as the target application."
            )
        if status < 0:
            return None  # No accessible object at this point.
        if not obj:
            return None
        try:
            state = Variant()
            status = method(obj, 14, C.c_int32, Variant, C.POINTER(Variant))(
                obj, child, C.byref(state),
            )
            # STATE_SYSTEM_UNAVAILABLE, INVISIBLE and OFFSCREEN.
            enabled = status >= 0 and state.vt == 3 and not state.integer & (1 | 0x8000 | 0x10000)
            oleaut.VariantClear(C.byref(state))
            if not enabled:
                return None
            action = C.c_void_p()
            status = method(obj, 20, C.c_int32, Variant, C.POINTER(C.c_void_p))(
                obj, child, C.byref(action),
            )
            try:
                actionable = status >= 0 and action and oleaut.SysStringLen(action) > 0
            finally:
                if action:
                    oleaut.SysFreeString(action)
            if not actionable:
                return None
            rect = [C.c_int32() for _ in range(4)]
            status = method(
                obj, 22, C.c_int32, *([C.POINTER(C.c_int32)] * 4), Variant,
            )(obj, *(C.byref(value) for value in rect), child)
            return tuple(value.value for value in rect) if status >= 0 else None
        finally:
            oleaut.VariantClear(C.byref(child))
            method(obj, 2, C.c_ulong)(obj)

    try:
        yield hit_test
    finally:
        ole32.CoUninitialize()


@contextmanager
def _macos_hit_test():
    ax = C.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    def bind(lib, name, result, *args):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, list(args)
        return function

    release = bind(cf, "CFRelease", None, C.c_void_p)
    string = bind(cf, "CFStringCreateWithCString", C.c_void_p,
                  C.c_void_p, C.c_char_p, C.c_uint32)
    count = bind(cf, "CFArrayGetCount", C.c_long, C.c_void_p)
    item = bind(cf, "CFArrayGetValueAtIndex", C.c_void_p, C.c_void_p, C.c_long)
    equal = bind(cf, "CFEqual", C.c_bool, C.c_void_p, C.c_void_p)
    get_bool = bind(cf, "CFBooleanGetValue", C.c_bool, C.c_void_p)
    trusted = bind(ax, "AXIsProcessTrusted", C.c_bool)
    if not trusted():
        raise RuntimeError(
            "Snap to needs Accessibility permission in System Settings > Privacy & Security "
            "> Accessibility. Allow OpenHeadMouse (or the Python executable), then try again."
        )
    system = bind(ax, "AXUIElementCreateSystemWide", C.c_void_p)()
    lookup = bind(ax, "AXUIElementCopyElementAtPosition", C.c_int32,
                  C.c_void_p, C.c_float, C.c_float, C.POINTER(C.c_void_p))
    attribute = bind(ax, "AXUIElementCopyAttributeValue", C.c_int32,
                     C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p))
    actions = bind(ax, "AXUIElementCopyActionNames", C.c_int32,
                   C.c_void_p, C.POINTER(C.c_void_p))
    value = bind(ax, "AXValueGetValue", C.c_bool, C.c_void_p, C.c_int32, C.c_void_p)
    bind(ax, "AXUIElementSetMessagingTimeout", C.c_int32, C.c_void_p, C.c_float)(system, 0.1)
    names = {name: string(None, name.encode(), 0x08000100)
             for name in ("AXEnabled", "AXPosition", "AXSize", "AXParent", "AXPress")}

    def copy(obj, name):
        result = C.c_void_p()
        return result if attribute(obj, names[name], C.byref(result)) == 0 else None

    def hit_test(point):
        obj = C.c_void_p()
        if lookup(system, point[0], point[1], C.byref(obj)) != 0:
            return None
        try:
            # Text inside a button may be the hit; walk only a few ancestors.
            for _ in range(5):
                enabled = copy(obj, "AXEnabled")
                try:
                    if enabled and not get_bool(enabled):
                        return None
                finally:
                    if enabled:
                        release(enabled)
                available = C.c_void_p()
                press = False
                if actions(obj, C.byref(available)) == 0:
                    try:
                        press = any(equal(item(available, i), names["AXPress"])
                                    for i in range(count(available)))
                    finally:
                        release(available)
                if press:
                    position, size = copy(obj, "AXPosition"), copy(obj, "AXSize")
                    try:
                        xy, wh = (C.c_double * 2)(), (C.c_double * 2)()
                        if position and size and value(position, 1, xy) and value(size, 2, wh):
                            return xy[0], xy[1], wh[0], wh[1]
                    finally:
                        if position:
                            release(position)
                        if size:
                            release(size)
                    return None
                parent = copy(obj, "AXParent")
                release(obj)
                obj = parent
                if not obj:
                    break
            return None
        finally:
            if obj:
                release(obj)

    try:
        yield hit_test
    finally:
        release(system)
        for name in names.values():
            release(name)


@contextmanager
def _linux_hit_test():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise RuntimeError("Snap to requires an X11 session; Wayland pointer control is not supported.")
    library = ctypes.util.find_library("atspi")
    if library is None:
        raise RuntimeError("Snap to requires AT-SPI 2. Install libatspi2.0-0 and enable desktop accessibility.")
    atspi = C.CDLL(library)
    gobject = C.CDLL(ctypes.util.find_library("gobject-2.0"))
    glib = C.CDLL(ctypes.util.find_library("glib-2.0"))

    class Rect(C.Structure):
        _fields_ = [(name, C.c_int) for name in ("x", "y", "width", "height")]

    class Error(C.Structure):
        _fields_ = [("domain", C.c_uint32), ("code", C.c_int), ("message", C.c_char_p)]

    def bind(lib, name, result, *args):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, list(args)
        return function

    unref = bind(gobject, "g_object_unref", None, C.c_void_p)
    free = bind(glib, "g_free", None, C.c_void_p)
    error_free = bind(glib, "g_error_free", None, C.POINTER(Error))
    error_arg = C.POINTER(C.POINTER(Error))

    def call(name, result, types, *args):
        error = C.POINTER(Error)()
        result = bind(atspi, name, result, *types, error_arg)(*args, C.byref(error))
        if error:
            try:
                message = error.contents.message.decode("utf-8", errors="replace")
            finally:
                error_free(error)
            raise RuntimeError(f"Snap to: AT-SPI could not inspect a control: {message}")
        return result

    if bind(atspi, "atspi_init", C.c_int)() not in (0, 1):
        raise RuntimeError("Snap to could not connect to AT-SPI. Enable desktop accessibility.")
    bind(atspi, "atspi_set_timeout", None, C.c_int, C.c_int)(100, 300)
    desktop = bind(atspi, "atspi_get_desktop", C.c_void_p, C.c_int)(0)
    if not desktop:
        raise RuntimeError("Snap to cannot find the AT-SPI desktop. Enable desktop accessibility.")
    component = bind(atspi, "atspi_accessible_get_component_iface", C.c_void_p, C.c_void_p)
    action = bind(atspi, "atspi_accessible_get_action_iface", C.c_void_p, C.c_void_p)
    states = bind(atspi, "atspi_accessible_get_state_set", C.c_void_p, C.c_void_p)
    contains = bind(atspi, "atspi_state_set_contains", C.c_int, C.c_void_p, C.c_int)

    def children(obj):
        count = call("atspi_accessible_get_child_count", C.c_int, [C.c_void_p], obj)
        for index in range(min(count, 128)):
            child = call("atspi_accessible_get_child_at_index", C.c_void_p,
                         [C.c_void_p, C.c_int], obj, index)
            if child:
                yield child

    def hit_control(obj, point, depth=0):
        if depth > 12:
            return None
        comp = component(obj)
        if not comp:
            return None
        try:
            # ATSPI_COORD_TYPE_SCREEN = 0.
            inside = call("atspi_component_contains", C.c_int,
                          [C.c_void_p, C.c_int, C.c_int, C.c_int],
                          comp, round(point[0]), round(point[1]), 0)
            if not inside:
                return None
            child = call("atspi_component_get_accessible_at_point", C.c_void_p,
                         [C.c_void_p, C.c_int, C.c_int, C.c_int],
                         comp, round(point[0]), round(point[1]), 0)
            if child:
                try:
                    if child != obj:
                        target = hit_control(child, point, depth + 1)
                        if target is not None:
                            return target
                finally:
                    unref(child)
            state = states(obj)
            try:
                # ATSPI_STATE_ENABLED = 8, SHOWING = 25.
                enabled = state and contains(state, 8) and contains(state, 25)
            finally:
                if state:
                    unref(state)
            if not enabled:
                return None
            iface = action(obj)
            if not iface:
                return None
            try:
                count = call("atspi_action_get_n_actions", C.c_int, [C.c_void_p], iface)
                if count <= 0:
                    return None
            finally:
                unref(iface)
            rect = call("atspi_component_get_extents", C.POINTER(Rect),
                        [C.c_void_p, C.c_int], comp, 0)
            if not rect:
                return None
            try:
                return tuple(getattr(rect.contents, name) for name in ("x", "y", "width", "height"))
            finally:
                free(rect)
        finally:
            unref(comp)

    def hit_test(point):
        # Restrict Linux to the active window so covered background windows cannot attract the cursor.
        for app in children(desktop):
            try:
                for window in children(app):
                    try:
                        state = states(window)
                        try:
                            active = state and contains(state, 1)  # ATSPI_STATE_ACTIVE
                        finally:
                            if state:
                                unref(state)
                        if active:
                            target = hit_control(window, point)
                            if target is not None:
                                return target
                    finally:
                        unref(window)
            finally:
                unref(app)
        return None

    try:
        yield hit_test
    finally:
        unref(desktop)
