"""Pre-test (initial rest / range of movement) summary stats and viewer wiring."""

import numpy as np
import pytest

from core import quaternion_math as qm
from core.recorder import Recorder
from session_viewer.pretest_stats import (
    format_pretest_summary_html,
    summarise_pretest,
)
from tests.test_fitts_playback import _fitts_trials, _plain_text


def _rest_records(move_t=100.0):
    return [
        {
            "phase": "initial_rest.move", "task": "initial_rest", "segment": "move",
            "direction": None, "duration_s": 3.0,
            "start_host_time": move_t, "end_host_time": move_t + 3.0,
            "cursor_trajectory": [],
        },
        {
            "phase": "initial_rest.hold", "task": "initial_rest", "segment": "hold",
            "direction": None, "duration_s": 2.0,
            "start_host_time": move_t + 3.0, "end_host_time": move_t + 5.0,
            "cursor_trajectory": [
                {"host_time": move_t + 3.0, "x_px": 950.0, "y_px": 530.0},
                {"host_time": move_t + 4.0, "x_px": 970.0, "y_px": 550.0},
            ],
        },
    ]


def _rom_records(start=110.0):
    extremes = {"left": (100.0, 540.0), "right": (1800.0, 540.0),
                "up": (960.0, 60.0), "down": (960.0, 1000.0)}
    records = []
    t = start
    for direction, (x, y) in extremes.items():
        records.append({
            "phase": f"range_of_movement.{direction}.move", "task": "range_of_movement",
            "segment": "move", "direction": direction, "duration_s": 3.0,
            "start_host_time": t, "end_host_time": t + 3.0, "cursor_trajectory": [],
        })
        records.append({
            "phase": f"range_of_movement.{direction}.hold", "task": "range_of_movement",
            "segment": "hold", "direction": direction, "duration_s": 2.0,
            "start_host_time": t + 3.0, "end_host_time": t + 5.0,
            "cursor_trajectory": [
                {"host_time": t + 3.0, "x_px": x, "y_px": y},
                {"host_time": t + 4.0, "x_px": 960.0, "y_px": 540.0},
            ],
        })
        t += 5.0
    return records


def _metadata():
    return {
        "pretest_configuration": {
            "initial_rest_enabled": True, "range_of_movement_enabled": True,
            "move_duration_s": 3.0, "hold_duration_s": 2.0,
        },
        "pretest_phases": _rest_records() + _rom_records(),
    }


def _head_samples(tmp_path):
    """A head session whose samples carry the matching ``task_phase`` labels."""
    recorder = Recorder(output_dir=tmp_path)
    t = 0.0
    for _ in range(5):
        recorder.record_sample(t, qm.from_euler_zyx(np.deg2rad(1.0), 0.0, 0.0),
                               raw_metadata={"task_phase": "initial_rest.hold"})
        t += 0.1
    angles = {"left": (-20.0, 0.0), "right": (20.0, 0.0),
              "up": (0.0, 15.0), "down": (0.0, -15.0)}
    for direction, (yaw, pitch) in angles.items():
        for _ in range(5):
            recorder.record_sample(
                t, qm.from_euler_zyx(np.deg2rad(yaw), np.deg2rad(pitch), 0.0),
                raw_metadata={"task_phase": f"range_of_movement.{direction}.hold"},
            )
            t += 0.1
    return Recorder().load(recorder.save("head.json"))


# -- statistics --------------------------------------------------------------

def test_summary_is_unavailable_without_pretest_data():
    summary = summarise_pretest({"screen_width_px": 1920})
    assert summary["available"] is False
    assert "No initial rest position" in format_pretest_summary_html(summary)


def test_cursor_stats_come_from_the_fitts_file_alone():
    summary = summarise_pretest(_metadata())
    assert summary["available"] is True

    rest = summary["cursor_initial_rest"]
    assert rest["sample_count"] == 2
    assert rest["mean_x_px"] == pytest.approx(960.0)
    assert rest["mean_y_px"] == pytest.approx(540.0)
    assert rest["std_x_px"] == pytest.approx(10.0)
    assert rest["max_deviation_px"] == pytest.approx(np.hypot(10.0, 10.0))
    assert rest["duration_s"] == pytest.approx(2.0)
    assert rest["cancelled"] is False

    rom = summary["cursor_range_of_movement"]
    assert rom["directions"]["left"]["extreme_px"] == pytest.approx(100.0)
    assert rom["directions"]["right"]["extreme_px"] == pytest.approx(1800.0)
    assert rom["directions"]["up"]["extreme_px"] == pytest.approx(60.0)
    assert rom["directions"]["down"]["extreme_px"] == pytest.approx(1000.0)
    assert rom["horizontal_range_px"] == pytest.approx(1700.0)
    assert rom["vertical_range_px"] == pytest.approx(940.0)

    # No head session: pose statistics are absent but cursor ones still render.
    assert summary["head_initial_rest"] is None
    html = format_pretest_summary_html(summary)
    assert "Initial head pose" in html and "Range of movement" in html
    assert "Cursor H range: 1700 px" in html


def test_head_pose_stats_use_task_phase_labels(tmp_path):
    summary = summarise_pretest(_metadata(), _head_samples(tmp_path))

    rest = summary["head_initial_rest"]
    assert rest["sample_count"] == 5
    assert rest["yaw_mean_deg"] == pytest.approx(1.0, abs=1e-6)
    assert rest["yaw_std_deg"] == pytest.approx(0.0, abs=1e-6)

    rom = summary["head_range_of_movement"]
    assert rom["directions"]["left"]["angle"] == "yaw"
    assert rom["directions"]["left"]["angle_mean_deg"] == pytest.approx(-20.0, abs=1e-6)
    assert rom["directions"]["left"]["offset_from_rest_deg"] == pytest.approx(-21.0, abs=1e-6)
    assert rom["directions"]["up"]["angle"] == "pitch"
    assert rom["yaw_range_deg"] == pytest.approx(40.0, abs=1e-6)
    assert rom["pitch_range_deg"] == pytest.approx(30.0, abs=1e-6)

    html = format_pretest_summary_html(summary)
    assert "Yaw range: 40.00°" in html


def test_partial_pretest_data_is_tolerated():
    summary = summarise_pretest({"pretest_phases": _rest_records()})
    assert summary["available"] is True
    assert summary["cursor_range_of_movement"] is None
    assert "Range of movement" not in format_pretest_summary_html(summary)


# -- viewer wiring -----------------------------------------------------------

def test_fitts_playback_shows_pretest_summary(qt_app, tmp_path):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(_fitts_trials(1000.0), _metadata(), "t")
        assert window.pretest_box.isVisibleTo(window)
        assert "Cursor H range" in window.pretest_label.text()
        assert "Yaw range" not in window.pretest_label.text()

        window.set_head_session_samples(_head_samples(tmp_path))
        assert "Yaw range: 40.00°" in _plain_text(window.pretest_label)
    finally:
        window.close()


def test_fitts_playback_hides_pretest_summary_when_absent(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(_fitts_trials(1000.0), {}, "t")
        assert not window.pretest_box.isVisibleTo(window)
    finally:
        window.close()


def test_session_viewer_offers_three_open_options(qt_app):
    from PySide6.QtWidgets import QToolBar

    from session_viewer.session_viewer import SessionVisualizerWindow

    window = SessionVisualizerWindow()
    try:
        items = [a.text() for a in window.menuBar().actions()[0].menu().actions() if not a.isSeparator()]
        assert items == [
            "Open OpenHeadMouse Session...",
            "Open Synchronised Playback...",
            "Open Fitts Test Only...",
            "Tile Windows Side by Side",
            "Close",
        ]
        toolbar = window.findChild(QToolBar)
        assert "Open Fitts Test Only..." in [a.text() for a in toolbar.actions()]
    finally:
        window.close()


def test_fitts_only_open_action_loads_a_session(qt_app, tmp_path, monkeypatch):
    from fitts_law.results import FittsResultsLogger
    from session_viewer import session_viewer as sv

    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.metadata.update(_metadata())
    for trial in _fitts_trials(1000.0):
        logger.add_trial(trial)
    path = logger.save("fitts.json")

    window = sv.SessionVisualizerWindow()
    try:
        monkeypatch.setattr(window, "_pick_fitts_session_file", lambda *a, **k: path)
        window._on_open_fitts_session()
        assert len(window._fitts_windows) == 1
        fitts_window = window._fitts_windows[0]
        # Opened on its own: no head session, so only cursor statistics.
        assert fitts_window.pretest_summary["head_initial_rest"] is None
        assert "Cursor H range" in fitts_window.pretest_label.text()
        fitts_window.close()
    finally:
        window.close()


def test_synchronised_playback_passes_head_samples_to_fitts_window(qt_app, tmp_path):
    from fitts_law.results import FittsResultsLogger
    from session_viewer import session_viewer as sv

    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.metadata.update(_metadata())
    trials = _fitts_trials(1000.0)
    for trial in trials:
        logger.add_trial(trial)

    recorder = Recorder(output_dir=tmp_path)
    for i in range(60):
        recorder.record_sample(i * 0.1, qm.from_euler_zyx(0.01 * i, 0.0, 0.0),
                               host_time=999.0 + 0.1 * i)
    samples = Recorder().load(recorder.save("head.json"))
    samples.extend(_head_samples(tmp_path))

    window = sv.SessionVisualizerWindow()
    try:
        fitts_window = window.open_synchronised(samples, recorder, "head", logger, "fitts")
        assert fitts_window is not None
        assert fitts_window.pretest_summary["head_initial_rest"]["sample_count"] == 5
        assert "Yaw range" in fitts_window.pretest_label.text()
        fitts_window.close()
    finally:
        window.close()
