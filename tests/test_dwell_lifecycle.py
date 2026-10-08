"""Dwell clicking follows the mouse-control lifecycle in the main GUI."""

import copy
import threading


class FakeMouseController:
    def __init__(self):
        self._position = (0.0, 0.0)
        self.clicks = []

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value

    def click(self, button):
        self.clicks.append(button)


def make_app(qt_app, dwell_enabled: bool):
    from main import App, load_config

    config = copy.deepcopy(load_config())
    config.setdefault("ui", {})["mouse_control_enabled"] = True
    config["ui"]["calibration_enabled"] = False
    config["ui"].setdefault("dwell_click", {})["enabled"] = dwell_enabled
    return App(config, auto_start_listeners=False, mouse_controller=FakeMouseController())


def test_ticking_dwell_box_does_not_start_dwell_clicking(qt_app):
    app = make_app(qt_app, dwell_enabled=False)
    try:
        assert app._dwell_click.is_running is False
        app.window.controls._dwell_check.setChecked(True)
        assert app._dwell_click.is_running is False
    finally:
        app.shutdown()


def test_dwell_starts_with_mouse_control_and_stops_when_paused(qt_app):
    app = make_app(qt_app, dwell_enabled=True)
    try:
        assert app._dwell_click.is_running is False

        app.headmouse.set_tracking_state(True)
        assert app._dwell_click.is_running is True

        app.headmouse.set_tracking_state(False)
        assert app._dwell_click.is_running is False
    finally:
        app.shutdown()


def test_unticking_mouse_control_stops_dwell_clicking(qt_app):
    app = make_app(qt_app, dwell_enabled=True)
    try:
        app.headmouse.set_tracking_state(True)
        assert app._dwell_click.is_running is True

        app.window.controls._mouse_check.setChecked(False)
        assert app._dwell_click.is_running is False

        app.window.controls._mouse_check.setChecked(True)
        assert app._dwell_click.is_running is True
    finally:
        app.shutdown()


def test_dwell_does_not_run_while_mouse_control_is_disabled(qt_app):
    app = make_app(qt_app, dwell_enabled=False)
    try:
        app.window.controls._mouse_check.setChecked(False)
        app.window.controls._dwell_check.setChecked(True)
        app.headmouse.set_tracking_state(True)
        assert app._dwell_click.is_running is False
    finally:
        app.shutdown()


def test_escape_pause_and_resume_from_listener_thread_restarts_dwell(qt_app):
    app = make_app(qt_app, dwell_enabled=True)
    try:
        app.headmouse.set_tracking_state(True)
        assert app._dwell_click.is_running is True

        worker = threading.Thread(
            target=lambda: (
                app.headmouse.set_tracking_state(False),
                app.headmouse.set_tracking_state(True),
            )
        )
        worker.start()
        worker.join()

        qt_app.processEvents()
        assert app._dwell_click.is_running is True
    finally:
        app.shutdown()
