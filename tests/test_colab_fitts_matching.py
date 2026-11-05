import copy
import math
import re
from dataclasses import replace
from collections import Counter

import pytest

import colab_session_viewer.data as viewer_data
from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import (
    Session, column_label, field_description, fitts_condition_rows, matched_fitts_rows,
    participant_rows, participant_tables, planned_id,
)
from fitts_law.results import FittsStats, TrialResult


def trial(distance, width=30, *, click_error=0, index=0, block="circle"):
    return TrialResult(
        condition_label="same configured condition", block_type=block,
        amplitude_px=600, width_px=width, start_pos=(0, 0), target_pos=(distance, 0),
        click_pos=(distance + click_error, 0), start_time=index * 2, end_time=index * 2 + 1,
        movement_time_s=1, hit=abs(click_error) <= width / 2, trial_index=index,
    )


def recordings():
    return [
        Session("A", "One", trials=[trial(300, index=i, click_error=i) for i in range(4)] +
                [trial(520, index=4, click_error=3)], fitts_filename="a.json"),
        Session("B", "One", trials=[trial(d, index=i, click_error=i * 3)
                                   for i, d in enumerate((300, 300, 525))], fitts_filename="b.json"),
        Session("C", "One", trials=[trial(d, index=i, click_error=i * 6)
                                   for i, d in enumerate((300, 300, 525))], fitts_filename="c.json"),
    ]


def test_planned_id_uses_presented_distance_not_configured_radius_or_clicks():
    t = trial(300, click_error=100)
    assert planned_id(t) == pytest.approx(math.log2(11))
    assert planned_id(replace(t, click_pos=(10000, 10000))) == planned_id(t)
    assert planned_id(replace(t, width_px=60)) == pytest.approx(math.log2(6))
    with pytest.raises(ValueError, match="positive target width"):
        planned_id(replace(t, width_px=0))
    assert column_label("index_of_difficulty_bits") == "Effective ID (IDe, bits)"


def test_balanced_nearest_matching_uses_geometry_only_and_preserves_original_results():
    sessions = recordings()
    before = copy.deepcopy(participant_rows(sessions))
    rows = matched_fitts_rows(sessions)
    assert [r["matching_targets_selected"] for r in rows] == [3, 3, 3]
    assert [r["matching_targets_omitted"] for r in rows] == [2, 0, 0]
    assert rows[0]["planned_id_mean_bits"] == pytest.approx(
        (2 * math.log2(11) + math.log2(520 / 30 + 1)) / 3,
    )
    assert abs(rows[0]["planned_id_mean_bits"] - rows[1]["planned_id_mean_bits"]) < .1
    assert rows[1]["index_of_difficulty_bits"] != rows[2]["index_of_difficulty_bits"]
    assert participant_rows(sessions) == before
    assert matched_fitts_rows(list(reversed(sessions))) == rows
    for session in sessions:
        for t in session.trials:
            t.click_pos = (t.target_pos[0] + 100, 100)
            t.movement_time_s = 100
            t.hit = False
    changed = matched_fitts_rows(sessions)
    for old, new in zip(rows, changed):
        assert {k: v for k, v in old.items() if k.startswith(("matching_", "planned_"))} == {
            k: v for k, v in new.items() if k.startswith(("matching_", "planned_"))
        }


def test_matching_applies_global_exclusions_first_and_requires_shared_task_width_and_id():
    rows = matched_fitts_rows(recordings(), 1)
    assert [r["matching_targets_available"] for r in rows] == [4, 2, 2]
    assert [r["matching_targets_selected"] for r in rows] == [2, 2, 2]
    with pytest.raises(ValueError, match="at least two participants"):
        matched_fitts_rows(recordings()[:1])
    for incompatible in (trial(1000), trial(300, width=60), trial(300, block="edge")):
        with pytest.raises(ValueError, match="No shared planned-ID matches"):
            matched_fitts_rows([
                Session("A", "One", trials=[trial(300)]),
                Session("B", "One", trials=[incompatible]),
            ])
    with pytest.raises(ValueError, match="No shared planned-ID matches"):
        matched_fitts_rows(recordings(), 100)
    # Both ends may be close to the anchor but too far from each other.
    def distance(difficulty):
        return 30 * (2 ** difficulty - 1)
    with pytest.raises(ValueError, match="No shared planned-ID matches"):
        matched_fitts_rows([
            Session("A", "One", trials=[trial(distance(3))]),
            Session("B", "One", trials=[trial(distance(2.94))]),
            Session("C", "One", trials=[trial(distance(3.06))]),
        ])


def test_optional_table_updates_and_has_ordered_field_key_without_changing_original_table():
    viewer = NotebookViewer()
    viewer.sessions = recordings()
    viewer._refresh_table()
    original = viewer.table.value
    assert not viewer.match_planned_id.value
    assert viewer.matched_fitts_table.value == ""
    children = viewer.session_widget.children
    assert children.index(viewer.match_planned_id) > children.index(viewer.table)
    viewer.match_planned_id.value = True
    assert viewer.table.value == original
    markup = viewer.matched_fitts_table.value
    assert "matched_planned_id_fitts_stats.csv" in markup
    assert "0.1 bits" in markup
    assert "Effective ID (IDe, bits)" in markup
    assert re.findall(r'<th title="([^"]+)"', markup) == re.findall(r"<code>([^<]+)</code>", markup)
    for key in matched_fitts_rows(viewer.sessions)[0]:
        assert field_description(key)
    viewer.exclude_targets.value = 1
    assert viewer.matched_fitts_table.value != markup
    viewer.exclude_targets.value = 100
    assert "Cannot calculate matched-target comparison" in viewer.matched_fitts_table.value
    viewer.match_planned_id.value = False
    assert viewer.matched_fitts_table.value == ""


def test_original_fitts_table_has_planned_id_immediately_before_effective_id():
    sessions = recordings()
    rows = participant_rows(sessions)
    columns = list(participant_tables(rows)["Fitts stats"][0])
    assert columns[columns.index("index_of_difficulty_bits") - 1] == "planned_id_mean_bits"
    assert rows[0]["planned_id_mean_bits"] == pytest.approx(
        sum(planned_id(t) for t in sessions[0].trials) / len(sessions[0].trials),
    )
    excluded = participant_rows(sessions, {"A": 1})
    assert excluded[0]["planned_id_mean_bits"] == pytest.approx(
        sum(planned_id(t) for t in sessions[0].trials[1:]) / 4,
    )
    assert participant_rows([Session("P", "No trials")])[0]["planned_id_mean_bits"] is None


def test_main_fitts_measures_use_every_retained_target_independently_of_matching():
    sessions = condition_recordings()
    viewer = NotebookViewer()
    viewer.sessions = sessions
    for exclude in (0, 2):
        viewer.exclude_targets.value = exclude
        viewer._refresh_table()
        original_table = viewer.table.value
        original_rows = viewer.summary_rows()
        for row in original_rows:
            trials = [t for s in sessions if s.participant == row["participant"] for t in s.trials[exclude:]]
            assert row["targets_analysed"] == len(trials)
            assert row["planned_id_mean_bits"] == pytest.approx(
                sum(planned_id(t) for t in trials) / len(trials),
            )
            for key, value in FittsStats(trials).summary().items():
                if key.startswith("overall_"):
                    assert row[key.removeprefix("overall_")] == value
            for key, value in FittsStats.accuracy_summary(trials).items():
                assert row[f"path_{key}"] == value
        viewer.match_planned_id.value = True
        for method in ("strict_id", "target_conditions"):
            viewer.fitts_matching_method.value = method
            assert viewer.table.value == original_table
            assert viewer.summary_rows() == original_rows
        viewer.match_planned_id.value = False


def test_condition_inventory_uses_saved_fields_and_observed_counts_before_exclusions():
    session = Session("P", "Run", fitts_filename="<run>.json", trials=[
        trial(300, index=0), trial(300, index=1),
        replace(trial(520, index=2), repetition_index=1),
        trial(300, width=60, index=3),
    ])
    rows = fitts_condition_rows([session], 2)
    assert len(rows) == 1
    first = rows[0]
    assert first["test_amplitudes_px"] == "600"  # Saved setting, not movement distance.
    assert first["test_widths_px"] == "30, 60"
    assert first["test_block_types"] == "circle"
    assert first["condition_trials_recorded"] == 4
    assert first["condition_trials_analysed"] == 2
    assert first["condition_targets_per_repetition_observed"] == "1"
    assert first["test_repetitions_observed"] == "1, 2"
    assert fitts_condition_rows([Session("P", "Head only")]) == []
    viewer = NotebookViewer()
    viewer.sessions = [session]
    viewer._refresh_table()
    markup = viewer.table.value
    assert '<h3>Fitts stats</h3><details class="fitts-conditions">' in markup
    assert 'class="fitts-conditions" open' not in markup
    assert "&lt;run&gt;.json" in markup
    assert "fitts_test_conditions.csv" in markup
    for key in first:
        assert field_description(key)


def condition_recordings():
    sessions = []
    for participant in ("A", "B", "C"):
        for test in (1, 2):
            per_condition = 6 if participant == "A" and test == 1 else 3
            trials = []
            for amplitude in (300, 600):
                for width in (30, 60):
                    for target in range(per_condition):
                        distance = amplitude if per_condition == 6 or target == 0 else amplitude * math.sqrt(3)
                        trials.append(replace(
                            trial(distance, width, index=len(trials), click_error=len(trials) % 4),
                            amplitude_px=amplitude,
                        ))
            sessions.append(Session(
                participant, f"Test {test}", trials=trials, fitts_filename=f"{participant}{test}.json",
            ))
    return sessions


def test_closest_conditions_maximises_balanced_counts_and_retains_every_test(monkeypatch):
    sessions = condition_recordings()
    before = copy.deepcopy(participant_rows(sessions))
    selected = {}
    summary = viewer_data._fitts_comparison_row

    def capture(participant, trials, available):
        selected[participant] = trials
        return summary(participant, trials, available)

    monkeypatch.setattr(viewer_data, "_fitts_comparison_row", capture)
    rows = matched_fitts_rows(sessions, method="target_conditions")
    assert [r["matching_targets_selected"] for r in rows] == [24, 24, 24]
    assert [r["matching_targets_omitted"] for r in rows] == [12, 0, 0]
    assert all(r["matching_tests_retained"] == 2 and r["matching_conditions"] == 4 for r in rows)
    assert all(r["matching_targets_by_test"] == "Test 1: 12; Test 2: 12" for r in rows)
    for trials in selected.values():
        assert len({id(t) for t in trials}) == 24
        assert set(Counter((t.block_type, t.amplitude_px, t.width_px) for t in trials).values()) == {6}
    assert rows[0]["matching_max_id_gap_bits"] > .1
    assert rows[0]["planned_id_mean_bits"] != rows[1]["planned_id_mean_bits"]
    assert participant_rows(sessions) == before
    assert matched_fitts_rows(list(reversed(sessions)), method="target_conditions") == rows
    for session in sessions:
        for t in session.trials:
            t.click_pos = (10000, 10000)
            t.hit = False
            t.movement_time_s = 100
    changed = matched_fitts_rows(sessions, method="target_conditions")
    for old, new in zip(rows, changed):
        keys = [k for k in old if k.startswith(("matching_", "planned_"))]
        assert {k: old[k] for k in keys} == {k: new[k] for k in keys}
    excluded = matched_fitts_rows(sessions, 2, method="target_conditions")
    assert all(r["matching_targets_selected"] == 20 for r in excluded)
    assert all(r["matching_targets_by_test"] == "Test 1: 10; Test 2: 10" for r in excluded)


def test_closest_conditions_uses_nearest_ids_within_test_quotas(monkeypatch):
    sessions = [
        Session("A", "One", trials=[trial(d, index=i) for i, d in enumerate((50, 100, 800))]),
        Session("A", "Two", trials=[trial(d, index=i) for i, d in enumerate((900, 200, 150))]),
        Session("B", "One", trials=[trial(100), trial(200, index=1)]),
    ]
    selected = {}
    summary = viewer_data._fitts_comparison_row

    def capture(participant, trials, available):
        selected[participant] = trials
        return summary(participant, trials, available)

    monkeypatch.setattr(viewer_data, "_fitts_comparison_row", capture)
    rows = matched_fitts_rows(sessions, method="target_conditions")
    assert [t.target_pos[0] for t in selected["A"]] == [100, 200]
    assert rows[0]["matching_targets_by_test"] == "One: 1; Two: 1"
    assert rows[0]["matching_max_id_gap_bits"] == 0


def test_closest_conditions_reserves_rare_test_conditions_before_filling_quotas():
    sessions = [
        Session("A", "One", trials=[trial(300), replace(trial(600), amplitude_px=900)]),
        Session("A", "Two", trials=[trial(300)]),
        Session("B", "One", trials=[trial(300), replace(trial(600), amplitude_px=900)]),
    ]
    rows = matched_fitts_rows(sessions, method="target_conditions")
    assert rows[0]["matching_targets_selected"] == 2
    assert rows[0]["matching_targets_by_test"] == "One: 1; Two: 1"
    # If two tests have only one shared target slot, preserving both is impossible.
    sessions[0].trials = [trial(300)]
    sessions[-1].trials = [trial(300)]
    with pytest.raises(ValueError, match="Cannot retain every test"):
        matched_fitts_rows(sessions, method="target_conditions")


def test_closest_conditions_explicitly_rejects_missing_tests_or_conditions():
    with pytest.raises(ValueError, match="Unknown Fitts matching method"):
        matched_fitts_rows(recordings(), method="unknown")
    sessions = condition_recordings()
    with pytest.raises(ValueError, match="No targets remain"):
        matched_fitts_rows(sessions, 100, method="target_conditions")
    for t in sessions[-1].trials:
        t.amplitude_px = 999
    with pytest.raises(ValueError, match="Cannot retain every test"):
        matched_fitts_rows(sessions, method="target_conditions")
    for s in sessions:
        if s.participant == "B":
            for t in s.trials:
                t.amplitude_px = 777
    with pytest.raises(ValueError, match="No shared block type/amplitude/width"):
        matched_fitts_rows(sessions, method="target_conditions")


def test_closest_option_in_collapsed_review_updates_comparison_only():
    viewer = NotebookViewer()
    viewer.sessions = condition_recordings()
    viewer._refresh_table()
    original = viewer.table.value
    viewer.match_planned_id.value = True
    strict = viewer.matched_fitts_table.value
    review = viewer.fitts_calculation_review
    assert review.get_title(0) == "Fitts calculation review"
    assert review.selected_index is None
    assert viewer.fitts_matching_method in review.children[0].children
    viewer.fitts_matching_method.value = "target_conditions"
    markup = viewer.matched_fitts_table.value
    assert viewer.table.value == original
    assert markup != strict
    assert "no planned-ID cutoff" in markup
    assert "Test 1: 12; Test 2: 12" in markup
    assert re.findall(r'<th title="([^"]+)"', markup) == re.findall(r"<code>([^<]+)</code>", markup)
    for key in matched_fitts_rows(viewer.sessions, method="target_conditions")[0]:
        assert field_description(key)
    viewer.exclude_targets.value = 2
    assert "Test 1: 10; Test 2: 10" in viewer.matched_fitts_table.value
    viewer.exclude_targets.value = 100
    assert "Cannot calculate matched-target comparison" in viewer.matched_fitts_table.value
    viewer.exclude_targets.value = 0
    viewer.fitts_matching_method.value = "strict_id"
    assert viewer.matched_fitts_table.value == strict
