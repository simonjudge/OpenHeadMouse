import math
import re
from dataclasses import replace
from html.parser import HTMLParser

import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import (
    Session, format_field, participant_rows, participant_tables, planned_id,
    playback_payload, summary_csv,
)
from colab_session_viewer.selection_stats import alternative_selection_rows
from tests.test_colab_selection_stats import csv_rows, trial


def recording():
    return Session(
        "P", "Run", fitts_filename="run.json", selection_method="mouse",
        trials=[
            trial(),
            replace(trial(click=(130, 0)), target_pos=(200, 0), width_px=10),
            replace(trial(trajectory=((1, 100, 0), (2, 200, 0)), click=(200, 0)),
                    amplitude_px=300),
        ],
    )


def test_planned_id_range_uses_only_remaining_targets_and_empty_is_unavailable():
    session = recording()
    rows = participant_rows([session])
    ids = [planned_id(t) for t in session.trials]
    assert rows[0]["planned_id_min_bits"] == min(ids)
    assert rows[0]["planned_id_max_bits"] == max(ids)
    assert rows[0]["planned_id_range_bits"] == f"{min(ids):.1f} / {max(ids):.1f}"
    table = participant_tables(rows)["Fitts targets and hits"][0]
    assert table["planned_id_range_bits"] == rows[0]["planned_id_range_bits"]
    assert table["target_widths_px"] == "10, 20"
    assert table["target_amplitudes_px"] == "100, 300"
    reviewed = participant_rows([session], {"P": 1}, excluded_trial_ids={id(session.trials[1])})[0]
    assert reviewed["planned_id_range_bits"] == f"{ids[2]:.1f} / {ids[2]:.1f}"
    reviewed_table = participant_tables([reviewed])["Fitts targets and hits"][0]
    assert reviewed_table["target_widths_px"] == "20"
    assert reviewed_table["target_amplitudes_px"] == "300"
    empty = participant_rows([session], {"P": 3})[0]
    assert empty["planned_id_range_bits"] is None
    assert empty["planned_id_min_bits"] is None and empty["planned_id_max_bits"] is None
    empty_table = participant_tables([empty])["Fitts targets and hits"][0]
    assert empty_table["target_widths_px"] is None
    assert empty_table["target_amplitudes_px"] is None


def test_target_table_displays_widths_and_amplitudes_before_planned_id_range():
    viewer = NotebookViewer()
    viewer.sessions = [recording()]
    viewer._refresh_table()
    rows = csv_rows(viewer.table.value + viewer.fitts_table.value, "fitts_targets_and_hits.csv")
    columns = list(rows[0])
    assert columns.index("target_widths_px") == columns.index("planned_id_range_bits") - 2
    assert columns.index("target_amplitudes_px") == columns.index("planned_id_range_bits") - 1
    assert rows[0]["target_widths_px"] == "10, 20"
    assert rows[0]["target_amplitudes_px"] == "100, 300"
    assert "Distinct target widths" in viewer.fitts_table.value
    assert "configured radius" in viewer.fitts_table.value


def test_error_percentages_show_actual_subset_numerator_denominator_and_counts_are_integers():
    viewer = NotebookViewer()
    viewer.sessions = [recording()]
    viewer._refresh_table()
    assert "66.7% (2/3)" in viewer.fitts_table.value
    assert "66.7% (2/3)" in viewer.nodwell_table.value
    assert "0.0% (0/2)" in viewer.first_entry_table.value
    for markup, filename in (
        (viewer.fitts_table.value, "fitts_stats.csv"),
        (viewer.nodwell_table.value, "no_dwell_stats.csv"),
        (viewer.first_entry_table.value, "first_entry_stats.csv"),
    ):
        row = csv_rows(markup, filename)[0]
        assert "." not in row["targets_analysed"] and "." not in row["misses"]
        assert float(row["error_rate"]) == int(row["misses"]) / int(row["targets_analysed"])
        session_table = markup.split('<div class="session-details">')[-1]
        assert re.search(r"\d+\.\d% \(\d+/\d+\)", session_table)
    viewer.exclude_targets.value = 1
    assert "100.0% (2/2)" in viewer.fitts_table.value
    assert "0.0% (0/1)" in viewer.first_entry_table.value
    viewer.exclude_targets.value = 3
    assert "(0/0)" not in viewer.fitts_table.value + viewer.nodwell_table.value + viewer.first_entry_table.value


@pytest.mark.parametrize("key", [
    "targets_recorded", "targets_excluded", "targets_analysed", "exclude_first_targets_per_file",
    "hits", "misses", "path_trial_count", "dwell_head_trial_count", "nodwell_trial_count",
    "nodwell_unknown_count", "condition_trials_recorded", "matching_targets_selected",
])
def test_target_counts_are_integer_in_html_formatter_and_csv(key):
    assert format_field(key, 12) == "12"
    assert summary_csv([{key: 12}]).splitlines()[1] == "12"
    assert format_field(key, None) == ""
    assert format_field("path_tre", 1.25) == "1.2"
    assert format_field("dwell_attempts_mean", 1.25) == "1.2"


def test_alternative_tables_count_their_contributing_subset():
    session = recording()
    session.selection_method = "dwell"
    session.dwell_time_s = 2.5
    row, coverage = alternative_selection_rows([session], 0)
    assert row[0]["targets_analysed"] == 0 and row[0]["misses"] == 0
    assert row[0]["error_rate"] is None
    assert coverage[0]["invalid_time"] == 3


def test_playback_provides_geometry_id_for_every_target_even_when_excluded():
    session = recording()
    payload = playback_payload(session, exclude_first_targets=1)
    assert payload["trials"][0]["excluded"]
    for original, output in zip(session.trials, payload["trials"]):
        assert output["planned_id_bits"] == pytest.approx(math.log2(
            math.dist(original.start_pos, original.target_pos) / original.width_px + 1,
        ))
        assert output["hit"] is original.hit


def test_every_report_table_header_has_a_short_field_name_and_description():
    class HeaderParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables = []
            self.in_field_key = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "table":
                self.tables.append({"class": attrs.get("class"), "titles": []})
                self.in_field_key = attrs.get("class") == "field-key"
            elif tag == "th" and self.tables and "title" in attrs:
                self.tables[-1]["titles"].append(attrs["title"])

        def handle_endtag(self, tag):
            if tag == "table":
                self.in_field_key = False

    viewer = NotebookViewer()
    viewer.sessions = [recording()]
    viewer._refresh_table()
    assert "<h3>Table 8: No-Dwell stats</h3>" in "".join(
        getattr(child, "value", "") for child in viewer.session_widget.children
    )
    markup = (
        viewer.table.value + viewer.fitts_table.value +
        viewer.nodwell_table.value + viewer.first_entry_table.value
    )
    parser = HeaderParser()
    parser.feed(markup)
    report_tables = [table for table in parser.tables if table["class"] != "field-key"]
    assert report_tables
    for table in report_tables:
        assert table["titles"]
        for title in table["titles"]:
            key, description = title.split(": ", 1)
            assert key
            assert len(description) <= 180
            assert len(description) >= 12
