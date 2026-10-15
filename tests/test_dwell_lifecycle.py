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


def test_fitts_dwell_works_without_headmouse_and_restores_disabled_setting(qt_app):
    app = make_app(qt_app, dwell_enabled=False)
    try:
        app.set_fitts_dwell_active(True)
        assert app._dwell_click.is_running is True
        assert app._dwell_armed is False
        assert app.window.controls.dwell_click_enabled is False
        app.set_fitts_dwell_active(False)
        assert app._dwell_click.is_running is False
    finally:
        app.shutdown()


def test_fitts_dwell_restores_running_headmouse_dwell(qt_app):
    app = make_app(qt_app, dwell_enabled=True)
    try:
        app.headmouse.set_tracking_state(True)
        app.set_fitts_dwell_active(True)
        app.set_fitts_dwell_active(False)
        assert app._dwell_click.is_running is True
        assert app._dwell_armed is True
    finally:
        app.shutdown()


def test_fitts_head_exercises_suppress_both_dwell_sources(qt_app):
    app = make_app(qt_app, dwell_enabled=True)
    try:
        app.headmouse.set_tracking_state(True)
        assert app._dwell_click.is_running
        app.set_fitts_mouse_task_active(False)
        assert not app._dwell_click.is_running
        app.set_fitts_dwell_active(True)
        assert not app._dwell_click.is_running
        app.headmouse.set_tracking_state(False)
        app.headmouse.set_tracking_state(True)
        assert not app._dwell_click.is_running
        app.set_fitts_mouse_task_active(True)
        assert app._dwell_click.is_running
        app.set_fitts_mouse_task_active(False)
        app.set_fitts_dwell_active(False)
        app.headmouse.set_tracking_state(False)
        app.set_fitts_mouse_task_active(None)
        assert not app._dwell_click.is_running
        assert app._dwell_armed
    finally:
        app.shutdown()


def test_hosted_headmouse_run_stops_dwell_and_tracking_before_saving_results(
    qt_app, tmp_path, monkeypatch,
):
    from fitts_law.fitts_test import FittsTestWindow, START_HEADMOUSE, TASK_COMBINED

    app = make_app(qt_app, dwell_enabled=True)
    window = FittsTestWindow()
    head_path = tmp_path / "head.json"
    stopped = []

    def start(ready, mouse_control):
        app.headmouse.set_tracking_state(True)
        assert not app._dwell_click.is_running
        ready(True)
        return True

    def stop():
        assert not window.isVisible()
        assert not app._dwell_click.is_running
        app.headmouse.set_tracking_state(False)
        stopped.append(True)
        return head_path

    monkeypatch.setattr(app, "_on_stop_requested", stop)
    window.set_head_start_provider(start)
    window.set_head_session_provider(app.stop_tracking_and_save)
    window.set_dwell_provider(app.set_fitts_dwell_active)
    window.set_mouse_task_provider(app.set_fitts_mouse_task_active)
    window.set_recordings_dir_provider(lambda prompt: tmp_path)

    try:
        window._task_combo.setCurrentText(TASK_COMBINED)
        window._start_mode_combo.setCurrentText(START_HEADMOUSE)
        window._on_start_clicked()
        assert not app._dwell_click.is_running
        overlay = window._pretest_overlay
        overlay.stop()
        while window._pretest_overlay is not None:
            assert not app._dwell_click.is_running
            overlay._phase_start -= overlay.current_stage["duration_s"]
            overlay._tick()
        assert app._dwell_click.is_running
        original_save = window._logger.save

        def save(path=None):
            assert stopped == [True]
            assert not app._dwell_click.is_running
            return original_save(path)

        monkeypatch.setattr(window._logger, "save", save)
        window._on_all_trials_completed()
        assert stopped == [True]
        assert not app._dwell_click.is_running
        assert app._dwell_armed
        assert window._head_session_path() == head_path
    finally:
        window.close()
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
