"""Unit tests for core/headmouse.py.

Uses a fake mouse controller and an injectable fake clock so these tests
never touch the real OS cursor or real pynput listeners (start()/stop(),
which register real global OS hooks, are intentionally not exercised here —
see the module docstring and the plan's M5 note: that path is verified
manually/interactively).
"""

import pytest

from core.headmouse import HeadMouse, ScreenBounds, TrackingState


class FakeMouseController:
    def __init__(self, start=(0.0, 0.0)):
        self._position = start

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_headmouse(width=1920, height=1080, corner_region_px=20, corner_failsafe_ms=500.0, clock=None):
    clock = clock or FakeClock()
    hm = HeadMouse(
        screen_bounds=ScreenBounds(width=width, height=height),
        corner_region_px=corner_region_px,
        corner_failsafe_ms=corner_failsafe_ms,
        clock=clock,
        mouse_controller=FakeMouseController(),
    )
    return hm, clock


def test_process_frame_is_a_noop_while_paused():
    hm, _clock = make_headmouse()
    hm.process_frame(100.0, 100.0, timestamp=0.0)
    assert hm._mouse.position == (0.0, 0.0)


def test_process_frame_moves_cursor_when_active():
    hm, _clock = make_headmouse()
    hm.set_tracking_state(True)
    hm.process_frame(50.0, -30.0, timestamp=0.0)
    # starting position was (0,0); clamped to [0, height-1] so negative y clamps to 0
    assert hm._mouse.position == (50.0, 0.0)


def test_process_frame_clamps_to_screen_bounds():
    hm, _clock = make_headmouse(width=1920, height=1080)
    hm.set_tracking_state(True)
    hm.process_frame(delta_x=5000.0, delta_y=5000.0, timestamp=0.0)
    assert hm._mouse.position == (1919.0, 1079.0)


def test_process_frame_clamps_to_virtual_desktop_with_negative_origin():
    hm, _clock = make_headmouse(width=100, height=80)
    hm.screen_bounds.origin_x = -100
    hm.screen_bounds.origin_y = -40
    hm._position = [-50.0, -20.0]
    hm.set_tracking_state(True)

    hm.process_frame(delta_x=-1000.0, delta_y=-1000.0, timestamp=0.0)
    assert hm._mouse.position == (-100.0, -40.0)

    hm.process_frame(delta_x=1000.0, delta_y=1000.0, timestamp=0.0)
    assert hm._mouse.position == (-1.0, 39.0)


def test_center_cursor_moves_to_the_active_screen_center():
    hm, _clock = make_headmouse(width=100, height=80)
    hm.set_screen_bounds(ScreenBounds(width=100, height=80, origin_x=-100, origin_y=-40))

    assert hm.center_cursor() == (-50.0, 0.0)
    assert hm._mouse.position == (-50.0, 0.0)
    assert hm._position == [-50.0, 0.0]


def test_disable_prevents_reactivation():
    hm, _clock = make_headmouse()
    hm.disable()
    hm.set_tracking_state(True)
    assert hm.state == TrackingState.DISABLED
    hm.process_frame(10.0, 10.0, timestamp=0.0)
    assert hm._mouse.position == (0.0, 0.0)


def test_state_callback_fires_on_transitions():
    hm, _clock = make_headmouse()
    seen = []
    hm.add_state_callback(seen.append)
    hm.set_tracking_state(True)
    hm.set_tracking_state(False)
    hm.disable()
    assert seen == [TrackingState.ACTIVE, TrackingState.PAUSED, TrackingState.DISABLED]


def test_macos_access_check_is_noop_on_non_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    HeadMouse._check_macos_input_access()


def test_own_injected_move_does_not_trigger_corner_failsafe():
    hm, clock = make_headmouse(width=200, height=200, corner_region_px=20, corner_failsafe_ms=500.0)
    hm.set_tracking_state(True)
    # Drive the cursor into the corner via our own process_frame (simulates
    # legitimate head-tracked movement landing in a corner).
    hm.process_frame(delta_x=-5000.0, delta_y=-5000.0, timestamp=0.0)
    assert hm._mouse.position == (0.0, 0.0)

    # The listener echo of that same injected move must be ignored.
    hm._on_manual_mouse_move(0.0, 0.0)
    clock.advance(1.0)
    hm._on_manual_mouse_move(0.0, 0.0)
    assert hm.state == TrackingState.ACTIVE


def test_manual_mouse_in_corner_over_threshold_pauses_tracking():
    hm, clock = make_headmouse(width=200, height=200, corner_region_px=20, corner_failsafe_ms=500.0)
    hm.set_tracking_state(True)

    hm._on_manual_mouse_move(0.0, 0.0)  # enters corner at t=0
    assert hm.state == TrackingState.ACTIVE

    clock.advance(0.6)  # 600ms > 500ms threshold
    hm._on_manual_mouse_move(0.0, 0.0)
    assert hm.state == TrackingState.PAUSED


def test_manual_mouse_in_corner_under_threshold_does_not_pause():
    hm, clock = make_headmouse(width=200, height=200, corner_region_px=20, corner_failsafe_ms=500.0)
    hm.set_tracking_state(True)

    hm._on_manual_mouse_move(0.0, 0.0)
    clock.advance(0.2)  # 200ms < 500ms threshold
    hm._on_manual_mouse_move(0.0, 0.0)
    assert hm.state == TrackingState.ACTIVE


def test_leaving_corner_resets_the_failsafe_timer():
    hm, clock = make_headmouse(width=200, height=200, corner_region_px=20, corner_failsafe_ms=500.0)
    hm.set_tracking_state(True)

    hm._on_manual_mouse_move(0.0, 0.0)  # enters corner
    clock.advance(0.4)
    hm._on_manual_mouse_move(100.0, 100.0)  # leaves corner before threshold, timer resets
    clock.advance(0.4)
    hm._on_manual_mouse_move(0.0, 0.0)  # re-enters corner; only 0s elapsed since re-entry
    assert hm.state == TrackingState.ACTIVE


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (0, 0, True),
        (199, 199, True),
        (199, 0, True),
        (0, 199, True),
        (100, 100, False),
        (0, 100, False),
    ],
)
def test_is_in_corner_geometry(x, y, expected):
    hm, _clock = make_headmouse(width=200, height=200, corner_region_px=20)
    assert hm._is_in_corner(x, y) == expected
