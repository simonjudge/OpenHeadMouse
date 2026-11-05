import base64
import csv
import io
import re
from dataclasses import replace

import numpy as np
import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.archive import ArchiveDataset, Assignment, manifest_csv
from colab_session_viewer.data import (
    Session, field_description, matched_fitts_rows, participant_rows, participant_tables,
)
from colab_session_viewer.dwell import (
    DWELL_BEHAVIOUR_FIELDS, DWELL_FIELDS, NODWELL_FITTS_FIELDS,
    selection_settings, selection_summary, target_intervals,
)
from core import quaternion_math as qm
from core.recorder import RecordedSample
from fitts_law.results import FittsStats, TrialResult
from tests.test_colab_session_viewer import archive_bytes, make_fitts


def dwell_trial():
    return TrialResult(
        condition_label="test", block_type="circle", amplitude_px=2, width_px=2,
        start_pos=(-2, 0), target_pos=(0, 0), click_pos=(0, 0),
        start_time=10, end_time=14, movement_time_s=4, hit=True,
        trajectory=[[1, 0, 0], [2, 2, 0], [3, 0, 0]],
        start_host_time=1000, end_host_time=1004,
    )


def dwell_session():
    samples = []
    for time in np.arange(0, 4.01, .25):
        samples.append(RecordedSample(
            time, *qm.from_euler_zyx(np.radians(10 * time), 0, 0), 10 * time, 0, 0,
            host_time=1000 + time,
        ))
    return Session(
        "P", "Test", trials=[dwell_trial()], samples=samples,
        selection_method="dwell", dwell_time_s=.6,
    )


def test_inside_target_estimates_include_reentries_without_bridging_movement_gaps():
    session = dwell_session()
    _, intervals = target_intervals(session.trials[0])
    assert intervals == [(.5, 1.5), (2.5, 4)]
    row = selection_summary([session], session.trials)
    assert row["mtnodwell_s"] == pytest.approx(3.4)
    assert row["dwell_inside_time_s_mean"] == pytest.approx(2.5)
    assert row["dwell_final_inside_s_mean"] == pytest.approx(1.5)
    assert row["dwell_settling_s_mean"] == pytest.approx(2)
    assert row["dwell_cursor_path_px_mean"] == pytest.approx(3)
    assert row["dwell_cursor_x_range_px_mean"] == pytest.approx(2)
    assert row["dwell_cursor_y_range_px_mean"] == 0
    assert row["dwell_head_path_deg_mean"] == pytest.approx(25)
    assert row["dwell_head_yaw_range_deg_mean"] == pytest.approx(35)
    assert row["dwell_head_pitch_range_deg_mean"] == 0
    assert row["dwell_cursor_trial_count"] == row["dwell_head_trial_count"] == row["dwell_final_trial_count"] == 1


def test_switch_unknown_and_invalid_dwell_timing_have_explicit_denominators():
    dwell = Session("P", "Dwell", selection_method="dwell", dwell_time_s=.6,
                    trials=[dwell_trial(), replace(dwell_trial(), movement_time_s=.4)])
    switch = Session("P", "Switch", selection_method="external_switch", trials=[dwell_trial()])
    unknown = Session("P", "Unknown", trials=[dwell_trial()])
    sessions = [dwell, switch, unknown]
    trials = [t for s in sessions for t in s.trials]
    row = selection_summary(sessions, trials)
    assert row["nodwell_trial_count"] == 2
    assert row["nodwell_invalid_count"] == row["nodwell_unknown_count"] == 1
    assert row["dwell_trial_count"] == 2
    assert row["mtnodwell_s"] == pytest.approx(3.7)
    assert selection_summary([unknown], unknown.trials)["mtnodwell_s"] is None
    equal = replace(dwell_trial(), movement_time_s=.6)
    row = selection_summary([replace(dwell, trials=[equal])], [equal])
    assert row["mtnodwell_s"] == 0
    assert row["nodwell_throughput_bits_per_s"] is None


def test_adjusted_throughput_uses_only_valid_adjusted_trials_and_preserves_originals():
    session = Session("P", "Dwell", selection_method="dwell", dwell_time_s=.6, trials=[
        replace(dwell_trial(), click_pos=(.1, 0)),
        replace(dwell_trial(), click_pos=(-.2, 0)),
        replace(dwell_trial(), movement_time_s=.3, click_pos=(10, 0)),
    ])
    before = participant_rows([session])[0]
    after = participant_rows([session], include_dwell=True)[0]
    assert all(after[key] == value for key, value in before.items())
    valid = session.trials[:2]
    ide = FittsStats.index_of_difficulty(FittsStats.effective_amplitude(valid), FittsStats.effective_width(valid))
    assert after["nodwell_throughput_bits_per_s"] == pytest.approx(ide / 3.4)
    assert after["targets_analysed"] == 3
    excluded = participant_rows([session], {"P": 1}, include_dwell=True)[0]
    assert excluded["nodwell_trial_count"] == 1
    assert excluded["nodwell_invalid_count"] == 1


@pytest.mark.parametrize("change", ["no_path", "no_head", "no_clock", "partial_head", "miss", "typing"])
def test_missing_paths_clocks_and_final_residence_are_not_silent_zero_head_movement(change):
    session = dwell_session()
    if change == "no_path":
        session.trials[0].trajectory = []
    elif change == "no_head":
        session.samples = []
    elif change == "no_clock":
        session.trials[0].start_host_time = None
    elif change == "partial_head":
        session.samples = session.samples[:-2]
    elif change == "miss":
        session.trials[0].click_pos = (2, 0)
    else:
        session.trials[0].block_type = "typing"
    row = selection_summary([session], session.trials)
    if change == "miss":
        assert row["dwell_final_inside_s_mean"] == 0
        assert row["dwell_settling_s_mean"] is None
        assert row["dwell_final_trial_count"] == 0
    else:
        assert row["dwell_head_path_deg_mean"] is None
        assert row["dwell_head_trial_count"] == 0
    if change in ("no_path", "typing"):
        assert row["dwell_cursor_trial_count"] == 0
        assert row["dwell_cursor_path_px_mean"] is None
    if change == "typing":
        assert row["dwell_unsupported_trial_count"] == 1


def test_stationary_cursor_residence_and_head_wrap_and_duplicate_samples():
    session = dwell_session()
    session.trials[0] = replace(
        dwell_trial(), start_pos=(0, 0), trajectory=[[1, 0, 0], [2, 0, 0], [3, 0, 0]],
    )
    for sample in session.samples:
        sample.quaternion_w, sample.quaternion_x, sample.quaternion_y, sample.quaternion_z = (
            qm.from_euler_zyx(np.radians(179 + 2 * sample.timestamp), 0, 0)
        )
    session.samples.insert(2, session.samples[2])
    row = selection_summary([session], session.trials)
    assert row["dwell_cursor_path_px_mean"] == 0
    assert row["dwell_inside_time_s_mean"] == 4
    assert row["dwell_settling_s_mean"] == 0
    assert row["dwell_head_path_deg_mean"] == pytest.approx(8)
    assert row["dwell_head_yaw_range_deg_mean"] == pytest.approx(8)


def test_no_target_entry_has_zero_residence_but_no_movement_or_settling_estimate():
    trial = replace(dwell_trial(), target_pos=(100, 100), hit=False)
    session = Session("P", "Test", trials=[trial], selection_method="dwell", dwell_time_s=.6)
    row = selection_summary([session], [trial])
    assert row["dwell_cursor_trial_count"] == 1
    assert row["dwell_inside_trial_count"] == 0
    assert row["dwell_inside_time_s_mean"] == row["dwell_final_inside_s_mean"] == 0
    assert row["dwell_cursor_path_px_mean"] is None
    assert row["dwell_settling_s_mean"] is None


def test_bad_cursor_times_are_an_explicit_analysis_error():
    trial = replace(dwell_trial(), trajectory=[[5, 0, 0]])
    with pytest.raises(ValueError, match="outside trial duration"):
        target_intervals(trial)
    trial.trajectory = [[2, 0, 0], [1, 0, 0]]
    with pytest.raises(ValueError, match="chronological"):
        target_intervals(trial)


@pytest.mark.parametrize("method,seconds", [
    ("dwell", None), ("dwell", 0), ("dwell", -1), ("dwell", "nan"),
    ("dwell", "inf"), ("dwell", "invalid"), ("external_switch", .6), ("bad", None),
])
def test_invalid_selection_metadata_is_rejected(method, seconds):
    with pytest.raises(ValueError):
        selection_settings(method, seconds)


def test_manifest_roundtrip_and_legacy_import_preserve_selection_settings(tmp_path):
    path = make_fitts(tmp_path)
    assignments = [Assignment("P", "Test", fitts_file="fitts.json", selection_method="dwell", dwell_time_s=.6)]
    manifest = manifest_csv(assignments)
    dataset = ArchiveDataset.read(archive_bytes(manifest, {"fitts.json": path.read_bytes()}))
    session = dataset.build_sessions(dataset.assignments)[0]
    assert (session.selection_method, session.dwell_time_s) == ("dwell", .6)
    assert manifest_csv(dataset.assignments) == manifest
    legacy = "participant,session,head_file,fitts_file\nP,Test,,fitts.json\n"
    session = ArchiveDataset.read(archive_bytes(legacy, {"fitts.json": path.read_bytes()}))
    assert session.build_sessions(session.assignments)[0].selection_method == "unknown"
    with pytest.raises(ValueError, match="positive"):
        ArchiveDataset.read(archive_bytes(manifest.replace("0.6", "-1"), {"fitts.json": path.read_bytes()}))


@pytest.mark.parametrize("review_method", ["external_switch", "dwell"])
def test_manual_and_zip_review_controls_propagate_method_and_dwell_time(tmp_path, monkeypatch, review_method):
    path = make_fitts(tmp_path)
    viewer = NotebookViewer()
    viewer.import_participant.value = "P"
    viewer.import_session.value = "Manual"
    assert viewer.import_dwell_time.disabled
    viewer.import_selection.value = "dwell"
    assert not viewer.import_dwell_time.disabled
    viewer.import_dwell_time.value = .6
    monkeypatch.setattr("colab_session_viewer.app._uploads", lambda upload:
                        [("fitts.json", path.read_bytes())] if upload is viewer.fitts_upload else [])
    viewer._import_session(None)
    assert viewer.sessions[0].selection_method == "dwell"
    assert viewer.sessions[0].dwell_time_s == .6
    dataset = ArchiveDataset.read(archive_bytes(None, {"fitts.json": path.read_bytes()}))
    viewer._show_archive_review(dataset)
    controls = viewer._review_rows[0]
    controls["session"].value = "ZIP"
    controls["selection_method"].value = review_method
    controls["dwell_time_s"].value = .8
    viewer._confirm_archive(None)
    assert viewer.sessions[1].selection_method == review_method
    assert viewer.sessions[1].dwell_time_s == (.8 if review_method == "dwell" else None)


def test_path_import_api_accepts_selection_metadata(tmp_path):
    viewer = NotebookViewer()
    session = viewer.add_session(
        "P", "Test", fitts_path=make_fitts(tmp_path), selection_method="dwell", dwell_time_s=.6,
    )
    assert session.selection_method == "dwell"
    assert session.dwell_time_s == .6


@pytest.mark.parametrize("method", ["strict_id", "target_conditions"])
def test_matching_subsets_all_new_measures_without_changing_main_results(method):
    first = Session(
        "A", "Test", trials=[replace(dwell_trial()) for _ in range(3)],
        selection_method="dwell", dwell_time_s=.6,
    )
    second = replace(first, participant="B", trials=[replace(dwell_trial()) for _ in range(2)])
    sessions = [first, second]
    original = participant_rows(sessions, include_dwell=True)
    assert original[0]["dwell_trial_count"] == 3
    for exclude, expected in ((0, 2), (1, 1)):
        matched = matched_fitts_rows(sessions, exclude, method=method, include_dwell=True)
        assert all(row["dwell_trial_count"] == row["matching_targets_selected"] == expected for row in matched)
        assert all(row["nodwell_trial_count"] == expected for row in matched)
        assert all(row["dwell_cursor_trial_count"] == expected for row in matched)
    assert participant_rows(sessions, include_dwell=True) == original


def test_optional_dwell_results_flow_to_main_session_matched_tables_and_csvs():
    first = Session("A", "Test", trials=[dwell_trial(), replace(dwell_trial(), click_pos=(.2, 0))],
                    fitts_filename="a.json", selection_method="external_switch")
    second = replace(first, participant="B", fitts_filename="b.json", selection_method="dwell", dwell_time_s=.6,
                     trials=[replace(t) for t in first.trials])
    viewer = NotebookViewer()
    viewer.sessions = [first, second]
    viewer._refresh_table()
    original = viewer.summary_rows()
    viewer.include_dwell.value = True
    assert "MTnodwell average (s)" in viewer.table.value
    assert "dwell_inside_time_s_mean" in viewer.table.value
    for old, new in zip(original, viewer.summary_rows()):
        assert all(new[key] == value for key, value in old.items())
    for method in ("strict_id", "target_conditions"):
        rows = matched_fitts_rows(viewer.sessions, method=method, include_dwell=True)
        assert rows[0]["mtnodwell_s"] is None
        assert rows[0]["nodwell_throughput_bits_per_s"] is None
        assert rows[1]["mtnodwell_s"] == pytest.approx(3.4)
    rows = participant_tables(viewer.summary_rows())["Fitts stats"]
    assert all(field_description(key) for key in rows[0])
    viewer.match_planned_id.value = True
    assert "mtnodwell_s" in viewer.matched_fitts_table.value
    # Individual-session tables have the same adjusted measures, not blank placeholders.
    assert viewer.table.value.count("3.4") >= 2
    viewer.include_dwell.value = False
    assert viewer.summary_rows() == original


def test_dwell_tables_are_separate_in_main_session_matched_results_and_exports():
    switch = Session("A", "Switch", trials=[dwell_trial()], selection_method="external_switch")
    dwell = replace(switch, participant="B", name="Dwell", selection_method="dwell", dwell_time_s=.6,
                    trials=[replace(dwell_trial())])
    viewer = NotebookViewer()
    viewer.sessions = [switch, dwell]
    viewer.include_dwell.value = True
    tables = participant_tables(viewer.summary_rows())
    assert set(tables["Fitts stats"][0]) & set(DWELL_FIELDS) == set(NODWELL_FITTS_FIELDS)
    assert list(tables["Dwell behaviour stats"][0]) == ["participant", *DWELL_BEHAVIOUR_FIELDS]
    assert tables["Dwell behaviour stats"][0]["dwell_head_trial_count"] is None
    assert tables["Dwell behaviour stats"][1]["dwell_head_trial_count"] == 0
    main_fitts, main_dwell = viewer.table.value.split("<h3>Fitts stats</h3>")[1].split(
        "<h3>Dwell behaviour stats</h3>",
    )
    assert 'title="dwell_head_trial_count"' not in main_fitts
    assert 'title="mtnodwell_s"' in main_fitts
    assert '<details class="session-details">' in main_dwell
    assert "<td>-</td>" in main_dwell
    assert "dwell_behaviour_stats.csv" in main_dwell

    def csv_rows(markup, filename):
        encoded = re.search(r'download="' + filename + r'" href="data:text/csv;base64,([^"]+)"', markup)[1]
        return list(csv.DictReader(io.StringIO(base64.b64decode(encoded).decode("utf-8-sig"))))

    exported = csv_rows(main_fitts, "fitts_stats.csv")
    assert set(exported[0]) & set(DWELL_FIELDS) == set(NODWELL_FITTS_FIELDS)
    assert exported[0]["mtnodwell_s"] == exported[0]["nodwell_throughput_bits_per_s"] == ""
    columns = list(exported[0])
    assert columns[columns.index("mean_movement_time_s") + 1] == "mtnodwell_s"
    assert columns[columns.index("throughput_bits_per_s") + 1] == "nodwell_throughput_bits_per_s"
    assert "<td>-</td>" in main_fitts
    exported = csv_rows(main_dwell, "dwell_behaviour_stats.csv")
    assert exported[0]["dwell_head_trial_count"] == ""  # Numeric exports keep missing values blank.
    assert exported[1]["dwell_head_trial_count"] == "0.0"
    viewer.match_planned_id.value = True
    for method in ("strict_id", "target_conditions"):
        viewer.fitts_matching_method.value = method
        markup = viewer.matched_fitts_table.value
        assert "<h4>Dwell behaviour stats: matched targets</h4>" in markup
        exported = csv_rows(markup, "matched_planned_id_fitts_stats.csv")
        assert set(exported[0]) & set(DWELL_FIELDS) == set(NODWELL_FITTS_FIELDS)
        columns = list(exported[0])
        assert columns[columns.index("mean_movement_time_s") + 1] == "mtnodwell_s"
        assert columns[columns.index("throughput_bits_per_s") + 1] == "nodwell_throughput_bits_per_s"
        assert exported[0]["mtnodwell_s"] == exported[0]["nodwell_throughput_bits_per_s"] == ""
        exported = csv_rows(markup, "matched_dwell_behaviour_stats.csv")
        assert exported[0]["dwell_head_trial_count"] == ""
        assert exported[1]["dwell_head_trial_count"] == "0.0"
        for table_markup in markup.split("<h4>Dwell behaviour stats: matched targets</h4>"):
            assert re.findall(r'<th title="([^"]+)"', table_markup) == re.findall(
                r"<code>([^<]+)</code>", table_markup,
            )
    viewer.include_dwell.value = False
    assert "Dwell behaviour stats" not in viewer.table.value
    assert "Dwell behaviour stats" not in viewer.matched_fitts_table.value


@pytest.mark.parametrize("method", ["external_switch", "mouse", "other", "unknown"])
def test_non_dwell_behaviour_is_inapplicable_not_zero(method):
    session = Session("P", "Test", trials=[dwell_trial()], selection_method=method)
    row = selection_summary([session], session.trials)
    assert all(value is None for key, value in row.items() if key.startswith("dwell_"))
    assert row["nodwell_invalid_count"] is None
    assert row["mtnodwell_s"] is None
    assert row["nodwell_throughput_bits_per_s"] is None


def test_mixed_selection_and_exhausted_dwell_sessions_keep_real_counts():
    switch = Session("P", "Switch", trials=[dwell_trial()], selection_method="external_switch")
    dwell = replace(switch, name="Dwell", selection_method="dwell", dwell_time_s=.6,
                    trials=[replace(dwell_trial())])
    mixed = selection_summary([switch, dwell], [*switch.trials, *dwell.trials])
    assert mixed["dwell_trial_count"] == 1
    assert mixed["nodwell_trial_count"] == 2
    exhausted = selection_summary([dwell], [])
    assert exhausted["dwell_trial_count"] == 0
    assert exhausted["dwell_head_trial_count"] == 0


def test_mtnodwell_subtracts_each_sessions_duration_per_trial_before_filtering_and_averaging():
    first = Session(
        "P", "Short dwell", selection_method="dwell", dwell_time_s=.5,
        trials=[replace(dwell_trial(), movement_time_s=2), replace(dwell_trial(), movement_time_s=3)],
    )
    second = Session(
        "P", "Long dwell", selection_method="dwell", dwell_time_s=1.5,
        trials=[replace(dwell_trial(), movement_time_s=1), replace(dwell_trial(), movement_time_s=5)],
    )
    row = selection_summary([first, second], [*first.trials, *second.trials])
    # 2-.5, 3-.5 and 5-1.5; the 1-1.5 target is invalid, not folded into a mean.
    assert row["mtnodwell_s"] == pytest.approx((1.5 + 2.5 + 3.5) / 3)
    assert row["nodwell_trial_count"] == 3
    assert row["nodwell_invalid_count"] == 1
