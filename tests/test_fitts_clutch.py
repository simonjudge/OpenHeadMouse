from dataclasses import replace

import numpy as np
import pytest

from colab_session_viewer.data import Session, participant_rows, participant_tables, playback_payload, summary_csv, table_sources
from core import quaternion_math as qm
from core.recorder import RecordedSample
from fitts_law.clutch import (
    ClutchSettings, boundary_counts, clutch_counts, clutch_episodes, clutch_summary, onscreen_counts,
    onscreen_episodes,
)
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
        assert not any(key.startswith(("clutch_", "onscreen_clutch_", "boundary_")) for key in row)


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


@pytest.mark.parametrize("setting,value", [
    ("still_axis_px", 0), ("min_still_s", .21), ("max_cursor_gap_s", .05),
    ("max_head_gap_s", .01), ("outward_deg", 4), ("return_deg", 3),
    ("return_px", 101), ("return_window_s", .1),
])
def test_changing_each_detection_threshold_affects_result(setting, value):
    original = trial()
    if setting == "still_axis_px":
        original = replace(original, trajectory=[
            [0, 100, 300], [.2, 0, 300], [.3, 1, 300],
            [.4, 2, 300], [.55, 30, 300], [.7, 100, 300],
        ])
    settings = replace(ClutchSettings(), **{setting: value})
    expected = None if setting == "max_head_gap_s" else 0
    assert clutch_counts([original], samples(), META, settings) == [expected]


def test_edge_zone_setting_affects_legacy_paths_not_saved_events():
    original = replace(trial(), trajectory=[
        [0, 100, 300], [.2, 20, 300], [.3, 20, 300],
        [.4, 20, 300], [.55, 50, 300], [.7, 100, 300],
    ])
    narrow = ClutchSettings(edge_zone_px=10)
    assert clutch_counts([original], samples(), META) == [1]
    assert clutch_counts([original], samples(), META, narrow) == [0]
    assert boundary_counts([original], META) == [1]
    assert boundary_counts([original], META, narrow) == [0]
    assert boundary_counts([replace(original, boundary_hits=3)], META, narrow) == [3]
    assert clutch_counts([original], samples(), META, ClutchSettings(edge_zone_px=300)) == [None]


@pytest.mark.parametrize("values", [{"outward_deg": -1}, {"edge_zone_px": float("nan")},
                                   {"return_window_s": float("inf")}, {"max_head_gap_s": 0},
                                   {"onscreen_hysteresis_px": 0}, {"onscreen_max_phase_s": 0},
                                   {"onscreen_speed_ratio": .9}, {"onscreen_opposition": 1.1}])
def test_invalid_clutch_settings_are_rejected(values):
    with pytest.raises(ValueError):
        ClutchSettings(**values)


def test_notebook_controls_refresh_participant_session_and_csv_results():
    from colab_session_viewer.app import NotebookViewer
    from tests.test_colab_selection_stats import csv_rows

    original = trial()
    session = Session("P", "test", trials=[original], samples=samples(),
                      metadata=META, fitts_filename="test.json", head_filename="head.json")
    viewer = NotebookViewer()
    viewer.sessions = [session]
    viewer._refresh_table()
    assert viewer.summary_rows()[0]["clutch_count"] == 1
    viewer.clutch_controls["outward_deg"].value = 4
    assert viewer.summary_rows()[0]["clutch_count"] == 0
    row = csv_rows(viewer.fitts_table.value, "clutch_stats.csv")[0]
    assert row["clutch_count"] == "0"
    assert float(row["clutch_setting_outward_deg"]) == 4
    assert row["clutch_setting_min_still_s"] == "0.06"
    session_details = viewer.fitts_table.value.split("Table 10b: Individual sessions")[1]
    assert "<td>P</td><td>test</td><td>1</td><td>1</td><td>1</td><td>1.0</td><td>1</td><td>0</td>" in session_details
    assert ">0.06<" in session_details
    assert viewer.summary_rows()[0]["boundary_count"] == 1
    viewer._reset_clutch_settings(None)
    assert viewer.summary_rows()[0]["clutch_count"] == 1
    assert viewer.clutch_controls["outward_deg"].value == 2


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


def onscreen_trial():
    return replace(trial(), click_pos=(265, 300), trajectory=[
        [0, 250, 300], [.3, 215, 300], [.6, 175, 300],
        [.7, 205, 300], [.8, 235, 300], [.9, 265, 300],
    ])


def test_onscreen_retreat_and_faster_return_and_tunable_thresholds():
    t = onscreen_trial()
    assert onscreen_counts([t], META) == [1]
    assert onscreen_episodes([t], META) == [[(0.0, .9)]]
    assert onscreen_counts([t], META, offscreen=[[(.3, .7)]]) == [0]
    assert onscreen_counts([t], META, offscreen=[None]) == [1]
    for settings in (
        replace(ClutchSettings(), onscreen_retreat_px=76),
        replace(ClutchSettings(), onscreen_return_px=91),
        replace(ClutchSettings(), onscreen_speed_ratio=2.5),
        replace(ClutchSettings(), onscreen_hysteresis_px=91),
        replace(ClutchSettings(), onscreen_max_phase_s=.5),
        replace(ClutchSettings(), max_cursor_gap_s=.2),
    ):
        assert onscreen_counts([t], META, settings) == [0]
    slower = replace(t, trajectory=[
        [0, 250, 300], [.1, 200, 300], [.3, 220, 300],
        [.5, 240, 300], [.7, 260, 300],
    ])
    assert onscreen_counts([slower], META) == [0]
    jitter = replace(t, trajectory=[[0, 250, 300], [.2, 245, 300], [.4, 250, 300]])
    assert onscreen_counts([jitter], META) == [0]
    sideways = replace(t, trajectory=[
        [0, 250, 300], [.3, 215, 320], [.6, 175, 340],
        [.7, 205, 380], [.8, 235, 420], [.9, 265, 450],
    ], click_pos=(265, 450))
    assert onscreen_counts([sideways], META) == [0]
    assert onscreen_counts([sideways], META, replace(ClutchSettings(), onscreen_opposition=0)) == [1]


def test_onscreen_multiple_episodes_and_target_correction():
    t = onscreen_trial()
    two = replace(t, trajectory=t.trajectory + [
        [1.1, 225, 300], [1.3, 190, 300], [1.4, 230, 300], [1.5, 265, 300],
    ])
    assert onscreen_episodes([two], META) == [[(0.0, .9), (.9, 1.5)]]
    correction = replace(t, trajectory=[
        [0, 400, 300], [.3, 375, 300], [.6, 350, 300],
        [.7, 370, 300], [.8, 390, 300], [.9, 400, 300],
    ], click_pos=(400, 300))
    assert onscreen_counts([correction], META) == [0]


def test_onscreen_allows_edge_visit_but_does_not_bridge_gaps_or_missing_data():
    t = onscreen_trial()
    edge = replace(t, trajectory=[
        [0, 100, 300], [.2, 50, 300], [.4, 20, 300],
        [.5, 50, 300], [.6, 100, 300], [.7, 150, 300],
    ])
    assert onscreen_counts([edge], META) == [1]
    assert onscreen_counts([edge], META, offscreen=[[(.3, .5)]]) == [0]
    gap = replace(t, trajectory=[[0, 250, 300], [.1, 225, 300], [.2, 200, 300],
                                 [1, 220, 300], [1.1, 240, 300], [1.2, 260, 300]])
    assert onscreen_counts([gap], META) == [0]
    assert onscreen_counts([replace(t, trajectory=[])], META) == [None]
    assert onscreen_counts([t], {}) == [None]
    assert onscreen_counts([t], {**META, "access_method": "Mouse"}) == [None]
    assert onscreen_counts([replace(t, trajectory=[[0, 250, 300]])], META) == [None]


def test_onscreen_summary_playback_and_exclusion_use_same_episodes():
    t = onscreen_trial()
    session = Session("P", "test", trials=[t], samples=samples(), metadata=META,
                      fitts_filename="test.json", head_filename="head.json")
    row = participant_tables(participant_rows([session]))["Clutch stats"][0]
    assert (row["onscreen_clutch_trial_count"], row["onscreen_clutch_count"],
            row["onscreen_clutch_mean"]) == (1, 1, 1)
    assert "onscreen_clutch_count" in summary_csv([row])
    assert participant_rows([session], excluded_trial_ids={id(t)})[0]["onscreen_clutch_count"] is None
    no_clutch = replace(t, trajectory=[[0, 250, 300], [.3, 300, 300], [.6, 350, 300]])
    other = Session("P", "other", trials=[no_clutch], metadata=META, fitts_filename="other.json")
    pooled = participant_tables(participant_rows([session, other]))["Clutch stats"][0]
    assert (pooled["onscreen_clutch_trial_count"], pooled["onscreen_clutch_count"],
            pooled["onscreen_clutch_mean"]) == (2, 1, .5)
    payload = playback_payload(session)
    assert payload["trials"][0]["onscreen_clutches"] == [(0.0, .9)]
    assert payload["trials"][0]["offscreen_clutches"] == clutch_episodes([t], samples(), META)[0]
    settings = replace(ClutchSettings(), onscreen_retreat_px=76)
    assert playback_payload(session, clutch_settings=settings)["trials"][0]["onscreen_clutches"] == []


def test_desktop_clutch_indicator_tracks_seek_and_toggle(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session([onscreen_trial()], META, "test")
        window.clock.seek(1000.3)
        assert "On-screen: active" in window.clutch_indicator.text()
        assert "Off-screen: unavailable" in window.clutch_indicator.text()
        window.clock.seek(1001)
        assert "On-screen: inactive" in window.clutch_indicator.text()
        window.show_clutch.setChecked(False)
        assert window.clutch_indicator.isHidden()
        window.load_fitts_session([trial()], META, "off-screen")
        window.set_head_session_samples(samples())
        window.clock.seek(1000.3)
        assert "Off-screen: active" in window.clutch_indicator.text()
        window.clock.seek(1001)
        assert "Off-screen: inactive" in window.clutch_indicator.text()
    finally:
        window.close()


def test_notebook_settings_refresh_playback_and_export():
    from colab_session_viewer.app import NotebookViewer
    from tests.test_colab_selection_stats import csv_rows

    session = Session("P", "test", trials=[onscreen_trial()], samples=samples(),
                      metadata=META, fitts_filename="test.json", head_filename="head.json")
    viewer = NotebookViewer()
    viewer._add_sessions([session])
    assert "&quot;onscreen_clutches&quot;: [[0.0, 0.9]]" in viewer.viewer.value
    viewer.clutch_controls["onscreen_retreat_px"].value = 76
    assert "&quot;onscreen_clutches&quot;: []" in viewer.viewer.value
    row = csv_rows(viewer.fitts_table.value, "clutch_stats.csv")[0]
    assert row["onscreen_clutch_count"] == "0"
    assert float(row["clutch_setting_onscreen_retreat_px"]) == 76
    viewer._reset_clutch_settings(None)
    assert "&quot;onscreen_clutches&quot;: [[0.0, 0.9]]" in viewer.viewer.value


def test_reset_after_failed_approach_not_throw_overshoot_or_incomplete_return():
    t = onscreen_trial()
    failed_approach = replace(t, trajectory=[
        [0, 100, 300], [.2, 200, 300], [.4, 300, 300],
        [.6, 250, 300], [.8, 200, 300], [.9, 260, 300], [1, 320, 300],
    ], click_pos=(320, 300))
    assert onscreen_episodes([failed_approach], META) == [[(.4, 1.0)]]
    throw = replace(t, trajectory=[
        [0, 100, 300], [.1, 300, 300], [.2, 500, 300],
        [.4, 450, 300], [.6, 420, 300], [.8, 400, 300],
    ], click_pos=(400, 300))
    assert onscreen_counts([throw], META) == [0]
    # Even a fast return from a large overshoot is a correction, not a reset.
    overshoot = replace(t, trajectory=[
        [0, 100, 300], [.2, 450, 300], [.4, 500, 300],
        [.6, 550, 300], [.7, 450, 300], [.8, 400, 300],
    ], click_pos=(400, 300))
    assert onscreen_counts([overshoot], META) == [0]
    incomplete = replace(t, trajectory=[
        [0, 250, 300], [.3, 200, 300], [.6, 150, 300], [.7, 210, 300],
    ], click_pos=(210, 300))
    assert onscreen_counts([incomplete], META) == [0]
    assert onscreen_counts([t], {"access_method": "HeadMouse"}) == [1]


def test_notebook_trial_controls_seek_zoom_and_reset_for_sessions():
    from colab_session_viewer.app import NotebookViewer

    first = onscreen_trial()
    second = replace(first, start_time=3, end_time=5, start_host_time=1003, end_host_time=1005)
    session = Session("P", "test", trials=[first, second], metadata=META, fitts_filename="test.json")
    viewer = NotebookViewer()
    viewer._add_sessions([session])
    assert viewer.trial.max == 2
    viewer.next_trial.click()
    assert viewer.trial.value == 2
    assert "&quot;initial_time&quot;: 3.0" in viewer.viewer.value
    viewer.zoom_to_trial.value = True
    assert "&quot;zoom_to_trial&quot;: true" in viewer.viewer.value
    assert "&quot;selected_trial&quot;: 1" in viewer.viewer.value
    viewer.previous_trial.click()
    assert viewer.trial.value == 1
    assert "&quot;initial_time&quot;: 0.0" in viewer.viewer.value
    viewer._add_sessions([Session("P", "short", trials=[first], metadata=META, fitts_filename="short.json")])
    assert viewer.trial.value == 1 and viewer.trial.max == 1
    assert viewer.next_trial.disabled and viewer.previous_trial.disabled
    viewer._add_sessions([Session("P", "head only", samples=samples(), head_filename="head.json")])
    assert viewer.trial.disabled and viewer.zoom_to_trial.disabled
    assert "&quot;zoom_to_trial&quot;: false" in viewer.viewer.value


def test_desktop_trial_zoom_and_timeline_marks(qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow

    first = onscreen_trial()
    second = replace(first, start_time=3, end_time=5, start_host_time=1003, end_host_time=1005)
    window = FittsPlaybackWindow()
    try:
        window.load_fitts_session([first, second], META)
        assert len(window._clutch_marker_items) == 4
        window.trial_spin.setValue(2)
        assert window.clock.time == 1003
        window.zoom_to_trial.setChecked(True)
        assert window.speed_plot.viewRange()[0] == pytest.approx([3, 5])
        window.clock.seek(1005)
        window.clock.play()
        assert window.clock.time == 1003
        window.clock.pause()
        window.zoom_to_trial.setChecked(False)
        assert window.speed_plot.viewRange()[0] == pytest.approx([0, 5])
        window.load_fitts_session([trial()], META)
        window.set_head_session_samples(samples())
        assert any(getattr(item, "toPlainText", lambda: "")() == "E"
                   for item in window._clutch_marker_items)
    finally:
        window.close()
