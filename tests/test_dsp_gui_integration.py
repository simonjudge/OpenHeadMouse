"""Deterministic GUI -> DSP -> fake OS cursor regression coverage."""

import queue
from types import SimpleNamespace

import numpy as np
import pytest

from core import quaternion_math as qm
from main import App, load_config
from providers.base_provider import ProviderSample


class FakeMouse:
    position = (400.0, 400.0)


@pytest.fixture
def controller(qt_app, tmp_path):
    config = load_config()
    config["dsp"]["mode"] = "absolute"
    config["dsp"]["absolute"] = {"kx": 10, "ky": -10}
    config["dsp"]["velocity"] = {"kx": 1, "ky": 1, "gamma": 1, "deadband_deg_s": 0}
    config["dsp"]["one_euro_filter"] = {
        "enabled": False, "min_cutoff": 0.8, "beta": 0.2, "d_cutoff": 1.2,
    }
    config["dsp"]["auto_zero"]["enabled"] = False
    config["dsp"]["yaw_drift_correction"]["enabled"] = False
    config["recorder"]["output_dir"] = str(tmp_path)
    app = App(config, auto_start_listeners=False, mouse_controller=FakeMouse())
    app._timer.stop()
    app.window.controls._mouse_check.setChecked(True)
    app.window.controls._record_check.setChecked(False)
    app._provider = SimpleNamespace(output_queue=queue.Queue(), stop=lambda: None)
    app.headmouse.set_tracking_state(True)
    yield app
    app.shutdown()
    app.window.close()


def feed(app, yaw, timestamp, pitch=0):
    pose = qm.from_euler_zyx(np.radians(yaw), np.radians(pitch), 0)
    app._provider.output_queue.put(ProviderSample(timestamp, pose))
    app._poll()
    return app.headmouse.cursor_position


def test_absolute_targets_recenter_and_edge_recovery(controller):
    app = controller
    cx = app.dsp.origin_x + app.dsp.screen_width / 2
    cy = app.dsp.origin_y + app.dsp.screen_height / 2
    assert feed(app, 10, 0) == pytest.approx((cx + 100, cy))
    assert feed(app, 20, 1) == pytest.approx((cx + 200, cy))
    feed(app, 80, 2)
    assert feed(app, 0, 3) == pytest.approx((cx, cy))
    feed(app, 20, 4)
    app._on_recenter()  # Uses the last sample even when the queue is empty.
    assert app.headmouse.cursor_position == pytest.approx((cx, cy))
    assert feed(app, 20, 5) == pytest.approx((cx, cy))


def test_live_velocity_edits_do_not_teleport_cursor(controller):
    app = controller
    feed(app, 10, 0)
    position = app.headmouse.cursor_position
    app.window._mode_combo.setCurrentText("Velocity")
    assert feed(app, 10, 1) == pytest.approx(position)
    moved = feed(app, 20, 2, pitch=10)
    assert moved == pytest.approx((position[0] + 10, position[1] - 10))
    position = moved
    for change in (
        lambda: app.window._velocity_kx_spin.setValue(2),
        lambda: app.window._filter_preset_combo.setCurrentText("High filtering"),
        lambda: app.window._one_euro_check.setChecked(True),
        lambda: app.window._auto_zero_check.setChecked(True),
    ):
        change()
        assert feed(app, 20, 3, pitch=10) == pytest.approx(position)


def test_live_absolute_preset_changes_actual_cursor_response(controller):
    app = controller
    center = feed(app, 0, 0)
    raw = feed(app, 1, 1 / 60)
    assert raw[0] - center[0] == pytest.approx(10)
    app.window._filter_preset_combo.setCurrentText("High filtering")
    app.window._one_euro_check.setChecked(True)
    feed(app, 0, 1)
    smoothed = feed(app, 1, 1 + 1 / 60)
    assert 0 < smoothed[0] - center[0] < 5


def test_presets_preserve_custom_config_then_update_live_engine(controller):
    app = controller
    assert app.window._filter_preset_combo.currentText() == "Custom (from config)"
    app.window._one_euro_check.setChecked(True)
    assert app.dsp.one_euro_filter.min_cutoff == 0.8
    assert app.dsp.one_euro_filter.beta == 0.2
    assert app.dsp.one_euro_filter.d_cutoff == 1.2
    app.window._filter_preset_combo.setCurrentText("High filtering")
    assert app.dsp.one_euro_filter.min_cutoff == 0.4
    assert app.dsp.one_euro_filter.beta == 0.15


@pytest.mark.parametrize("pause_method", ["hotkey", "checkbox"])
def test_velocity_resume_uses_actual_mouse_position(controller, pause_method):
    app = controller
    app.window._mode_combo.setCurrentText("Velocity")
    feed(app, 0, 0)
    feed(app, 10, 1)
    if pause_method == "hotkey":
        app.headmouse.set_tracking_state(False)
    else:
        app.window.controls._mouse_check.setChecked(False)
    feed(app, 30, 2)
    app.headmouse._mouse.position = (250.0, 300.0)
    if pause_method == "hotkey":
        app.headmouse.set_tracking_state(True)
    else:
        app.window.controls._mouse_check.setChecked(True)
    assert feed(app, 30, 3) == pytest.approx((250, 300))
    assert feed(app, 40, 4) == pytest.approx((260, 300))
