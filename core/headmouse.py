"""Bridges dsp_engine outputs to physical OS cursor control (spec section 6).

Implements the module interface from the spec: ``process_frame(delta_x,
delta_y, timestamp)`` and ``set_tracking_state(enabled)``. Owns:
  - OS cursor injection via ``pynput.mouse.Controller``
  - multi-monitor screen bounds clamping, via a caller-supplied
    :class:`ScreenBounds` (populated from ``QGuiApplication.screens()`` by
    the GUI layer in a later milestone — this module stays Qt-independent so
    it's unit-testable without a running QApplication)
  - a global toggle hotkey (``pynput.keyboard.GlobalHotKeys``)
  - the corner failsafe: halts tracking if the OS cursor sits in a screen
    corner for > ``corner_failsafe_ms``, but only counts *manual* input, not
    this module's own injected movement. Distinguished via an independent
    ``pynput.mouse.Listener`` that compares each observed move to the last
    position this module itself injected (pynput's system-wide listener
    reports our own injected moves too, so this filter is required).

``process_frame`` takes a delta (not an absolute target), matching the
spec's literal signature; dsp_engine.py's Mode A (absolute mapping) computes
deltas as (new absolute target - last commanded position) so this module can
stay mode-agnostic between Mode A and Mode B (velocity mapping, added later).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from pynput import keyboard, mouse


class TrackingState(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


@dataclass
class ScreenBounds:
    width: int
    height: int


class HeadMouse:
    def __init__(
        self,
        screen_bounds: ScreenBounds,
        corner_region_px: int = 20,
        corner_failsafe_ms: float = 500.0,
        hotkey: str = "<cmd>+<alt>+m",
        clock=time.monotonic,
        mouse_controller=None,
    ):
        self.screen_bounds = screen_bounds
        self.corner_region_px = corner_region_px
        self.corner_failsafe_ms = corner_failsafe_ms
        self._clock = clock
        self._mouse = mouse_controller if mouse_controller is not None else mouse.Controller()
        self._hotkey_str = hotkey

        self._state = TrackingState.PAUSED  # starts paused until the GUI/main explicitly activates
        self._state_callbacks: list = []

        try:
            start_x, start_y = self._mouse.position
        except Exception:
            start_x, start_y = 0.0, 0.0
        self._position = [float(start_x), float(start_y)]

        self._injected_lock = threading.Lock()
        self._last_injected_position: tuple[float, float] | None = None
        self._corner_entry_time: float | None = None

        self._hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._mouse_listener: mouse.Listener | None = None

    # -- state management ----------------------------------------------------
    @property
    def state(self) -> TrackingState:
        return self._state

    def add_state_callback(self, callback) -> None:
        """Register a callback(new_state: TrackingState) for gui/ status indicators."""
        self._state_callbacks.append(callback)

    def _set_state(self, new_state: TrackingState) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        for callback in self._state_callbacks:
            callback(new_state)

    def set_tracking_state(self, enabled: bool) -> None:
        if self._state == TrackingState.DISABLED:
            return
        self._set_state(TrackingState.ACTIVE if enabled else TrackingState.PAUSED)
        if enabled:
            self._corner_entry_time = None  # resuming shouldn't inherit a stale corner timer

    def disable(self) -> None:
        self._set_state(TrackingState.DISABLED)

    # -- OS listener lifecycle (native hotkey + failsafe input tap) ---------
    def start(self) -> None:
        # macOS requires Accessibility/Input Monitoring permission for global
        # hooks; without it pynput can raise rather than just silently not
        # deliver events. Treat that as degraded-but-running (GUI toggle
        # still works via set_tracking_state) rather than a fatal error.
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({self._hotkey_str: self._toggle_tracking})
            self._hotkey_listener.start()
            print(f"✓ Global hotkey '{self._hotkey_str}' registered successfully")
        except Exception as e:
            print(f"✗ Failed to register hotkey '{self._hotkey_str}': {e}")
            print(f"  On macOS, the app needs Accessibility permission.")
            print(f"  Go to System Settings > Privacy & Security > Accessibility and allow this app.")
            self._hotkey_listener = None
        try:
            self._mouse_listener = mouse.Listener(on_move=self._on_manual_mouse_move)
            self._mouse_listener.start()
        except Exception as e:
            print(f"✗ Failed to register mouse listener: {e}")
            self._mouse_listener = None

    def stop(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def _toggle_tracking(self) -> None:
        self.set_tracking_state(self._state != TrackingState.ACTIVE)

    # -- corner failsafe -------------------------------------------------------
    def _is_in_corner(self, x: float, y: float) -> bool:
        w, h = self.screen_bounds.width, self.screen_bounds.height
        r = self.corner_region_px
        in_x_edge = x <= r or x >= (w - 1 - r)
        in_y_edge = y <= r or y >= (h - 1 - r)
        return in_x_edge and in_y_edge

    def _on_manual_mouse_move(self, x: float, y: float) -> None:
        """pynput.mouse.Listener callback; fires for BOTH manual and injected moves."""
        if self._state != TrackingState.ACTIVE:
            return

        with self._injected_lock:
            last_injected = self._last_injected_position
        if last_injected is not None and abs(x - last_injected[0]) < 1e-6 and abs(y - last_injected[1]) < 1e-6:
            return  # this event is an echo of our own injected move, not manual input

        now = self._clock()
        if self._is_in_corner(x, y):
            if self._corner_entry_time is None:
                self._corner_entry_time = now
            elif (now - self._corner_entry_time) * 1000.0 >= self.corner_failsafe_ms:
                self.set_tracking_state(False)
        else:
            self._corner_entry_time = None

    # -- main input interface (spec section 6) ---------------------------------
    def process_frame(self, delta_x: float, delta_y: float, timestamp: float) -> None:
        if self._state != TrackingState.ACTIVE:
            return

        x = max(0.0, min(float(self.screen_bounds.width - 1), self._position[0] + delta_x))
        y = max(0.0, min(float(self.screen_bounds.height - 1), self._position[1] + delta_y))
        self._position = [x, y]

        # Tag the position as "ours" before injecting it, so the mouse
        # listener thread (which may run this callback concurrently) sees
        # the expected position at essentially the same moment the OS event
        # it will observe is generated.
        with self._injected_lock:
            self._last_injected_position = (x, y)
        self._mouse.position = (x, y)
