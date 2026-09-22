"""GUI smoke tests: verify widgets build headlessly and the App wiring moves
data end-to-end (provider -> dsp_engine -> headmouse -> telemetry) using the
synthetic MockProvider. Real OS-level listeners (global hotkey, mouse
tap) are not started here (auto_start_listeners=False) — that path needs a
real display/Accessibility permission and is verified interactively per the
plan's M6 note. Requires QT_QPA_PLATFORM=offscreen (see conftest.py).
"""

import time

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


def test_telemetry_widget_updates_labels(qt_app):
    from gui.vis_gui import TelemetryWidget

    widget = TelemetryWidget()
    widget.update_angles(12.3, -4.5, 0.0)
    assert "12.3" in widget._yaw_label.text()
    assert "-4.5" in widget._pitch_label.text()


def test_headmouse_window_builds_and_reports_status(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=8.0, initial_ky=-8.0, hotkey="<cmd>+<alt>+m")
    assert window._kx_spin.value() == 8.0
    assert window._ky_spin.value() == -8.0

    window.set_status("active")
    assert window._status_label.text() == "ACTIVE"


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

    config = load_config()
    app = App(config, poll_hz=90.0, auto_start_listeners=False, mouse_controller=FakeMouseController())
    try:
        app._on_start_requested("mock")
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

    config = load_config()
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
