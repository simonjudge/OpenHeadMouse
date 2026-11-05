"""Initial-rest opening exclusions and per-session notebook inspection."""

import base64
import copy
import csv
import html
import io
import re
from html.parser import HTMLParser

import numpy as np
import pytest

import colab_session_viewer.data as viewer_data
from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import (
    Session, head_pretest_summary, head_range_diagnostics, participant_rows, participant_tables,
    playback_payload, summary_csv,
)
from core import quaternion_math as qm
from core.recorder import Recorder
from core.session_stats import SessionStats
from fitts_law.results import TrialResult


@pytest.fixture(autouse=True)
def mesh_file(tmp_path, monkeypatch):
    path = tmp_path / "mesh.obj"
    path.write_text("v -1 -1 0\nv 1 -1 0\nv 0 1 1\nf 1 2 3\n")
    monkeypatch.setattr("colab_session_viewer.data.OBJ_PATH", path)


def rest_session(name="One", *, participant="P", offset=0.0, host=True):
    recorder = Recorder()
    calibration = qm.from_euler_zyx(*np.radians([5, 4, 0]))
    recorder.set_calibration([(0, calibration)], calibration)
    entries = [
        (9, "initial_rest.move", 100, -60),
        (10.2, "initial_rest.hold", 30, 40),
        (10.5, "initial_rest.hold", 20, 30),
        (10.99, "initial_rest.hold", 15, 20),
        (11, "initial_rest.hold", 10, 10),
        (11.5, "initial_rest.hold", 12, 12),
        (12, "initial_rest.hold", 14, 14),
        (12.5, "initial_rest.hold", 16, 16),
        (13, "range_of_movement.up.hold", 0, 40),
        (13.5, "range_of_movement.up.hold", 0, 42),
        (14, "fitts_test", 120, 65),
    ]
    for t, phase, yaw, pitch in entries:
        recorder.record_sample(
            t, qm.from_euler_zyx(*np.radians([yaw + offset, pitch + offset, 0])),
            metadata={"task_phase": phase}, host_time=1000 + t if host else None,
        )
        if not host:
            recorder._samples[-1].host_time = None  # Legacy files have no saved host clock.
    recorder._events = [{
        "event_type": "task_phase", "timestamp": 10, "host_time": 1010 if host else None,
        "data": {"phase": "initial_rest.hold", "action": "start"},
    }]
    return Session(participant, name, samples=recorder._samples, recorder=recorder, head_filename=f"{name}.json")


@pytest.mark.parametrize("clock", ["host_event", "provider_event", "provider_event_with_host_samples", "fitts_phase", "first_sample"])
def test_exclusion_is_timed_from_hold_start_and_never_uses_preceding_move(clock):
    session = rest_session(host=clock != "provider_event")
    if clock == "provider_event_with_host_samples":
        session.recorder._events[0]["host_time"] = None
    elif clock in ("fitts_phase", "first_sample"):
        session.recorder._events = []
    if clock == "fitts_phase":
        session.metadata = {"pretest_phases": [{
            "phase": "initial_rest.hold", "start_host_time": 1010, "end_host_time": 1013,
        }]}
    before = copy.deepcopy(session.samples)
    full = head_pretest_summary(session)
    trimmed = head_pretest_summary(session, initial_rest_exclude_s=1)
    rest = trimmed["head_initial_rest"]
    expected_pitch = 14 if clock == "first_sample" else 13
    assert rest["pitch_mean_deg"] == pytest.approx(expected_pitch)
    assert rest["sample_count"] == (3 if clock == "first_sample" else 4)
    assert rest["duration_s"] == (None if clock == "provider_event" else 1 if clock == "first_sample" else 1.5)
    assert rest["yaw_absolute_mean_deg"] == pytest.approx(expected_pitch)
    assert rest["yaw_relative_mean_deg"] == pytest.approx(expected_pitch - 5)
    assert rest["yaw_calibration_mean_deg"] == pytest.approx(5)
    assert full["head_initial_rest"]["sample_count"] == 7
    assert full["head_initial_rest"]["pitch_mean_deg"] == pytest.approx(142 / 7)
    direction = trimmed["head_range_of_movement"]["directions"]["up"]
    assert direction["sample_count"] == 2  # Do not trim a directional hold.
    assert direction["angle_mean_deg"] == pytest.approx(41)
    assert direction["offset_from_rest_deg"] == pytest.approx(41 - expected_pitch)
    for actual, original in zip(session.samples, before):
        assert actual.timestamp == original.timestamp
        assert actual.metadata == original.metadata
        np.testing.assert_array_equal(actual.quaternion, original.quaternion)


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_opening_exclusion_is_explicit(invalid):
    with pytest.raises(ValueError, match="finite, non-negative"):
        head_pretest_summary(rest_session(), initial_rest_exclude_s=invalid)


def test_exhausted_hold_preserves_calibration_but_has_no_pose_or_offsets():
    session = rest_session()
    row = participant_rows([session], initial_rest_exclude_s=3)[0]
    assert row["head_initial_rest_sample_count_session_mean"] == 0
    assert row["head_initial_rest_yaw_calibration_mean_deg_session_mean"] == pytest.approx(5)
    assert "head_initial_rest_pitch_mean_deg_session_mean" not in row
    assert "head_range_of_movement_directions_up_offset_from_rest_deg_session_mean" not in row
    assert row["head_range_of_movement_directions_up_angle_mean_deg_session_mean"] == pytest.approx(41)
    viewer = NotebookViewer()
    viewer._add_sessions([session])
    viewer.initial_rest_exclude.value = 3
    assert "No initial-rest samples remain after exclusion" in viewer.rest_exclusion_note.value
    assert "P / One" in viewer.rest_exclusion_note.value
    assert "No initial-rest samples remain after exclusion" in viewer.table.value
    assert "nan" not in summary_csv(viewer.summary_rows()).lower()


def test_fractional_exclusion_includes_exact_boundary_and_uses_retained_spread():
    rest = head_pretest_summary(rest_session(), initial_rest_exclude_s=.5)["head_initial_rest"]
    assert rest["sample_count"] == 6  # Includes the sample exactly 0.5 seconds after hold start.
    pitch = [30, 20, 10, 12, 14, 16]
    assert rest["pitch_mean_deg"] == pytest.approx(np.mean(pitch))
    assert rest["pitch_std_deg"] == pytest.approx(np.std(pitch))
    assert rest["duration_s"] == 2


def test_exhausted_and_missing_holds_do_not_bias_available_session_pose_average():
    first = rest_session()
    short = rest_session("Short", offset=30)
    short.samples = [s for s in short.samples
                     if s.metadata["task_phase"] != "initial_rest.hold" or s.timestamp < 11]
    missing = rest_session("Missing", offset=50)
    missing.samples = [s for s in missing.samples if s.metadata["task_phase"] != "initial_rest.hold"]
    row = participant_rows([first, short, missing], initial_rest_exclude_s=1)[0]
    assert row["head_initial_rest_pitch_mean_deg_session_mean"] == pytest.approx(13)
    assert row["head_initial_rest_sample_count_session_mean"] == 2  # Four retained, zero retained; missing has no hold.
    viewer = NotebookViewer()
    viewer.sessions = [first, short, missing]
    viewer._refresh_table()
    assert "P / Short" in viewer.rest_exclusion_note.value
    assert "P / Missing" not in viewer.rest_exclusion_note.value


def test_each_initial_hold_has_its_own_opening_exclusion():
    session = rest_session()
    session.recorder.record_sample(20.2, qm.from_euler_zyx(0, np.radians(50), 0),
                                   metadata={"task_phase": "initial_rest.hold"}, host_time=1020.2)
    session.recorder.record_sample(21, qm.from_euler_zyx(0, np.radians(18), 0),
                                   metadata={"task_phase": "initial_rest.hold"}, host_time=1021)
    session.recorder._events.append({
        "event_type": "task_phase", "timestamp": 20, "host_time": 1020,
        "data": {"phase": "initial_rest.hold", "action": "start"},
    })
    rest = head_pretest_summary(session, initial_rest_exclude_s=1)["head_initial_rest"]
    assert rest["sample_count"] == 5
    assert rest["pitch_mean_deg"] == pytest.approx(14)


def test_opening_control_defaults_to_one_second_and_only_updates_dependent_summaries(monkeypatch):
    session = rest_session()
    viewer = NotebookViewer()
    shown = []
    monkeypatch.setattr("colab_session_viewer.app.display", shown.append)
    viewer._add_sessions([session])
    assert viewer.initial_rest_exclude.value == 1
    assert viewer.head_opening_exclude.value == 1
    assert viewer.analysis_exclusions in viewer.session_widget.children
    assert viewer.analysis_exclusions.children == (viewer.exclusion_controls, viewer.time_exclusion_controls)
    assert tuple(box.children[1] for box in viewer.time_exclusion_controls.children) == (
        viewer.initial_rest_exclude, viewer.head_opening_exclude,
    )
    assert len(shown) == 1
    before_playback = playback_payload(session)
    before_calibration = copy.deepcopy(session.recorder.calibration)
    before = participant_tables(viewer.summary_rows())
    assert before["Initial head positions"][0]["head_initial_rest_pitch_mean_deg_session_mean"] == pytest.approx(13)
    markup = viewer.table.value
    viewer.initial_rest_exclude.value = 0
    after = participant_tables(viewer.summary_rows())
    assert viewer.table.value != markup
    assert before["Initial head positions"] != after["Initial head positions"]
    assert before["Range of movement stats"] != after["Range of movement stats"]  # Offset from rest only.
    for table in ("Session summary", "Head movement stats", "Fitts targets and hits", "Fitts stats"):
        assert before[table] == after[table]
    assert playback_payload(session) == before_playback
    assert session.recorder.calibration == before_calibration
    assert len(shown) == 2  # Exclusion changes also update the embedded playback statistics.
    viewer.initial_rest_exclude.value = 1
    assert viewer.table.value == markup
    assert viewer.summary_rows()[0]["initial_rest_exclude_s"] == 1
    viewer.initial_rest_exclude.value = float("nan")
    assert "Cannot calculate summaries" in viewer.table.value
    assert "finite, non-negative" in viewer.table.value
    viewer.initial_rest_exclude.value = 1
    assert viewer.table.value == markup


class SessionDetailsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.current = None
        self.row = None
        self.cell = None
        self.table_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.table_depth += 1
        if tag == "details" and attrs.get("class") == "session-details":
            assert self.table_depth == 0  # Session sections are outside participant tables.
            self.current = {"attrs": attrs, "rows": []}
            self.details.append(self.current)
        elif self.current is not None:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = ""

    def handle_data(self, text):
        if self.cell is not None:
            self.cell += text

    def handle_endtag(self, tag):
        if tag == "table":
            self.table_depth -= 1
        if self.current is not None:
            if tag in ("td", "th") and self.cell is not None:
                self.row.append(self.cell)
                self.cell = None
            elif tag == "tr":
                self.current["rows"].append(self.row)
                self.row = None
            elif tag == "details":
                self.current = None


def test_collapsed_session_tables_match_calculations_settings_and_keep_participant_exports():
    sessions = [
        rest_session("<One>"), rest_session("Two", offset=10),
        rest_session("Other", participant="Q", offset=-5),
    ]
    for session in sessions:
        session.trials = [
            TrialResult(
                condition_label="A", block_type="circle", amplitude_px=100, width_px=30,
                start_pos=(0, 0), target_pos=(100, 0), click_pos=(100, i),
                start_time=i * 2, end_time=i * 2 + 1, movement_time_s=1 + i,
                hit=True, trial_index=i, repetition_index=0, trajectory=[[0, 0, 0], [.5, 50, i]],
                start_host_time=1000 + i * 2, end_host_time=1001 + i * 2,
            ) for i in range(3)
        ]
    viewer = NotebookViewer()
    viewer.exclude_targets.value = 1
    viewer.sessions = sessions
    viewer._refresh_table()
    parser = SessionDetailsParser()
    parser.feed(viewer.table.value)
    assert len(parser.details) == 6  # One separate session table below each participant table.
    assert all("open" not in detail["attrs"] for detail in parser.details)
    assert "width:max-content;max-width:none;overflow-wrap:normal" in viewer.table.value
    expected = []
    for session in sessions:
        expected.append(participant_tables(participant_rows(
            [session], viewer.exclusions, initial_rest_exclude_s=1, head_opening_exclude_s=1,
        )))
    for table_index, label in enumerate(expected[0]):
        detail = parser.details[table_index]
        assert detail["rows"][0][:2] == ["participant", "session"]
        assert "file" not in detail["rows"][0]
        assert "fitts file" not in detail["rows"][0]
        actual_rows = detail["rows"][1:]  # Skip header.
        assert len(actual_rows) == len(sessions)
        for actual, session, tables in zip(actual_rows, sessions, expected):
            assert actual[:2] == [session.participant, session.name]
            values = tables[label][0]
            assert actual[2:] == [
                "" if value is None else f"{value:.1f}"
                for key, value in values.items() if key != "participant"
            ]
    assert "&lt;One&gt;" in viewer.table.value
    encoded = re.search(r'download="initial_head_positions.csv" href="data:text/csv;base64,([^"]+)', viewer.table.value)[1]
    exported = list(csv.DictReader(io.StringIO(base64.b64decode(encoded).decode("utf-8-sig"))))
    assert [row["participant"] for row in exported] == ["P", "Q"]
    assert all(row["initial_rest_exclude_s"] == "1.0" for row in exported)
    assert "session" not in exported[0]
    assert exported[0]["head_initial_rest_pitch_mean_deg_session_mean"] == "18.0"


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_head_opening_exclusion_is_explicit(invalid):
    with pytest.raises(ValueError, match="Head-recording opening exclusion"):
        participant_rows([], head_opening_exclude_s=invalid)
    with pytest.raises(ValueError, match="Head-recording opening exclusion"):
        head_range_diagnostics(rest_session(), head_opening_exclude_s=invalid)


def startup_session():
    recorder = Recorder()
    for t, yaw in [(0, 90), (.3, 60), (.99, 30), (1, 1), (1.5, 2), (2, 4)]:
        recorder.record_sample(t, qm.from_euler_zyx(np.radians(yaw), 0, 0), host_time=100 + t)
    return Session("P", "Startup", samples=recorder._samples, recorder=recorder, head_filename="startup.json")


def test_recording_exclusion_keeps_boundary_and_recomputes_kinematics_without_crossing_cutoff(monkeypatch):
    session = startup_session()
    calculate = viewer_data.calculate_neutral_quaternion
    calls = []

    def reference(samples, recorder):
        calls.append(samples)
        return calculate(samples, recorder)

    monkeypatch.setattr(viewer_data, "calculate_neutral_quaternion", reference)
    full = participant_rows([session])[0]
    trimmed = participant_rows([session], head_opening_exclude_s=1)[0]
    assert all(samples is session.samples for samples in calls)
    assert full["head_yaw_range_deg"] == pytest.approx(89)
    assert trimmed["head_yaw_range_deg"] == pytest.approx(3)
    assert trimmed["head_movement_samples"] == 3
    assert trimmed["head_samples"] == 6
    assert trimmed["head_duration_s"] == 2
    expected = SessionStats(session.samples[3:])
    for prefix, values in [
        ("head_speed_deg_s", expected.angular_speed_series()[1]),
        ("head_acceleration_deg_s2", expected.angular_acceleration_series()[1]),
    ]:
        assert trimmed[f"{prefix}_mean"] == pytest.approx(np.mean(values))
        assert trimmed[f"{prefix}_max"] == pytest.approx(np.max(values))
        assert trimmed[f"{prefix}_max"] < full[f"{prefix}_max"]
    diagnostics = head_range_diagnostics(session, head_opening_exclude_s=1)
    assert diagnostics["samples"] == 3
    assert diagnostics["yaw_range_deg"] == pytest.approx(3)


def test_recording_exclusion_exhaustion_single_sample_and_unaffected_other_scopes(monkeypatch):
    session = rest_session()
    viewer = NotebookViewer()
    shown = []
    monkeypatch.setattr("colab_session_viewer.app.display", shown.append)
    viewer._add_sessions([session])
    playback = playback_payload(session)
    calibration = copy.deepcopy(session.recorder.calibration)
    before = participant_tables(viewer.summary_rows())
    viewer.head_opening_exclude.value = 0
    after = participant_tables(viewer.summary_rows())
    for label in before:
        assert (before[label] != after[label]) == (label == "Head movement stats")
    viewer.head_opening_exclude.value = 5
    one = viewer.summary_rows()[0]
    assert one["head_movement_samples"] == 1
    assert one["head_yaw_range_deg"] == 0
    assert one["head_speed_deg_s_max"] is None
    viewer.head_opening_exclude.value = 6
    empty = viewer.summary_rows()[0]
    assert empty["head_movement_samples"] == 0
    assert empty["head_yaw_range_deg"] is None
    assert "P / One" in viewer.head_exclusion_note.value
    assert head_range_diagnostics(session, head_opening_exclude_s=6) is None
    assert playback_payload(session) == playback
    assert session.recorder.calibration == calibration
    assert len(shown) == 4  # Each setting change refreshes playback too.
    viewer.head_opening_exclude.value = float("nan")
    assert "Cannot calculate summaries" in viewer.table.value
    viewer.head_opening_exclude.value = 1
    assert viewer.head_exclusion_note.value == ""


def test_recording_exclusion_uses_shared_clock_and_keeps_available_sessions():
    first = startup_session()
    second = startup_session()
    second.samples = second.samples[:2]
    # A reset provider clock uses the common host clock, as for kinematics.
    first.samples[3].timestamp = -100
    row = participant_rows([first, second], head_opening_exclude_s=1)[0]
    assert row["head_movement_samples"] == 3
    assert row["head_yaw_range_deg"] == pytest.approx(3)
    assert row["head_samples"] == 8
    assert len(second.samples) == 2


def target_trial(start, end, index=0):
    return TrialResult(
        condition_label="A", block_type="circle", amplitude_px=100, width_px=30,
        start_pos=(0, 0), target_pos=(100, 0), click_pos=(100, 0),
        start_time=start, end_time=end, start_host_time=1000 + start, end_host_time=1000 + end,
        movement_time_s=end - start, hit=True, trial_index=index, repetition_index=0,
        trajectory=[[0, 0, 0], [(end - start) / 2, 50, 0]],
    )


def test_target_exclusions_apply_to_initial_pose_session_stats_and_playback_without_removing_frames():
    session = rest_session()
    session.trials = [target_trial(9.5, 11.5), target_trial(12, 14.5, 1)]
    session.fitts_filename = "pointing.json"
    original = copy.deepcopy(session.samples)
    baseline = playback_payload(session)
    payload = playback_payload(
        session, exclude_first_targets=1, initial_rest_exclude_s=1, head_opening_exclude_s=1,
    )
    expected = participant_rows(
        [session], {"P": 1}, initial_rest_exclude_s=1, head_opening_exclude_s=1,
    )[0]
    assert payload["session_summary"] == expected
    assert expected["targets_analysed"] == 1
    assert expected["head_initial_rest_pitch_mean_deg_session_mean"] == pytest.approx(15)
    assert expected["head_initial_rest_sample_count_session_mean"] == 2
    assert expected["head_movement_samples"] == 5
    assert "15.0" in payload["pretest_html"]
    assert len(payload["frames"]) == len(baseline["frames"])
    assert [f["rotation"] for f in payload["frames"]] == [f["rotation"] for f in baseline["frames"]]
    assert [t["excluded"] for t in payload["trials"]] == [True, False]
    assert len(payload["trials"]) == len(baseline["trials"])
    assert all(f["excluded"] for f in payload["frames"][:5])
    assert all(not f["excluded"] for f in payload["frames"][5:])
    for graph in payload["head_graphs"]:
        assert [p[2] for p in graph["points"]] == [f["excluded"] for f in payload["frames"]]
        assert [p[:2] for p in graph["points"]] == [
            p[:2] for p in baseline["head_graphs"][payload["head_graphs"].index(graph)]["points"]
        ]
    for graph in payload["cursor_graphs"]:
        assert [p[2] for p in graph["points"] if p] == [True, True, False, False]
    # Recording-opening exclusion never removes initial-rest samples.
    rest_only = playback_payload(
        session, exclude_first_targets=1, initial_rest_exclude_s=1, head_opening_exclude_s=20,
    )
    assert rest_only["session_summary"]["head_movement_samples"] == 0
    assert rest_only["session_summary"]["head_initial_rest_pitch_mean_deg_session_mean"] == 15
    for sample, before in zip(session.samples, original):
        np.testing.assert_array_equal(sample.quaternion, before.quaternion)
        assert sample.timestamp == before.timestamp


def test_target_intervals_never_create_kinematic_bridges_and_need_shared_clock():
    session = startup_session()
    # Excluding an interior interval leaves an isolated sample and one valid pair.
    trial = target_trial(.2, 1)
    trial.start_host_time, trial.end_host_time = 100.2, 101
    session.trials = [trial]
    row = participant_rows([session], {"P": 1})[0]
    assert row["head_movement_samples"] == 3
    assert row["head_speed_deg_s_mean"] == pytest.approx(4)
    assert row["head_speed_deg_s_max"] == pytest.approx(4)
    assert row["head_acceleration_deg_s2_max"] is None
    session.samples[0].host_time = None
    with pytest.raises(ValueError, match="shared host timestamps"):
        participant_rows([session], {"P": 1})


def test_global_target_control_is_at_top_and_refreshes_embedded_viewer_for_every_participant(monkeypatch):
    sessions = [rest_session(participant=p) for p in ("P", "Q")]
    for session in sessions:
        session.trials = [target_trial(9.5, 11.5), target_trial(12, 14.5, 1)]
        session.fitts_filename = "pointing.json"
    viewer = NotebookViewer()
    shown = []
    monkeypatch.setattr("colab_session_viewer.app.display", shown.append)
    viewer._add_sessions(sessions)
    children = viewer.session_widget.children
    assert children[1].value.startswith("<h3>Exclusions</h3>")
    assert children.index(viewer.analysis_exclusions) < children.index(viewer.viewer)
    assert viewer.exclusion_controls.children[1] is viewer.exclude_targets
    assert viewer.exclusion_controls.children[0].value == "First targets per Fitts file"
    assert len(viewer.exclusion_controls.children) == 2  # A label and one global input.
    viewer.exclude_targets.value = 1
    assert viewer.exclusions == {"P": 1, "Q": 1}
    assert [r["targets_analysed"] for r in viewer.summary_rows()] == [1, 1]
    assert len(shown) == 2
    assert '"excluded": true' in html.unescape(shown[-1]._repr_html_())
    viewer.participant.value = "P"
    assert len(shown) == 3
    assert '"targets_analysed": 1' in html.unescape(shown[-1]._repr_html_())
