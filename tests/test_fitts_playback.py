"""Tests for host-time stamping, MacKenzie path measures, sync checks and Fitts playback."""

import csv
import json

import numpy as np
import pytest

from core import quaternion_math as qm
from core.clock import host_time
from core.recorder import Recorder
from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult
from session_viewer.playback_clock import check_sync


def _trial(trajectory, start=(0.0, 0.0), target=(100.0, 0.0), click=(100.0, 0.0), width=20.0,
           start_time=10.0, mt=1.0, host_start=None, rep=0, index=0):
    return TrialResult(
        condition_label="A=100 W=20", block_type="circle", amplitude_px=100.0, width_px=width,
        start_pos=start, target_pos=target, click_pos=click,
        start_time=start_time, end_time=start_time + mt, movement_time_s=mt,
        hit=math_dist(click, target) <= width / 2, trial_index=index, repetition_index=rep,
        trajectory=trajectory,
        start_host_time=host_start, end_host_time=(host_start + mt) if host_start is not None else None,
    )


def math_dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


# -- shared clock / recorder -------------------------------------------------

def test_host_time_is_wall_clock_epoch():
    import time

    assert abs(host_time() - time.time()) < 0.1


def test_recorder_clear_starts_a_new_session(tmp_path):
    recorder = Recorder(output_dir=tmp_path)
    recorder.record_sample(1.0, qm.from_euler_zyx(0.1, 0.0, 0.0))
    recorder.record_event(1.0, "recenter", {"quaternion": [1.0, 0.0, 0.0, 0.0]})
    recorder.clear()
    assert not recorder.has_samples and not recorder.has_calibration
    recorder.record_sample(2.0, qm.from_euler_zyx(0.1, 0.0, 0.0))
    data = json.loads(recorder.save("s.json").read_text())
    assert data["sample_count"] == 1 and data["events"] == []


def test_recorder_json_round_trips_host_time(tmp_path):
    recorder = Recorder(output_dir=tmp_path)
    recorder.record_sample(1.0, qm.from_euler_zyx(0.1, 0.0, 0.0), host_time=1000.5)
    recorder.record_sample(1.1, qm.from_euler_zyx(0.2, 0.0, 0.0))
    path = recorder.save("s.json")
    data = json.loads(path.read_text())
    assert data["clock"] == {"host_time": "unix_epoch_s"}
    loaded = Recorder().load(path)
    assert loaded[0].host_time == 1000.5
    assert loaded[1].host_time is not None


def test_recorder_csv_round_trips_host_time(tmp_path):
    recorder = Recorder(output_dir=tmp_path, format="csv")
    recorder.record_sample(1.0, qm.from_euler_zyx(0.1, 0.0, 0.0), host_time=2000.25)
    path = recorder.save("s.csv")
    with open(path, newline="") as f:
        assert "host_time" in csv.DictReader(f).fieldnames
    assert Recorder().load(path)[0].host_time == 2000.25


def test_recorder_loads_legacy_files_without_host_time(tmp_path):
    legacy = {
        "format_version": "1.0", "sample_count": 1, "events": [],
        "samples": [{"timestamp": 1.0, "quaternion_w": 1.0, "quaternion_x": 0.0, "quaternion_y": 0.0,
                     "quaternion_z": 0.0, "yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}],
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy))
    assert Recorder().load(path)[0].host_time is None


# -- Fitts logger --------------------------------------------------------------

def test_fitts_logger_round_trips_host_times_and_screen(tmp_path):
    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.metadata["screen_width_px"] = 1920.0
    logger.metadata["screen_height_px"] = 1080.0
    logger.add_trial(_trial([[0.0, 0.0, 0.0]], host_start=5000.0))
    path = logger.save("f.json")
    assert json.loads(path.read_text())["format_version"] == "1.1"
    loaded = FittsResultsLogger()
    trials = loaded.load(path)
    assert trials[0].start_host_time == 5000.0 and trials[0].end_host_time == 5001.0
    assert loaded.metadata["screen_width_px"] == 1920.0


def test_fitts_logger_loads_legacy_and_ignores_unknown_keys(tmp_path):
    trial = {
        "condition_label": "c", "block_type": "circle", "amplitude_px": 1, "width_px": 1,
        "start_pos": [0, 0], "target_pos": [1, 0], "click_pos": [1, 0], "start_time": 0,
        "end_time": 1, "movement_time_s": 1, "hit": True, "future_field": 42,
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"format_version": "1.0", "trials": [trial]}))
    loaded = FittsResultsLogger().load(path)[0]
    assert loaded.start_host_time is None and loaded.trajectory == []


# -- MacKenzie et al. (2001) path measures ------------------------------------

def test_straight_path_has_no_deviation():
    trial = _trial([[t / 10, x, 0.0] for t, x in enumerate(range(0, 101, 10))])
    m = FittsStats.path_measures(trial)
    assert m == {"tre": 0, "tac": 0, "mdc": 0, "odc": 0, "mv_px": 0.0, "me_px": 0.0, "mo_px": 0.0}


def test_zigzag_path_counts_crossings_and_direction_changes():
    # y' = +5, -5, +5, -5 ... along the axis; the click lands back on the axis.
    ys = [5.0, -5.0, 5.0, -5.0]
    trial = _trial([[i * 0.1, 20.0 * (i + 1), y] for i, y in enumerate(ys)])
    m = FittsStats.path_measures(trial)
    assert m["tac"] == 3
    # dy' = -10, +10, -10, +5 -> 3 sign changes
    assert m["mdc"] == 3
    assert m["odc"] == 0
    points_y = np.array(ys + [0.0])
    assert m["me_px"] == pytest.approx(np.mean(np.abs(points_y)))
    assert m["mo_px"] == pytest.approx(np.mean(points_y))
    assert m["mv_px"] == pytest.approx(np.std(points_y, ddof=1))


def test_overshoot_counts_odc_and_target_reentry():
    # Enter target at x=95, overshoot to 120 (outside W/2=10), come back to 100.
    trial = _trial([[0.0, 0.0, 0.0], [0.1, 95.0, 0.0], [0.2, 120.0, 0.0], [0.3, 100.0, 0.0]])
    m = FittsStats.path_measures(trial)
    assert m["tre"] == 1
    assert m["odc"] == 1


def test_measures_use_task_axis_orientation():
    # Movement straight down, path offset to the +x side: y' is negative
    # (y' = -dx for an axis pointing along +y in screen coordinates).
    trial = _trial([[0.0, 0.0, 0.0], [0.1, 10.0, 50.0]], target=(0.0, 100.0), click=(0.0, 100.0))
    m = FittsStats.path_measures(trial)
    assert m["mo_px"] < 0


def test_accuracy_summary_skips_trials_without_paths():
    good = _trial([[0.0, 0.0, 0.0], [0.1, 50.0, 10.0]])
    empty = _trial([], click=(100.0, 0.0))
    summary = FittsStats.accuracy_summary([good, empty])
    assert summary["trial_count"] == 1
    assert FittsStats.accuracy_summary([empty])["tre"] is None


# -- sync check ----------------------------------------------------------------

def _head_samples(host_times):
    recorder = Recorder()
    for i, h in enumerate(host_times):
        recorder.record_sample(i * 0.1, qm.from_euler_zyx(0.01 * i, 0.0, 0.0), host_time=h)
    return recorder._samples


def test_check_sync_accepts_overlapping_recordings():
    result = check_sync(_head_samples([100.0, 101.0, 102.0, 103.0]), [_trial([], host_start=101.5)])
    assert result.ok
    assert (result.t_min, result.t_max) == (100.0, 103.0)


def test_check_sync_refuses_legacy_files():
    head = _head_samples([100.0, 101.0])
    head[0].host_time = None
    assert "head-movement session has no shared host" in check_sync(head, [_trial([], host_start=100.0)]).reason
    assert "Fitts session has no shared host" in check_sync(_head_samples([100.0, 101.0]), [_trial([])]).reason


def test_check_sync_refuses_non_overlapping_recordings():
    result = check_sync(_head_samples([100.0, 101.0]), [_trial([], host_start=200.0)])
    assert not result.ok
    assert "do not overlap" in result.reason


# -- windows -------------------------------------------------------------------

def _fitts_trials(host_start):
    trials = []
    t0 = host_start
    for i in range(3):
        traj = [[k * 0.1, 10.0 * k, 2.0 * (-1) ** k] for k in range(11)]
        trials.append(_trial(traj, start_time=50.0 + i, host_start=t0 + i, index=i))
    return trials


def test_fitts_playback_window_standalone(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session(_fitts_trials(1000.0), {"screen_width_px": 800, "screen_height_px": 600}, "t")
        assert "Throughput:" in window.stats_label.text()
        assert "TAC:" in window.stats_label.text()
        window.clock.seek(1001.55)
        live = window.raw_data_widget.text()
        assert "Trial:</b> 2 / 3" in live
        assert "in progress" in live
        window.clock.seek(window.clock.t_max)
        assert "HIT" in window.raw_data_widget.text()
    finally:
        window.close()


def test_synchronised_playback_shares_one_clock(qt_app):
    from session_viewer.session_viewer import SessionVisualizerWindow

    head = _head_samples([999.0 + 0.1 * i for i in range(40)])
    logger = FittsResultsLogger()
    logger.trials = _fitts_trials(1000.0)
    viewer = SessionVisualizerWindow()
    try:
        fitts_window = viewer.open_synchronised(head, None, "head", logger, "fitts")
        assert fitts_window is not None and fitts_window.clock is viewer.clock
        # Only the Fitts test period (1000-1003) is played back; head data
        # outside it (999-1000) is dropped and times are relative to the test start.
        assert (viewer.clock.t_min, viewer.clock.t_max) == pytest.approx((1000.0, 1003.0))
        assert viewer.timestamps[0] == pytest.approx(0.0)
        assert all(0.0 <= t <= 3.0 for t in viewer.timestamps)

        # Seeking from the Fitts window moves the head viewer too.
        fitts_window.transport._on_slider_moved(550)  # 0.55 s after t_min
        assert viewer.frame_data[viewer.current_frame_index]['time_rel'] == pytest.approx(0.5)
        assert "Trial:</b> 1 / 3" in fitts_window.raw_data_widget.text()

        viewer.clock.seek(1002.95)
        assert "Trial:</b> 3 / 3" in fitts_window.raw_data_widget.text()
        assert "Head recording has ended" in viewer.raw_data_widget.text()

        # Opening a new head session on its own unlinks it.
        viewer.load_session(head, "again")
        assert viewer.clock is not fitts_window.clock
        fitts_window.close()
    finally:
        viewer.close()


def test_refused_sync_can_open_fitts_on_its_own(qt_app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from session_viewer.session_viewer import SessionVisualizerWindow

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    head = _head_samples([10.0 + 0.1 * i for i in range(5)])
    logger = FittsResultsLogger()
    logger.trials = _fitts_trials(1000.0)
    viewer = SessionVisualizerWindow()
    try:
        fitts_window = viewer.open_synchronised(head, None, "head", logger, "fitts")
        assert "do not overlap" in asked[0]
        assert fitts_window is not None and not fitts_window.synced
        assert fitts_window.clock is not viewer.clock
        fitts_window.close()
    finally:
        viewer.close()


def test_viewer_uses_host_time_when_provider_clocks_are_mixed(qt_app):
    from session_viewer.session_viewer import SessionVisualizerWindow

    recorder = Recorder()
    for i in range(5):  # monotonic-clock provider
        recorder.record_sample(24000.0 + 0.1 * i, qm.from_euler_zyx(0.01 * i, 0.0, 0.0), host_time=500.0 + 0.1 * i)
    for i in range(5):  # epoch-clock provider later in the same file
        recorder.record_sample(1.79e9 + 0.1 * i, qm.from_euler_zyx(0.01 * i, 0.0, 0.0), host_time=510.0 + 0.1 * i)
    viewer = SessionVisualizerWindow()
    try:
        viewer.load_session(recorder._samples, "mixed")
        assert viewer.timestamps[-1] == pytest.approx(10.4)
    finally:
        viewer.close()


def test_graphs_mark_each_trial_start_and_end(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        trials = _fitts_trials(1000.0)
        window.load_fitts_session(trials, {}, "t")
        # One start line and one end line per trial, on both graphs.
        assert len(window._trial_marker_items) == 2 * 2 * len(trials)
        # Lines sit at each trial's start and click, relative to the session start.
        xs = sorted({round(item.value(), 3) for _, item in window._trial_marker_items})
        expected = sorted({round(t.start_host_time - 1000.0, 3) for t in trials}
                          | {round(t.end_host_time - 1000.0, 3) for t in trials})
        assert xs == expected
    finally:
        window.close()


def test_results_page_offers_synchronised_playback(qt_app, tmp_path, monkeypatch):
    from fitts_law.fitts_test import FittsTestWindow

    recorder = Recorder(output_dir=tmp_path)
    for i in range(40):
        recorder.record_sample(i * 0.1, qm.from_euler_zyx(0.01 * i, 0.0, 0.0), host_time=1000.0 + 0.1 * i)
    head_path = recorder.save("head.json")

    logger = FittsResultsLogger(output_dir=tmp_path)
    for trial in _fitts_trials(1000.5):
        logger.add_trial(trial)
    fitts_path = logger.save("fitts.json")

    window = FittsTestWindow()
    try:
        # Save/Open live in the File menu, and the bar chart is gone.
        menu_items = [a.text() for a in window.menuBar().actions()[0].menu().actions()]
        assert "Save Results As..." in menu_items and "Open Results..." in menu_items
        assert "Open Synchronised Playback..." in menu_items
        assert not hasattr(window, "_throughput_plot")

        window._logger = logger
        window._saved_path = fitts_path
        stopped = []
        window.set_head_session_provider(lambda: stopped.append(True) or head_path)
        window._on_open_synchronised()

        assert stopped, "the running head recording should be stopped and saved first"
        viewer, fitts_window = window._playback_windows
        assert fitts_window.synced
        # The two windows are tiled over the halves of the available screen area
        # (approximately: a window manager may adjust for frames or minimum sizes).
        area = viewer.screen().availableGeometry()
        assert viewer.geometry().x() == pytest.approx(area.left(), abs=4)
        assert fitts_window.geometry().x() == pytest.approx(area.left() + area.width() // 2, abs=4)
        for w in window._playback_windows:
            w.close()
    finally:
        window.close()
