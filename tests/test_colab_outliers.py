import base64
import csv
import io
import math
import re
from dataclasses import replace

import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import Session, participant_rows
from colab_session_viewer.outliers import review_targets
from colab_session_viewer.selection_stats import alternative_selection_rows
from core import quaternion_math as qm
from core.recorder import Recorder
from fitts_law.results import TrialResult


def trial(index=0):
    return TrialResult(
        condition_label="A", block_type="circle", amplitude_px=100, width_px=20,
        start_pos=(0, 0), target_pos=(100, 0), click_pos=(100 + index % 3 - 1, 0),
        start_time=index * 3, end_time=index * 3 + 2, movement_time_s=2,
        hit=True, trajectory=[[1, 50, 0], [2, 100 + index % 3 - 1, 0]],
    )


def sessions():
    bad = replace(trial(20), click_pos=(-300, 0), movement_time_s=.23,
                  hit=False, trajectory=[[.05, 100, 0], [.23, -300, 0]])
    return [
        Session("A", "First", trials=[trial(i) for i in range(10)],
                fitts_filename="first.json", selection_method="mouse"),
        Session("A", "<Second>", trials=[trial(i) for i in range(10, 20)] + [bad],
                fitts_filename="<second>.json", selection_method="dwell", dwell_time_s=.1),
        Session("B", "Other", trials=[trial(i) for i in range(12)],
                fitts_filename="other.json", selection_method="mouse"),
    ]


def csv_rows(markup, filename):
    encoded = re.search(
        r'download="' + filename + r'" href="data:text/csv;base64,([^"]+)"', markup,
    )[1]
    return list(csv.DictReader(io.StringIO(base64.b64decode(encoded).decode("utf-8-sig"))))


def test_detection_uses_participant_pool_before_review_and_numbers_across_files():
    data = sessions()
    candidates = review_targets(data, 0)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.participant, candidate.number, candidate.file_number) == ("A", 21, 11)
    assert len(candidate.reasons) == 3
    assert candidate.trial is data[1].trials[-1]
    assert review_targets([replace(data[1], trials=[candidate.trial] + data[1].trials[:-1])], 1) == []


def test_sample_sd_threshold_small_pools_zero_sd_and_nonfinite_values():
    normal = replace(trial(), click_pos=(100, 0))
    odd = replace(normal, movement_time_s=20)
    for trials in ([], [normal], [normal] * 11, [normal] * 9 + [odd]):
        assert review_targets([Session("P", "Run", trials=trials, fitts_filename="p.json")], 0) == []
    result = review_targets([Session(
        "P", "Run", trials=[normal] * 10 + [odd], fitts_filename="p.json",
    )], 0)
    assert len(result) == 1 and len(result[0].reasons) == 1
    with pytest.raises(ValueError, match="non-finite"):
        review_targets([Session("P", "Run", trials=[replace(normal, movement_time_s=float("nan"))],
                                fitts_filename="p.json")], 0)


def test_review_checkboxes_update_all_tables_sessions_counts_and_exports_reversibly():
    viewer = NotebookViewer()
    viewer.sessions = sessions()
    viewer._refresh_table()
    candidate = review_targets(viewer.sessions, 0)[0]
    checkbox = viewer._target_review_controls[id(candidate.trial)]
    assert checkbox.value
    before_rows = viewer.summary_rows()
    head_before = viewer.table.value
    plot_before = viewer._cursor_path_controls["A"].output.value
    original_trials = [list(s.trials) for s in viewer.sessions]
    assert "&lt;Second&gt;" in viewer.target_review.children[1].children[1].value
    outputs = (
        ("fitts_table", "fitts_stats.csv"),
        ("nodwell_table", "no_dwell_stats.csv"),
        ("first_entry_table", "first_entry_stats.csv"),
    )
    before = {name: csv_rows(getattr(viewer, name).value, filename) for name, filename in outputs}
    before_alternatives = [
        alternative_selection_rows(viewer.sessions, 0, first_entry=entry)[0]
        for entry in (False, True)
    ]
    assert float(before["fitts_table"][0]["error_rate"]) == pytest.approx(1 / 21)
    assert float(before["nodwell_table"][0]["error_rate"]) == pytest.approx(1 / 21)
    assert float(before["first_entry_table"][0]["error_rate"]) == 0
    checkbox.value = False
    assert viewer._target_review_controls[id(candidate.trial)] is checkbox
    assert viewer.summary_rows()[0]["targets_analysed"] == 20
    assert viewer.summary_rows()[0]["targets_excluded"] == 1
    assert viewer.summary_rows()[1] == before_rows[1]
    assert viewer.table.value.split("</a>", 1)[1] == head_before.split("</a>", 1)[1]
    assert viewer._cursor_path_controls["A"].output.value == plot_before
    assert [s.trials for s in viewer.sessions] == original_trials
    for name, filename in outputs:
        rows = csv_rows(getattr(viewer, name).value, filename)
        assert float(rows[0]["error_rate"]) == 0
        if name == "fitts_table":
            expected = viewer.summary_rows()[0]["mean_movement_time_s"]
        else:
            entry = name == "first_entry_table"
            updated = alternative_selection_rows(
                viewer.sessions, 0, first_entry=entry,
                excluded_trial_ids=viewer._excluded_fitts_trials,
            )[0]
            expected = updated[0]["mean_movement_time_s"]
            assert expected != before_alternatives[int(entry)][0]["mean_movement_time_s"]
        assert float(rows[0]["mean_movement_time_s"]) == float(f"{expected:.1f}")
        assert rows[1] == before[name][1]
        assert "Individual sessions" in getattr(viewer, name).value
    condition = csv_rows(viewer.fitts_table.value, "fitts_test_conditions.csv")
    assert next(row for row in condition if row["session"] == "<Second>")["condition_trials_analysed"] == "10"
    summary = csv_rows(viewer.table.value, "participant_summary.csv")
    assert float(summary[0]["targets_analysed"]) == 20
    checkbox.value = True
    assert viewer.summary_rows() == before_rows
    for name, filename in outputs:
        assert csv_rows(getattr(viewer, name).value, filename) == before[name]
    checkbox.value = False
    viewer.target_review.children[-1].click()
    assert viewer.summary_rows() == before_rows


def test_first_target_exclusions_and_review_choices_do_not_renumber_or_cascade():
    viewer = NotebookViewer()
    viewer.sessions = sessions()
    viewer._refresh_table()
    candidate = review_targets(viewer.sessions, 0)[0]
    viewer._target_review_controls[id(candidate.trial)].value = False
    viewer.exclude_targets.value = 1
    assert id(candidate.trial) in viewer._excluded_fitts_trials
    assert viewer.summary_rows()[0]["targets_analysed"] == 18
    assert viewer.summary_rows()[0]["targets_excluded"] == 3
    assert review_targets(viewer.sessions, 1)[0].number == 21
    viewer.exclude_targets.value = 100
    assert all(row["targets_analysed"] == 0 for row in viewer.summary_rows())
    viewer.exclude_targets.value = 0
    assert not viewer._target_review_controls[id(candidate.trial)].value
    assert len(viewer._target_review_controls) == 1


def test_individual_review_does_not_change_head_statistics_or_raw_counts():
    data = sessions()
    recorder = Recorder()
    for index in range(6):
        recorder.record_sample(
            index, qm.from_euler_zyx(math.radians(index * 5), 0, 0),
            metadata={"task_phase": "fitts_test"}, host_time=1000 + index,
        )
    data[0].samples = recorder._samples
    data[0].recorder = recorder
    data[0].head_filename = "head.json"
    bad = data[1].trials[-1]
    original = participant_rows(data, include_dwell=True)
    reviewed = participant_rows(data, include_dwell=True, excluded_trial_ids={id(bad)})
    assert original[0]["head_movement_samples"] == 6
    assert original[0]["head_yaw_range_deg"] > 0
    for before, after in zip(original, reviewed):
        for key in before:
            if key.startswith("head_") or key in ("sessions", "head_files", "fitts_files", "targets_recorded"):
                assert after[key] == before[key]
