from dataclasses import replace

import numpy as np
import pytest

from colab_session_viewer.data import Session, participant_rows, participant_tables, summary_csv, table_sources
from core import quaternion_math as qm
from core.recorder import RecordedSample
from fitts_law.clutch import boundary_counts, clutch_counts, clutch_summary
from fitts_law.results import TrialResult


META = {"access_method": "HeadMouse", "screen_width_px": 800, "screen_height_px": 600}


def samples(yaw=(0, 0, 3, 1, 0), pitch=(0, 0, 0, 0, 0)):
    anchors = [0, .2, .4, .55, 2]
    result = []
    for t in np.linspace(0, 2, 41):
        y, p = np.interp(t, anchors, yaw), np.interp(t, anchors, pitch)
        q = qm.from_euler_zyx(np.radians(y), np.radians(p), 0)
        result.append(RecordedSample(t, *q, y, p, 0, host_time=1000 + t))
    return result


def trial():
    return TrialResult(
        condition_label="test", block_type="circle", amplitude_px=100, width_px=20,
        start_pos=(100, 300), target_pos=(400, 300), click_pos=(100, 300),
        start_time=0, end_time=2, movement_time_s=2, hit=False,
        start_host_time=1000, end_host_time=1002,
        trajectory=[[0, 100, 300], [.2, 0, 300], [.3, 0, 300],
                    [.4, 0, 300], [.55, 30, 300], [.7, 100, 300]],
    )


def test_clutch_requires_outward_head_rotation_and_return():
    t = trial()
    assert clutch_counts([t], samples(), META) == [1]
    assert clutch_counts([t], samples(yaw=(0, 0, 0, 0, 0)), META) == [0]
    assert clutch_counts([t], samples(yaw=(0, 0, -3, -1, 0)), META) == [0]
    assert clutch_counts([t], samples(yaw=(0, 0, 3, 3, 3)), META) == [0]
    assert clutch_counts([replace(t, trajectory=t.trajectory[:4], click_pos=(0, 300))], samples(), META) == [0]


def test_corner_is_one_episode_and_later_return_is_another():
    t = trial()
    corner = replace(t, trajectory=[[a, x, x] for a, x, _ in t.trajectory], click_pos=(100, 100))
    assert clutch_counts([corner], samples(pitch=(0, 0, 3, 1, 0)), META) == [1]
    two = replace(t, trajectory=t.trajectory + [
        [1.0, 0, 300], [1.1, 0, 300], [1.2, 0, 300], [1.35, 30, 300],
    ])
    head = samples()
    for s in head:
        s.yaw_deg = float(np.interp(s.timestamp, [0, .2, .4, .55, 1, 1.2, 1.35, 2],
                                    [0, 0, 3, 1, 0, 3, 1, 0]))
    assert clutch_counts([two], head, META) == [2]


def test_missing_or_inadequate_recording_is_not_zero():
    t = trial()
    assert clutch_counts([t], [], META) == [None]
    assert clutch_counts([t], samples()[1:], META) == [None]
    assert clutch_counts([t], samples()[::5], META) == [None]
    assert clutch_counts([replace(t, trajectory=[])], samples(), META) == [None]
    assert clutch_counts([t], samples(), {**META, "access_method": "Mouse"}) == [None]
    assert clutch_counts([t], samples(), {}) == [None]
    assert clutch_summary([t], [], META) == {
        "clutch_trial_count": 0, "clutch_count": None, "clutch_mean": None,
    }


def test_legacy_boundary_counts_use_paths_and_merge_corner_contacts():
    original = trial()
    corner = replace(original, trajectory=[
        [0, 100, 100], [.1, 0, 100], [.2, 0, 0], [.3, 100, 0],
        [.4, 100, 100], [.5, 799, 100],
    ])
    clear = replace(original, trajectory=[[0, 100, 100], [.2, 400, 300]])
    assert boundary_counts([corner, clear, replace(original, trajectory=[])], META) == [2, 0, None]
    assert boundary_counts([original], {}) == [None]
    assert boundary_counts([replace(corner, boundary_hits=7)], META) == [7]
    # Do not invent a movement from the planned start position.
    starts_near = replace(original, trajectory=[[.1, 0, 100], [.2, 0, 0]])
    assert boundary_counts([starts_near], META) == [1]


def test_participant_results_pool_eligible_trials_and_apply_review_exclusion():
    first = trial()
    second = replace(first, trajectory=[[0, 100, 300], [.5, 400, 300]])
    session = Session("C", "test", trials=[first, second], samples=samples(),
                      metadata=META, fitts_filename="test.json", head_filename="head.json")
    tables = participant_tables(participant_rows([session]))
    row = tables["Clutch stats"][0]
    assert row["clutch_trial_count"] == 2
    assert row["clutch_count"] == 1
    assert row["clutch_mean"] == pytest.approx(.5)
    assert row["boundary_trial_count"] == 2
    assert row["boundary_count"] == 1
    assert row["boundary_hits"] == pytest.approx(.5)
    assert "clutch_trial_count,clutch_count,clutch_mean" in summary_csv([row])
    assert table_sources([session], "Clutch stats") == {"C": ["test.json", "head.json"]}
    assert not any(key.startswith(("clutch_", "boundary_")) for key in tables["Fitts stats"][0])
    row = participant_rows([session], excluded_trial_ids={id(first)})[0]
    assert row["clutch_trial_count"] == 1
    assert row["clutch_mean"] == 0
    assert row["boundary_count"] == 0


def test_alternative_selection_tables_do_not_include_clutch_or_boundary_measures():
    from colab_session_viewer.selection_stats import alternative_selection_rows

    original = trial()
    session = Session("C", "test", trials=[original], samples=samples(), metadata=META,
                      fitts_filename="test.json", selection_method="mouse")
    no_dwell, _ = alternative_selection_rows([session], 0)
    entry, _ = alternative_selection_rows([session], 0, first_entry=True)
    for row in (no_dwell[0], entry[0]):
        assert not any(key.startswith(("clutch_", "boundary_")) for key in row)


def test_boundary_pool_uses_each_sessions_dimensions_and_weights_targets_equally():
    left = trial()
    right = replace(left, trajectory=[[0, 100, 300], [.2, 790, 300], [.4, 100, 300]])
    first = Session("P", "small", trials=[left, right], metadata=META)
    second = Session("P", "wide", trials=[replace(right)],
                     metadata={**META, "screen_width_px": 1600})
    row = participant_tables(participant_rows([first, second]))["Clutch stats"][0]
    assert row["boundary_trial_count"] == 3
    assert row["boundary_count"] == 2
    assert row["boundary_hits"] == pytest.approx(2 / 3)
    assert row["clutch_count"] is None


def test_desktop_summary_refreshes_after_head_data_is_attached(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session([trial()], META, "test")
        assert "n/a /trial" in window.stats_label.text()
        window.set_head_session_samples(samples())
        assert "1.00 /trial" in window.stats_label.text()
        assert "1 total; 1 trials" in window.stats_label.text()
    finally:
        window.close()
