"""Tests for the initial (neutral-pose) calibration overlay and App flow."""

import copy
import json
import time

import numpy as np
import pytest


class FakeMouseController:
    def __init__(self):
        self.position = (0.0, 0.0)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _pump(qt_app, seconds: float, until=None) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def test_overlay_phases_and_countdown(qt_app):
    from gui.calibration_overlay import CalibrationOverlay

    clock = FakeClock()
    overlay = CalibrationOverlay(read_duration_s=5.0, capture_duration_s=5.0, clock=clock)
    events = []
    overlay.capture_started.connect(lambda: events.append("capture"))
    overlay.finished.connect(lambda: events.append("finished"))
    overlay.start()
    assert "neutral position" in overlay.message
    assert "5 seconds" in overlay.message
    assert overlay.phase == overlay.PHASE_READ
    assert overlay.status_text() == "Get ready…"

    clock.now = 5.0
    overlay._tick()
    assert events == ["capture"]
    assert overlay.status_text() == "Hold still: 5"
    clock.now = 8.5
    assert overlay.status_text() == "Hold still: 2"
    clock.now = 10.0
    overlay._tick()
    assert events == ["capture", "finished"]
    assert overlay.phase == overlay.PHASE_DONE


def test_overlay_escape_cancels(qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from gui.calibration_overlay import CalibrationOverlay

    overlay = CalibrationOverlay(clock=FakeClock())
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start()
    QTest.keyClick(overlay, Qt.Key_Escape)
    assert cancelled == [True]
    assert overlay.phase == overlay.PHASE_DONE


@pytest.fixture
def make_app(qt_app, tmp_path):
    from main import App, load_config

    apps = []

    def factory(recording=True, read_s=0.1, capture_s=0.4):
        config = copy.deepcopy(load_config())
        config["ui"]["calibration_enabled"] = True
        config["ui"]["recording_enabled"] = recording
        config["ui"]["mouse_control_enabled"] = True
        config["calibration"] = {"read_duration_s": read_s, "capture_duration_s": capture_s}
        config["recorder"]["output_dir"] = str(tmp_path)
        config["recorder"]["format"] = "json"
        app = App(config, auto_start_listeners=False, mouse_controller=FakeMouseController())
        apps.append(app)
        return app

    yield factory
    for app in apps:
        app._on_stop_requested()
        app.shutdown()


def test_calibration_blocks_movement_then_recenters_and_saves(qt_app, make_app, tmp_path):
    app = make_app()
    app._on_start_requested("mock")
    assert app._calibrating
    assert app._calibration_overlay.isVisible()
    assert app.headmouse.state.value == "paused"
    centre = app.headmouse.cursor_position

    _pump(qt_app, 0.3)
    # During calibration the cursor must not move and no movement data is recorded.
    assert app.headmouse.cursor_position == centre
    assert app._recorder.sample_count == 0

    _pump(qt_app, 2.0, until=lambda: not app._calibrating)
    assert not app._calibrating
    assert app._calibration_overlay is None
    assert app.headmouse.state.value == "active"
    assert app._recorder.has_calibration
    calibration = app._recorder.calibration
    assert calibration["sample_count"] > 0
    np.testing.assert_allclose(app.dsp._q_neutral, calibration["neutral_quaternion"])

    _pump(qt_app, 0.2)
    assert app._recorder.sample_count > 0

    app.window.setup_panel._on_stop_clicked()
    files = list(tmp_path.glob("session_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["calibration"]["sample_count"] == calibration["sample_count"]
    assert len(data["calibration"]["samples"]) == calibration["sample_count"]
    assert any(
        e["event_type"] == "recenter" and e["data"].get("source") == "calibration"
        for e in data["events"]
    )


def test_calibration_not_saved_when_recording_disabled(qt_app, make_app):
    app = make_app(recording=False)
    app._on_start_requested("mock")
    _pump(qt_app, 2.0, until=lambda: not app._calibrating)
    assert app.headmouse.state.value == "active"
    assert not app._recorder.has_calibration
    assert not np.allclose(app.dsp._q_neutral, [1.0, 0.0, 0.0, 0.0])


def test_escape_on_overlay_aborts_and_stops_provider(qt_app, make_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app = make_app(read_s=5.0)
    app._on_start_requested("mock")
    QTest.keyClick(app._calibration_overlay, Qt.Key_Escape)
    assert not app._calibrating
    assert app._provider is None
    assert app.headmouse.state.value == "paused"
    assert app.window.setup_panel._start_button.isEnabled()
    assert not app._recorder.has_calibration


def test_global_hotkey_during_calibration_aborts(qt_app, make_app):
    app = make_app(read_s=5.0)
    app._on_start_requested("mock")
    app.headmouse._toggle_tracking()  # simulates the global Esc hotkey
    app._poll()
    assert not app._calibrating
    assert app._provider is None
    assert app.headmouse.state.value == "paused"


def test_stop_button_during_calibration_aborts(qt_app, make_app):
    app = make_app(read_s=5.0)
    app._on_start_requested("mock")
    overlay = app._calibration_overlay
    app.window.setup_panel._on_stop_clicked()
    assert not app._calibrating
    assert app._provider is None
    assert not overlay.isVisible()


def test_calibration_without_samples_warns_and_starts_tracking(qt_app, make_app, monkeypatch):
    app = make_app()
    monkeypatch.setattr(app, "_drain_all", lambda: [])
    app._on_start_requested("mock")
    _pump(qt_app, 2.0, until=lambda: not app._calibrating)
    assert app.headmouse.state.value == "active"
    assert not app._recorder.has_calibration
    assert app._calibration_warning is not None
    app._calibration_warning.close()


def test_ximu_wifi_waits_for_samples_before_initial_calibration(qt_app, make_app):
    from providers.ximu3_provider import Ximu3Provider
    from providers.base_provider import ProviderSample

    app = make_app()
    provider = Ximu3Provider(connection_type="wifi_ap")
    app._provider = provider
    app._ximu_wifi_provider = True
    app._ximu_waiting_to_start = True
    app._poll()
    assert not app._calibrating
    assert app.headmouse.state.value != "active"

    provider._set_link_status("streaming")
    provider._emit(ProviderSample(timestamp=1.0, quaternion=np.array([1., 0., 0., 0.])))
    app._poll()
    assert app._calibrating
    assert app._calibration_overlay.isVisible()

    provider._set_link_status("reconnecting")
    app._poll()
    assert not app._calibrating
    assert app._ximu_waiting_to_start
    provider._set_link_status("streaming")
    provider._emit(ProviderSample(timestamp=2.0, quaternion=np.array([1., 0., 0., 0.])))
    app._poll()
    assert app._calibrating


def test_ximu_wifi_reconnect_preserves_neutral(qt_app, make_app):
    from providers.ximu3_provider import Ximu3Provider
    from providers.base_provider import ProviderSample

    app = make_app()
    app.window.controls._calibration_check.setChecked(False)
    provider = Ximu3Provider(connection_type="wifi_ap")
    app._provider = provider
    app._ximu_wifi_provider = True
    app._ximu_waiting_to_start = True
    reference = np.array([0.96592583, 0., 0., 0.25881905])
    app.dsp.recenter(reference)
    provider._set_link_status("streaming")
    provider._emit(ProviderSample(timestamp=1.0, quaternion=reference))
    app._poll()
    assert app.headmouse.state.value == "active"
    provider._set_link_status("reconnecting")
    app._poll()
    provider._set_link_status("streaming")
    provider._emit(ProviderSample(timestamp=10.0, quaternion=reference))
    app._poll()
    np.testing.assert_allclose(app.dsp._q_neutral, reference)
    assert app.headmouse.state.value == "active"


def test_ximu_direct_connect_available_without_announcements(qt_app, make_app, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QPushButton
    from providers import ximu3_provider

    app = make_app()
    monkeypatch.setattr(ximu3_provider, "discover_ximu3_devices", lambda **kwargs: [])
    monkeypatch.setattr(ximu3_provider.Ximu3Provider, "start", lambda self: None)
    app._config["providers"]["ximu3"].update({
        "wifi_ip_address": "192.168.1.42", "wifi_port_send": 9123,
        "wifi_port_receive": 8123,
    })

    def choose_direct():
        dialog = QApplication.activeModalWidget()
        assert dialog is not None
        combo = dialog.findChild(QComboBox)
        assert combo.count() == 1
        assert combo.currentData().ip_address == "192.168.1.42"
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Connect").click()

    QTimer.singleShot(0, choose_direct)
    app._on_start_requested("ximu3")
    assert app._provider.wifi_ip_address == "192.168.1.42"
    assert (app._provider.wifi_port_send, app._provider.wifi_port_receive) == (9123, 8123)
    assert app._ximu_waiting_to_start


def test_ximu_usb_selection_survives_invalid_direct_wifi_config(qt_app, make_app, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QPushButton
    from providers import ximu3_provider

    app = make_app()
    app._config["providers"]["ximu3"]["wifi_ip_address"] = "invalid address"
    monkeypatch.setattr(
        ximu3_provider, "discover_ximu3_devices",
        lambda **kwargs: [ximu3_provider.DiscoveredDevice(
            name="USB device", connection_type="usb", port_name="/dev/test"
        )],
    )
    monkeypatch.setattr(ximu3_provider.Ximu3Provider, "start", lambda self: None)

    def select_usb():
        dialog = QApplication.activeModalWidget()
        combo = dialog.findChild(QComboBox)
        if combo.count() == 0:
            QTimer.singleShot(50, select_usb)
            return
        assert combo.currentData().connection_type == "usb"
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Connect").click()

    QTimer.singleShot(0, select_usb)
    app._on_start_requested("ximu3")
    assert app._provider.connection_type == "usb"
