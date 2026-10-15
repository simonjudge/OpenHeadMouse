"""Visible close controls and per-measure hover explanations."""

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolBar, QToolTip

from session_viewer.fitts_playback import FittsPlaybackWindow
from session_viewer.measure_help import MEASURE_HELP, MeasureLabel
from tests.test_fitts_playback import _fitts_trials


def test_fitts_measures_have_summary_and_live_hover_links(qt_app):
    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(_fitts_trials(1000.0))
        summary = window.stats_label.text()
        live = window.raw_data_widget.text()
        for metric in ("Ae", "We", "IDe", "Throughput", "TRE", "TAC", "MDC", "ODC", "MV", "ME", "MO"):
            assert f'href="{metric}"' in summary
            assert len(MEASURE_HELP[metric]) > 40
        for metric in ("A", "W", "Elapsed MT", "TRE", "TAC", "MDC", "ODC", "MV", "ME", "MO"):
            assert f'href="{metric}"' in live
    finally:
        window.close()


def test_hovering_metric_shows_its_specific_explanation(qt_app):
    label = MeasureLabel("<b>Ae:</b> 100 px")
    label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    label.resize(300, 50)
    label.show()
    try:
        qt_app.processEvents()
        QTest.mouseMove(label, QPoint(200, 30))
        QTest.mouseMove(label, QPoint(8, 8))
        qt_app.processEvents()
        assert QToolTip.text() == MEASURE_HELP["Ae"]
        label._show_help("Mean%20MT")
        assert QToolTip.text() == MEASURE_HELP["Mean MT"]
    finally:
        QToolTip.hideText()
        label.close()


@pytest.mark.parametrize("kind", ["fitts", "head", "setup", "test"])
def test_close_action_is_available_and_closes_window(qt_app, kind):
    if kind == "fitts":
        window = FittsPlaybackWindow()
        window.load_fitts_session(_fitts_trials(1000.0))
        window.clock.play()
    elif kind == "head":
        from session_viewer.session_viewer import SessionVisualizerWindow

        window = SessionVisualizerWindow()
    elif kind == "test":
        from fitts_law.fitts_test import FittsTestWindow

        window = FittsTestWindow()
    else:
        from gui.headmouse_gui import HeadMouseWindow

        window = HeadMouseWindow(initial_kx=1, initial_ky=1)
    window.show()
    try:
        assert window._close_action.text() == "Close"
        assert window._close_action.shortcut().toString()
        file_menu = window.menuBar().actions()[0].menu()
        assert window._close_action in file_menu.actions()
        assert all(
            window._close_action not in toolbar.actions()
            for toolbar in window.findChildren(QToolBar)
        )
        assert not any(toolbar.windowTitle() == "Window" for toolbar in window.findChildren(QToolBar))
        window._close_action.trigger()
        assert not window.isVisible()
        if hasattr(window, "clock"):
            assert not window.clock.is_playing
    finally:
        window.close()


def test_setup_close_button_closes_its_window(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=1, initial_ky=1)
    window.show()
    window.setup_panel._close_button.click()
    assert not window.isVisible()


def test_setup_close_stops_input_and_saves_active_recording(qt_app, tmp_path):
    from core import quaternion_math as qm
    from tests.test_dwell_lifecycle import drive_cursor, make_app

    app = make_app(qt_app, dwell_enabled=True)
    app._recorder.output_dir = tmp_path
    app._recorder.record_sample(0.0, qm.from_euler_zyx(0.2, 0, 0))
    app.headmouse.set_tracking_state(True)
    drive_cursor(app)
    app.window.show()
    try:
        assert app._dwell_click.is_running
        app.window.setup_panel._close_button.click()
        assert not app.window.isVisible()
        assert not app._dwell_click.is_running
        assert not app._timer.isActive()
        assert not app._recorder.has_samples
        assert len(list(tmp_path.glob("*.json"))) == 1
    finally:
        app.shutdown()


def test_setup_close_can_be_cancelled_when_cleanup_fails(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=1, initial_ky=1)
    window.set_close_handler(lambda: False)
    window.show()
    try:
        window._close_action.trigger()
        assert window.isVisible()
    finally:
        window.set_close_handler(None)
        window.close()


@pytest.mark.parametrize("head_only", [False, True])
def test_closing_active_fitts_test_saves_partial_data(qt_app, tmp_path, head_only):
    from fitts_law.fitts_test import FittsTestWindow, TASK_HEAD_ONLY
    from tests.test_fitts_gui_smoke import _click_at

    window = FittsTestWindow()
    stopped = []
    window.set_recordings_dir_provider(lambda prompt: tmp_path)
    window.set_head_session_provider(lambda: stopped.append(True))
    if head_only:
        window._task_combo.setCurrentText(TASK_HEAD_ONLY)
    window._on_start_clicked()
    if not head_only:
        _click_at(window._canvas, *window._canvas._specs[0]["target_pos"])
    window._close_action.trigger()
    assert not window.isVisible()
    assert stopped == [True]
    data = json.loads(window._saved_path.read_text())
    if head_only:
        phase = data["metadata"]["pretest_phases"][-1]
        assert phase["cancelled"]
        assert phase["end_host_time"] is not None
    else:
        assert data["trial_count"] == 1
        assert data["metadata"]["fitts_task"]["completed"] is False
    window.close()
    assert stopped == [True]


def test_calibration_close_button_cancels_once(qt_app):
    from gui.calibration_overlay import CalibrationOverlay

    overlay = CalibrationOverlay()
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start()
    overlay._close_button.click()
    assert cancelled == [True]
    assert not overlay._timer.isActive()
    assert not overlay.isVisible()
    overlay.close()
    assert cancelled == [True]
