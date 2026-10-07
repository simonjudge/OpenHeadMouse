"""GUI smoke tests: verify widgets build headlessly and the App wiring moves
data end-to-end (provider -> dsp_engine -> headmouse -> telemetry) using the
synthetic MockProvider. Real OS-level listeners (global hotkey, mouse
tap) are not started here (auto_start_listeners=False) — that path needs a
real display/Accessibility permission and is verified interactively per the
plan's M6 note. Requires QT_QPA_PLATFORM=offscreen (see conftest.py).
"""

import time
import copy
import json

import pytest


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_setup_panel_start_stop_signals(qt_app):
    from gui.setup_gui import SetupPanel

    panel = SetupPanel()
    seen_start = []
    panel.start_requested.connect(seen_start.append)
    seen_stop = []
    panel.stop_requested.connect(lambda: seen_stop.append(True))

    panel._start_button.click()
    assert seen_start == ["uniface"]
    assert not panel._start_button.isEnabled()
    assert panel._stop_button.isEnabled()

    panel._stop_button.click()
    assert seen_stop == [True]
    assert panel._start_button.isEnabled()
    assert not panel._stop_button.isEnabled()


def test_setup_panel_only_shows_airpods_when_enabled(qt_app):
    from gui.setup_gui import SetupPanel

    non_mac_panel = SetupPanel(show_airpods=False)
    assert non_mac_panel._provider_combo.findData("airpods") == -1

    mac_panel = SetupPanel(show_airpods=True)
    assert mac_panel._provider_combo.findData("airpods") != -1


def test_ximu3_ignore_magnetometer_button_visibility_and_signal(qt_app):
    from gui.setup_gui import SetupPanel

    panel = SetupPanel()
    # uniface is selected by default; the xIMU3-only button should be hidden.
    assert panel._ximu3_ignore_mag_button.isHidden()

    ximu3_index = panel._provider_combo.findData("ximu3")
    panel._provider_combo.setCurrentIndex(ximu3_index)
    assert not panel._ximu3_ignore_mag_button.isHidden()

    seen = []
    panel.ximu3_ignore_magnetometer_toggled.connect(seen.append)
    panel._ximu3_ignore_mag_button.click()
    assert seen == [True]
    assert "On" in panel._ximu3_ignore_mag_button.text()
    assert panel.ximu3_ignore_magnetometer_enabled is True

    panel._ximu3_ignore_mag_button.click()
    assert seen == [True, False]
    assert "Off" in panel._ximu3_ignore_mag_button.text()

    # Switching away from xIMU3 hides the button again.
    panel._provider_combo.setCurrentIndex(panel._provider_combo.findData("uniface"))
    assert panel._ximu3_ignore_mag_button.isHidden()


def test_set_ximu3_ignore_magnetometer_does_not_emit_signal(qt_app):
    from gui.setup_gui import SetupPanel

    panel = SetupPanel()
    seen = []
    panel.ximu3_ignore_magnetometer_toggled.connect(seen.append)

    panel.set_ximu3_ignore_magnetometer(True)
    assert panel.ximu3_ignore_magnetometer_enabled is True
    assert seen == []


def test_mounting_dropdown_visibility_presets_and_signal(qt_app):
    from gui.setup_gui import SetupPanel

    panel = SetupPanel(show_airpods=True)
    combo = panel._ximu3_mounting_combo
    assert combo.isHidden()
    assert panel._ximu3_mounting_label.isHidden()
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "LHS-Horizontal", "LHS-Vertical", "RHS-Horizontal", "RHS-Vertical",
    ]
    panel._provider_combo.setCurrentIndex(panel._provider_combo.findData("ximu3"))
    assert not combo.isHidden()
    assert not panel._ximu3_mounting_label.isHidden()
    seen = []
    panel.ximu3_mounting_orientation_changed.connect(seen.append)
    combo.setCurrentIndex(3)
    assert seen == ["RHS-Vertical"]
    panel.set_ximu3_axes_configuration("+Y-Z-X", 2)
    assert combo.currentData() == "LHS-Vertical"
    assert seen == ["RHS-Vertical"]
    panel.set_ximu3_axes_configuration("+X+Y+Z", 0)
    assert combo.currentIndex() == -1
    assert seen == ["RHS-Vertical"]
    for provider in ("uniface", "airpods"):
        panel._provider_combo.setCurrentIndex(panel._provider_combo.findData(provider))
        assert combo.isHidden()
        assert panel._ximu3_mounting_label.isHidden()


@pytest.mark.parametrize("name,alignment,value", [
    ("LHS-Horizontal", "+X-Z+Y", 2),
    ("LHS-Vertical", "+Y-Z-X", 6),
    ("RHS-Horizontal", "+X+Z-Y", 1),
    ("RHS-Vertical", "-Y+Z-X", 17),
])
def test_app_mounting_applies_and_persists(qt_app, tmp_path, name, alignment, value):
    from types import SimpleNamespace
    from main import App, load_config, make_provider
    from providers.ximu3_provider import Ximu3Provider

    config = copy.deepcopy(load_config())
    config["providers"]["ximu3"].update(axes_alignment="+X+Y+Z", ahrs_axes_convention=0)
    config.setdefault("ui", {})["calibration_enabled"] = False
    config_path = tmp_path / "config.json"
    app = App(config, auto_start_listeners=False, mouse_controller=FakeMouseController(),
              config_path=config_path)
    commands = []

    class Connection:
        def send_command(self, command):
            message = json.loads(command)
            if list(message.values()) != [None] or "apply" in message or "initialise" in message:
                commands.append(message)
            return SimpleNamespace(error="", value="0")

    provider = Ximu3Provider()
    provider._connection = Connection()
    app._provider = provider
    try:
        combo = app.window.setup_panel._ximu3_mounting_combo
        assert combo.currentIndex() == -1
        combo.setCurrentIndex(combo.findData(name))
        assert commands == [
            {"axesAlignment": value}, {"ahrsAxesConvention": 2},
            {"ahrsIgnoreMagnetometer": False}, {"apply": None}, {"initialise": None},
        ]
        saved = load_config(config_path)
        assert saved["providers"]["ximu3"]["axes_alignment"] == alignment
        assert saved["providers"]["ximu3"]["ahrs_axes_convention"] == 2
        created = make_provider("ximu3", saved)
        assert (created.axes_alignment, created.ahrs_axes_convention) == (alignment, 2)
    finally:
        app.shutdown()


def test_app_mounting_failure_restores_gui_config_and_reports_error(qt_app, monkeypatch):
    from main import App, load_config
    from providers.ximu3_provider import Ximu3Provider
    from PySide6.QtWidgets import QMessageBox

    config = copy.deepcopy(load_config())
    config["providers"]["ximu3"].update(axes_alignment="+X-Z+Y", ahrs_axes_convention=2)
    previous = copy.deepcopy(config)
    app = App(config, auto_start_listeners=False, mouse_controller=FakeMouseController())
    app._provider = Ximu3Provider()
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    try:
        combo = app.window.setup_panel._ximu3_mounting_combo
        combo.setCurrentIndex(3)
        assert combo.currentData() == "LHS-Horizontal"
        assert config == previous
        assert len(errors) == 1
        assert "not connected" in errors[0]
    finally:
        app.shutdown()


def test_mounting_save_failure_restores_live_device(qt_app, monkeypatch):
    from types import SimpleNamespace
    from main import App, load_config
    from providers.ximu3_provider import Ximu3Provider
    from PySide6.QtWidgets import QMessageBox

    config = copy.deepcopy(load_config())
    config["providers"]["ximu3"].update(axes_alignment="+X-Z+Y", ahrs_axes_convention=2)
    previous = copy.deepcopy(config)
    app = App(config, auto_start_listeners=False, mouse_controller=FakeMouseController())
    provider = Ximu3Provider()
    commands = []

    class Connection:
        def send_command(self, command):
            message = json.loads(command)
            if list(message.values()) != [None] or "apply" in message or "initialise" in message:
                commands.append(message)
            return SimpleNamespace(error="", value="0")

    def fail_save():
        raise RuntimeError("Cannot save settings")

    provider._connection = Connection()
    app._provider = provider
    errors = []
    monkeypatch.setattr(app, "_persist_config", fail_save)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))
    try:
        combo = app.window.setup_panel._ximu3_mounting_combo
        combo.setCurrentIndex(3)
        assert config == previous
        assert combo.currentData() == "LHS-Horizontal"
        assert (provider.axes_alignment, provider.ahrs_axes_convention) == ("+X-Z+Y", 2)
        assert commands == [
            {"axesAlignment": 17}, {"ahrsAxesConvention": 2},
            {"ahrsIgnoreMagnetometer": False}, {"apply": None}, {"initialise": None},
            {"axesAlignment": 2}, {"ahrsAxesConvention": 2},
            {"ahrsIgnoreMagnetometer": False}, {"apply": None}, {"initialise": None},
        ]
        assert len(errors) == 1
        assert "Cannot save settings" in errors[0]
    finally:
        app.shutdown()


def test_telemetry_widget_updates_labels(qt_app):
    from gui.vis_gui import TelemetryWidget

    widget = TelemetryWidget()
    widget.update_angles(12.3, -4.5, 0.0)
    assert "12.3" in widget._yaw_label.text()
    assert "-4.5" in widget._pitch_label.text()


def test_headmouse_window_builds_and_reports_status(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0, hotkey="<esc>")
    assert window._kx_spin.value() == 8.0
    assert window._ky_spin.value() == -8.0

    window.set_status("active")
    assert window._status_label.text() == "ACTIVE"

    window.set_hotkey_status("Hotkey unavailable")
    assert window._hotkey_label.text() == "Hotkey unavailable"


def test_default_toggle_hotkey_is_escape(qt_app):
    from main import load_config
    from core.headmouse import HeadMouse, ScreenBounds

    assert load_config()["hotkeys"]["toggle_tracking"] == "<esc>"
    headmouse = HeadMouse(ScreenBounds(800, 600), mouse_controller=FakeMouseController())
    assert headmouse._hotkey_str == "<esc>"


def test_headmouse_window_exposes_dsp_mode_and_filter_controls(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0)
    seen = []
    window.dsp_config_changed.connect(seen.append)

    assert not window._absolute_options.isHidden()
    assert window._velocity_options.isHidden()
    assert not window._one_euro_check.isHidden()
    window._one_euro_check.setChecked(True)
    window._filter_preset_combo.setCurrentText("High filtering")
    window._mode_combo.setCurrentText("Velocity")
    assert window._absolute_options.isHidden()
    assert not window._velocity_options.isHidden()
    assert not window._filter_options.isHidden()
    window._gamma_spin.setValue(1.5)

    assert seen[-1]["mode"] == "velocity"
    assert seen[-1]["gamma"] == pytest.approx(1.5)
    assert seen[-1]["one_euro_enabled"] is True
    assert seen[-1]["one_euro_min_cutoff"] == pytest.approx(0.4)
    assert seen[-1]["one_euro_beta"] == pytest.approx(0.15)


def test_headmouse_window_preserves_configured_velocity_mode_when_filter_changes(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    config = {
        "mode": "velocity",
        "velocity": {"kx": 2.0, "ky": 3.0, "gamma": 1.4, "deadband_deg_s": 2.5},
        "one_euro_filter": {"enabled": False, "min_cutoff": 0.8, "beta": 0.2, "d_cutoff": 1.2},
        "auto_zero": {"enabled": True},
        "yaw_drift_correction": {"enabled": True},
    }
    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0, initial_dsp_config=config)
    seen = []
    window.dsp_config_changed.connect(seen.append)

    assert window._mode_combo.currentData() == "velocity"
    assert window._velocity_kx_spin.value() == pytest.approx(2.0)
    assert window._auto_zero_check.isChecked()
    window._one_euro_check.setChecked(True)

    assert seen[-1]["mode"] == "velocity"
    assert seen[-1]["one_euro_enabled"] is True


def test_dsp_numeric_formatting_steps_and_tooltips(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0)

    assert window._kx_spin.text() == "8"
    assert window._ky_spin.text() == "-8"
    assert window._velocity_kx_spin.text() == "1"
    assert window._velocity_ky_spin.text() == "1"
    assert window._gamma_spin.singleStep() == pytest.approx(0.1)
    assert window._deadband_spin.singleStep() == pytest.approx(0.1)

    controls = (
        window._mode_combo,
        window._kx_spin,
        window._ky_spin,
        window._velocity_kx_spin,
        window._velocity_ky_spin,
        window._gamma_spin,
        window._deadband_spin,
        window._one_euro_check,
        window._filter_preset_combo,
        window._auto_zero_check,
        window._drift_check,
    )
    assert all(control.toolTip() for control in controls)


def test_filter_presets_progress_from_responsive_to_steady(qt_app):
    from gui.headmouse_gui import HeadMouseWindow, ONE_EURO_PRESETS

    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0)
    assert [window._filter_preset_combo.itemText(i) for i in range(3)] == list(ONE_EURO_PRESETS)
    assert ONE_EURO_PRESETS["Low filtering"]["min_cutoff"] > ONE_EURO_PRESETS["Medium filtering"]["min_cutoff"]
    assert ONE_EURO_PRESETS["Medium filtering"]["min_cutoff"] > ONE_EURO_PRESETS["High filtering"]["min_cutoff"]


class FakeMouseController:
    """Avoids moving the real OS cursor during automated tests."""

    def __init__(self, start=(0.0, 0.0)):
        self._position = start

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value


def test_app_wires_mock_provider_through_to_headmouse(qt_app):
    from main import App, load_config

    config = copy.deepcopy(load_config())
    config.setdefault("ui", {})["calibration_enabled"] = False
    app = App(config, poll_hz=90.0, auto_start_listeners=False, mouse_controller=FakeMouseController())
    try:
        app._on_start_requested("mock")
        assert app.headmouse._mouse.position == (400.0, 400.0)
        time.sleep(0.3)  # let the MockProvider thread push a few samples

        app._poll()
        yaw_text = app.window.telemetry._yaw_label.text()
        assert "--" not in yaw_text  # telemetry was actually updated from a real sample

        assert app.headmouse.state.value == "active"
        first_position = tuple(app.headmouse._position)

        time.sleep(0.05)  # ensure a fresh sample is queued before the next poll
        app._poll()
        second_position = tuple(app.headmouse._position)
        # MockProvider is continuously oscillating, so the cursor should have
        # moved between successive polls once tracking is active.
        assert first_position != second_position
    finally:
        app._on_stop_requested()
        app.shutdown()


def test_app_recenter_zeroes_out_current_pose(qt_app):
    from main import App, load_config

    config = copy.deepcopy(load_config())
    config.setdefault("ui", {})["calibration_enabled"] = False
    app = App(config, poll_hz=90.0, auto_start_listeners=False, mouse_controller=FakeMouseController())
    try:
        app._on_start_requested("mock")
        time.sleep(0.3)

        app._on_recenter()
        latest = app._drain_latest()
        # after recentering to a just-seen pose, draining further won't
        # necessarily return None immediately since the provider keeps
        # producing; just confirm recenter didn't raise and reset state.
        assert app._last_target is None
    finally:
        app._on_stop_requested()
        app.shutdown()


def test_app_starts_and_stops_airpods_helper_with_provider(qt_app, monkeypatch, tmp_path):
    from main import App, load_config
    import main as main_module

    run_commands = []
    kill_calls = []
    helper_alive = True
    pid_path = tmp_path / "airpods-helper.pid"
    log_path = tmp_path / "airpods-helper.log"

    def fake_run(command, **kwargs):
        run_commands.append(command)
        if "set helperTab to do script" in command[2]:
            pid_path.write_text("12345")
            return main_module.subprocess.CompletedProcess(command, 0, stdout="67890\n")
        return main_module.subprocess.CompletedProcess(command, 0)

    def fake_kill(pid, sig):
        nonlocal helper_alive
        kill_calls.append((pid, sig))
        if not helper_alive:
            raise ProcessLookupError
        if sig != 0:
            helper_alive = False

    monkeypatch.setattr(main_module.sys, "platform", "darwin")
    monkeypatch.setattr(main_module.subprocess, "run", fake_run)
    monkeypatch.setattr(main_module.os, "kill", fake_kill)
    monkeypatch.setattr(main_module, "AIRPODS_HELPER_PID_PATH", pid_path)
    monkeypatch.setattr(main_module, "AIRPODS_HELPER_LOG_PATH", log_path)

    config = copy.deepcopy(load_config())
    config["providers"]["airpods"]["udp_port"] = 5997
    config.setdefault("ui", {})["calibration_enabled"] = False
    app = App(config, poll_hz=90.0, auto_start_listeners=False, mouse_controller=FakeMouseController())
    try:
        airpods_index = app.window.setup_panel._provider_combo.findData("airpods")
        assert airpods_index != -1

        app._on_start_requested("airpods")
        assert run_commands[0][:2] == ["/usr/bin/osascript", "-e"]
        assert "set helperTab to do script" in run_commands[0][2]
        assert "return helperWindowId" in run_commands[0][2]
        assert app._airpods_helper_pid == 12345
        assert app._airpods_terminal_window_id == 67890
        assert app._provider is not None

        app._on_stop_requested()
        assert (12345, main_module.signal.SIGTERM) in kill_calls
        assert app._airpods_helper_pid is None
        assert app._airpods_terminal_window_id is None
        assert "close (window id 67890)" in run_commands[1][2]
        assert app._provider is None
    finally:
        app.shutdown()


def test_main_gui_settings_persist_and_reload(qt_app, tmp_path):
    from main import App, load_config

    config_path = tmp_path / "config.json"
    config = copy.deepcopy(load_config())
    config_path.write_text(json.dumps(config))

    app = App(
        config,
        auto_start_listeners=False,
        mouse_controller=FakeMouseController(),
        config_path=config_path,
    )
    try:
        setup = app.window.setup_panel
        setup._provider_combo.setCurrentIndex(setup._provider_combo.findData("ximu3"))
        setup._ximu3_ignore_mag_button.setChecked(True)
        setup._ximu3_mounting_combo.setCurrentIndex(3)
        app.window.controls._record_check.setChecked(False)
        app.window.controls._mouse_check.setChecked(False)
        app.window.controls._calibration_check.setChecked(True)
        app.window._kx_spin.setValue(21)
        app.window._ky_spin.setValue(-17)
        app.window._mode_combo.setCurrentIndex(app.window._mode_combo.findData("velocity"))
        app.window._velocity_kx_spin.setValue(2)
        app.window._velocity_ky_spin.setValue(3)
        app.window._gamma_spin.setValue(1.8)
        app.window._deadband_spin.setValue(4.5)
        app.window._one_euro_check.setChecked(False)
        app.window._filter_preset_combo.setCurrentText("High filtering")
        app.window._auto_zero_check.setChecked(False)
        app.window._drift_check.setChecked(True)
    finally:
        app.shutdown()

    saved = json.loads(config_path.read_text())
    assert saved["active_provider"] == "ximu3"
    assert saved["ui"] == {
        "recording_enabled": False,
        "mouse_control_enabled": False,
        "calibration_enabled": True,
    }
    assert saved["providers"]["ximu3"]["ahrs_ignore_magnetometer"] is True
    assert saved["providers"]["ximu3"]["axes_alignment"] == "-Y+Z-X"
    assert saved["providers"]["ximu3"]["ahrs_axes_convention"] == 2
    assert saved["dsp"]["absolute"] == {"kx": 21.0, "ky": -17.0}
    assert saved["dsp"]["mode"] == "velocity"
    assert saved["dsp"]["velocity"] == {
        "kx": 2.0,
        "ky": 3.0,
        "gamma": 1.8,
        "deadband_deg_s": 4.5,
    }
    assert saved["dsp"]["one_euro_filter"] == {
        "enabled": False,
        "min_cutoff": 0.4,
        "beta": 0.15,
        "d_cutoff": 1.0,
    }
    assert saved["dsp"]["auto_zero"]["enabled"] is False
    assert saved["dsp"]["yaw_drift_correction"]["enabled"] is True

    reloaded_app = App(
        load_config(config_path),
        auto_start_listeners=False,
        mouse_controller=FakeMouseController(),
        config_path=config_path,
    )
    try:
        assert reloaded_app.window.setup_panel._provider_combo.currentData() == "ximu3"
        assert reloaded_app.window.setup_panel.ximu3_ignore_magnetometer_enabled is True
        assert reloaded_app.window.setup_panel._ximu3_mounting_combo.currentData() == "RHS-Vertical"
        assert reloaded_app.window.controls.recording_enabled is False
        assert reloaded_app.window.controls.mouse_control_enabled is False
        assert reloaded_app.window.controls.calibration_enabled is True
        assert reloaded_app.window._kx_spin.value() == 21
        assert reloaded_app.window._ky_spin.value() == -17
        assert reloaded_app.window._mode_combo.currentData() == "velocity"
        assert reloaded_app.window._velocity_kx_spin.value() == 2
        assert reloaded_app.window._velocity_ky_spin.value() == 3
        assert reloaded_app.window._gamma_spin.value() == pytest.approx(1.8)
        assert reloaded_app.window._deadband_spin.value() == pytest.approx(4.5)
        assert reloaded_app.window._one_euro_check.isChecked() is False
        assert reloaded_app.window._filter_preset_combo.currentText() == "High filtering"
        assert reloaded_app.window._auto_zero_check.isChecked() is False
        assert reloaded_app.window._drift_check.isChecked() is True
    finally:
        reloaded_app.shutdown()
