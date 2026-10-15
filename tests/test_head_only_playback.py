"""Head-only Fitts sessions are playable both alone and beside their head recording."""

import json

import numpy as np
import pytest

from core import quaternion_math as qm
from core.recorder import Recorder
from fitts_law.results import FittsResultsLogger
from session_viewer.fitts_playback import FittsPlaybackWindow
from session_viewer.playback_clock import check_sync
from session_viewer.pretest_stats import phase_description
from tests.test_fitts_playback import _fitts_trials, _plain_text


def _metadata():
    return {
        "task_selection": "HeadMovement only",
        "screen_width_px": 800, "screen_height_px": 600,
        "pretest_phases": [
            {
                "phase": "initial_rest.hold", "task": "initial_rest", "segment": "hold",
                "direction": None, "start_host_time": 1000.0, "end_host_time": 1001.0,
                "cursor_trajectory": [
                    {"host_time": 1000.1, "x_px": 400, "y_px": 300},
                    {"host_time": 1000.5, "x_px": 401, "y_px": 301},
                ],
            },
            {
                "phase": "range_of_movement.left.hold", "task": "range_of_movement",
                "segment": "hold", "direction": "left",
                "start_host_time": 1001.0, "end_host_time": 1002.0,
                "cursor_trajectory": [],
            },
        ],
    }


def _head_recorder():
    recorder = Recorder()
    for index in range(31):
        recorder.record_sample(
            index * 0.1, qm.from_euler_zyx(0.4 if index < 10 else 1.2, 0, 0),
            host_time=1000.0 + index * 0.1,
            metadata={"task_phase": "initial_rest.hold" if index < 10 else "range_of_movement.left.hold"},
        )
    return recorder


def test_head_only_file_loads_and_replays_phase_and_cursor(qt_app, tmp_path):
    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.metadata = _metadata()
    path = logger.save("head_only.json")
    loaded = FittsResultsLogger()
    loaded.load(path)
    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(loaded.trials, loaded.metadata, "head only")
        assert window.clock.t_min == 1000.0
        assert window.clock.t_max == 1002.0
        assert window.play_button.isEnabled()
        assert window.pretest_box.isVisibleTo(window)
        assert "Pointing measures do not apply" in _plain_text(window.stats_label)
        window.clock.seek(1000.6)
        assert window.canvas.cursor == (401, 301)
        assert window.canvas.phase_text == phase_description("initial_rest.hold")
        window.clock.seek(1001.5)
        assert window.canvas.phase_text == phase_description("range_of_movement.left.hold")
        assert window.canvas.cursor is None
        assert window.canvas.path.shape == (0, 2)
    finally:
        window.close()


def test_head_only_can_be_opened_from_session_viewer(qt_app, tmp_path, monkeypatch):
    from session_viewer.session_viewer import SessionVisualizerWindow

    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.metadata = _metadata()
    path = logger.save("head_only.json")
    viewer = SessionVisualizerWindow()
    monkeypatch.setattr(viewer, "_pick_fitts_session_file", lambda *args: path)
    try:
        viewer._on_open_fitts_session()
        assert len(viewer._fitts_windows) == 1
        assert viewer._fitts_windows[0].clock.t_max == 1002.0
    finally:
        for window in viewer._fitts_windows:
            window.close()
        viewer.close()


def test_head_only_synchronises_with_head_samples(qt_app):
    from session_viewer.session_viewer import SessionVisualizerWindow

    recorder = _head_recorder()
    logger = FittsResultsLogger()
    logger.metadata = _metadata()
    result = check_sync(recorder._samples, [], logger.metadata)
    assert result.ok
    assert result.fitts_range == (1000.0, 1002.0)
    viewer = SessionVisualizerWindow()
    window = None
    try:
        window = viewer.open_synchronised(recorder._samples, recorder, "head", logger, "head only")
        assert window.synced
        assert window.clock is viewer.clock
        np.testing.assert_allclose(viewer.neutral_q, qm.from_euler_zyx(0.4, 0, 0))
        assert viewer.frame_data[0]["yaw_rel_deg"] == pytest.approx(0.0)
        window.clock.seek(1001.5)
        assert viewer.current_frame_index == 15
        assert window.canvas.phase_text == phase_description("range_of_movement.left.hold")
    finally:
        if window is not None:
            window.close()
        viewer.close()


def test_head_only_to_mouse_to_head_only_clears_previous_state(qt_app):
    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session([], _metadata())
        window.clock.seek(1000.6)
        window.load_fitts_session(_fitts_trials(1000.0), {})
        assert window.canvas.phase_text == ""
        assert window._head_task_range is None
        window.load_fitts_session([], _metadata())
        data = window.speed_curve.getData()[0]
        assert data is None or len(data) == 0
        assert not window._trial_marker_items
        assert "No mouse trials" in _plain_text(window.stats_label)
    finally:
        window.close()


def test_empty_session_still_reports_useful_error(qt_app):
    window = FittsPlaybackWindow()
    try:
        with pytest.raises(ValueError, match="no trials or timed head-movement tasks"):
            window.load_fitts_session([], {})
    finally:
        window.close()


def test_head_only_results_offer_synchronised_playback(qt_app, tmp_path):
    from fitts_law.fitts_test import FittsTestWindow

    window = FittsTestWindow()
    try:
        window._logger.metadata = _metadata()
        window._logger.output_dir = tmp_path
        path = window._fitts_session_path()
        assert path.exists()
        assert json.loads(path.read_text())["trial_count"] == 0
    finally:
        window.close()


def test_phase_description_is_human_readable():
    assert "neutral position" in phase_description("initial_rest.move")
    assert "holding still" in phase_description("initial_rest.hold")
    assert "furthest LEFT" in phase_description("range_of_movement.left.hold")
    assert "as far UP" in phase_description("range_of_movement.up.move")
    assert "Fitts mouse task" in phase_description("fitts_test")
    assert phase_description(None) == "Not in a test task"


def test_combined_session_plays_exercise_then_trials(qt_app):
    trials = _fitts_trials(1005.0)
    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(trials, _metadata(), "combined")
        assert window.clock.t_min == 1000.0
        assert window.clock.t_max == pytest.approx(max(t.end_host_time for t in trials))
        window.clock.seek(1000.6)
        live = _plain_text(window.raw_data_widget)
        assert phase_description("initial_rest.hold") in live
        assert "Mouse task starts in" in live
        assert window.canvas.cursor == (401, 301)
        window.clock.seek(1003.0)
        assert "waiting for the mouse task" in _plain_text(window.raw_data_widget)
        window.clock.seek(1005.5)
        live = _plain_text(window.raw_data_widget)
        assert "Trial: 1 / 3" in live
        assert phase_description("fitts_test") in live
    finally:
        window.close()


def test_combined_session_sync_range_includes_exercise(qt_app):
    from session_viewer.session_viewer import SessionVisualizerWindow

    recorder = Recorder()
    for index in range(90):
        phase = "initial_rest.hold" if index < 20 else "fitts_test" if index >= 50 else None
        recorder.record_sample(
            index * 0.1, qm.from_euler_zyx(0.0, 0, 0), host_time=1000.0 + index * 0.1,
            metadata={"task_phase": phase} if phase else None,
        )
    logger = FittsResultsLogger()
    logger.metadata = _metadata()
    logger.trials = _fitts_trials(1005.0)
    result = check_sync(recorder._samples, logger.trials, logger.metadata)
    assert result.ok
    assert result.fitts_range[0] == 1000.0
    viewer = SessionVisualizerWindow()
    window = None
    try:
        window = viewer.open_synchronised(recorder._samples, recorder, "head", logger, "combined")
        assert viewer.clock.t_min == 1000.0
        window.clock.seek(1000.5)
        assert phase_description("initial_rest.hold") in viewer.raw_data_widget.text()
        assert phase_description("initial_rest.hold") in _plain_text(window.raw_data_widget)
        window.clock.seek(1006.0)
        assert phase_description("fitts_test") in viewer.raw_data_widget.text()
    finally:
        if window is not None:
            window.close()
        viewer.close()


def test_fitts_only_open_finds_head_recording_for_pose_stats(qt_app, tmp_path, monkeypatch):
    from session_viewer.head_session_lookup import find_head_session_for_fitts
    from session_viewer.session_viewer import SessionVisualizerWindow

    fitts_dir = tmp_path / "fitts_sessions"
    head_dir = tmp_path / "sessions"
    head_dir.mkdir()
    _head_recorder().save(head_dir / "session_1792065753.json")
    logger = FittsResultsLogger(output_dir=fitts_dir)
    logger.metadata = _metadata()
    path = logger.save("fitts_session_1792065754.json")

    match = find_head_session_for_fitts(path, logger.metadata, [])
    assert match is not None and match[0].name == "session_1792065753.json"

    viewer = SessionVisualizerWindow()
    monkeypatch.setattr(viewer, "_pick_fitts_session_file", lambda *args: path)
    try:
        viewer._on_open_fitts_session()
        window = viewer._fitts_windows[0]
        assert window.pretest_summary["head_initial_rest"] is not None
        assert window.pretest_summary["head_range_of_movement"] is not None
    finally:
        for window in viewer._fitts_windows:
            window.close()
        viewer.close()


def test_head_lookup_prefers_recorded_path_and_rejects_non_overlapping(tmp_path):
    from session_viewer.head_session_lookup import find_head_session_for_fitts

    recorded = tmp_path / "elsewhere" / "my_head.json"
    recorded.parent.mkdir()
    _head_recorder().save(recorded)
    fitts_path = tmp_path / "fitts_sessions" / "fitts_session_5.json"
    metadata = dict(_metadata(), head_session_file=str(recorded))
    assert find_head_session_for_fitts(fitts_path, metadata, [])[0] == recorded

    far = dict(_metadata())
    far["pretest_phases"] = [dict(r, start_host_time=5000.0, end_host_time=5001.0) for r in far["pretest_phases"]]
    far["head_session_file"] = str(recorded)
    assert find_head_session_for_fitts(fitts_path, far, []) is None
