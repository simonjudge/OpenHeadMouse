import base64
import csv
import io
import math
import re
from dataclasses import replace

import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import Session, field_description
from colab_session_viewer.selection_stats import alternative_selection_rows, first_entry_trial
from fitts_law.results import TrialResult


def trial(trajectory=((1, 50, 0), (2, 100, 0)), click=(100, 0)):
    return TrialResult(
        condition_label="test", block_type="circle", amplitude_px=100, width_px=20,
        start_pos=(0, 0), target_pos=(100, 0), click_pos=click,
        start_time=0, end_time=2, movement_time_s=2, hit=math.dist(click, (100, 0)) <= 10,
        trajectory=[list(point) for point in trajectory],
    )


def csv_rows(markup, filename):
    encoded = re.search(r'download="' + re.escape(filename) + r'" href="data:text/csv;base64,([^"]+)"', markup)[1]
    return list(csv.DictReader(io.StringIO(base64.b64decode(encoded).decode("utf-8-sig"))))


def test_first_entry_interpolates_boundary_contact_and_truncates_path():
    original = trial()
    entry = first_entry_trial(original)
    assert entry.movement_time_s == pytest.approx(1.8)
    assert entry.click_pos == pytest.approx((90, 0))
    assert entry.hit
    assert entry.trajectory[-1] == pytest.approx([1.8, 90, 0])
    assert original.movement_time_s == 2 and original.click_pos == (100, 0)
    assert first_entry_trial(trial(((1, 50, 0), (2, 60, 0)), click=(60, 0))) is None


def test_first_entry_detects_tangency_and_start_inside():
    tangent = replace(
        trial(((1, 100, 10), (2, 120, 10)), click=(120, 10)),
        start_pos=(80, 10),
    )
    entry = first_entry_trial(tangent)
    assert entry.movement_time_s == pytest.approx(1)
    assert entry.click_pos == pytest.approx((100, 10))
    inside = replace(trial(), start_pos=(100, 0))
    entry = first_entry_trial(inside)
    assert entry.movement_time_s == 0
    assert entry.click_pos == (100, 0)


def test_first_entry_rows_use_nominal_width_and_report_omissions_after_exclusions():
    never = trial(((1, 50, 0), (2, 60, 0)), click=(60, 0))
    later_miss = trial(((1, 95, 0), (2, 130, 0)), click=(130, 0))  # Touched, then clicked outside.
    session = Session("P", "Run", trials=[never, trial(), replace(trial(), trajectory=[]), later_miss],
                      fitts_filename="p.json")
    rows, coverage = alternative_selection_rows([session], 0, first_entry=True)
    assert coverage[0] | {"participant": "P"} == {
        "participant": "P", "available": 4, "used": 2, "unknown": 0, "invalid_time": 0,
        "missing_path": 1, "no_entry": 1, "unsupported": 0,
    }
    row = rows[0]
    assert row["nominal_width_px"] == 20
    assert "effective_width_px" not in row and "index_of_difficulty_bits" not in row
    # Interpolated contacts at (90, 0): start (0, 0) at t=0 is prepended to each path.
    first_time = 1.8
    second_time = 90 / 95
    expected_id = math.log2(90 / 20 + 1)
    assert row["first_entry_id_bits"] == pytest.approx(expected_id)
    assert row["mean_movement_time_s"] == pytest.approx((first_time + second_time) / 2)
    assert row["throughput_bits_per_s"] == pytest.approx(expected_id / ((first_time + second_time) / 2))
    _, excluded = alternative_selection_rows([session], 1, first_entry=True)
    assert excluded[0]["available"] == 3 and excluded[0]["no_entry"] == 0
    assert all(field_description(key) for key in row)


def two_participants():
    return [
        Session(p, "Run", trials=[trial(), trial(((1, 95, 0), (2, 100, 0)))],
                fitts_filename=f"{p}.json", selection_method=method, dwell_time_s=dwell)
        for p, method, dwell in (("A", "external_switch", None), ("B", "dwell", .5))
    ]


def test_nodwell_and_first_entry_tables_share_main_fitts_layout():
    viewer = NotebookViewer()
    viewer.sessions = two_participants()
    viewer._refresh_table()
    main = viewer.fitts_table.value.split("<h3>Table 6: Fitts stats</h3>")[1]
    columns = list(csv_rows(main, "fitts_stats.csv")[0])
    renamed = [{"effective_width_px": "nominal_width_px",
                "index_of_difficulty_bits": "first_entry_id_bits"}.get(c, c) for c in columns]
    for number, markup, filename, expected in (
        (8, viewer.nodwell_table.value, "no_dwell_stats.csv", columns),
        (9, viewer.first_entry_table.value, "first_entry_stats.csv", renamed),
    ):
        assert list(csv_rows(markup, filename)[0]) == expected
        assert markup.count("<summary><b>Table Notes</b></summary>") == 1
        assert "Source files</" not in markup
        for suffix, section in zip("abc", ("Field key", "Individual sessions", "Contributing targets and omissions")):
            assert f"<h4>Table {number}{suffix}: {section}</h4>" in markup
        assert "Calculation notes</h4>" not in markup
        assert "A.json" in markup and "B.json" in markup
        titles = re.findall(r'<th title="([^"]+)"', markup)
        assert all(": " in title and len(title.split(": ", 1)[1]) >= 12 for title in titles)
        assert [title.split(": ", 1)[0] for title in titles[:len(expected)]] == expected
    nodwell = csv_rows(viewer.nodwell_table.value, "no_dwell_stats.csv")
    assert float(nodwell[0]["mean_movement_time_s"]) == pytest.approx(2.0)
    assert float(nodwell[1]["mean_movement_time_s"]) == pytest.approx(1.5)
    for markup, explanation in (
        (viewer.nodwell_table.value, "Mean MT after subtracting configured dwell duration"),
        (viewer.first_entry_table.value, "Mean interpolated time from original trial start"),
    ):
        assert markup.count(explanation) == 2  # Header hover and field-key definition.
    assert viewer.first_entry_table.value.count("Average straight-line distance from the saved planned start position") == 2
    assert "not necessarily the actual cursor position" in viewer.first_entry_table.value
    for widget in (viewer.fitts_table, viewer.nodwell_table, viewer.first_entry_table):
        assert widget in viewer.session_widget.children
    assert "mtnodwell_s" not in viewer.table.value


def test_analysis_error_clears_previous_comparison_results():
    viewer = NotebookViewer()
    viewer.sessions = two_participants()
    viewer._refresh_table()
    assert viewer.first_entry_table.value and viewer.nodwell_table.value and viewer.fitts_table.value
    viewer._show_analysis_error(ValueError("bad input"))
    assert "bad input" in viewer.table.value
    assert not viewer.fitts_table.value
    assert not viewer.target_review.children
    assert not viewer.nodwell_table.value
    assert not viewer.first_entry_table.value
    assert not viewer.cursor_paths.children


def test_table_notes_consolidate_supporting_content_with_files_inside_sessions():
    from html.parser import HTMLParser

    class NotesParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.notes = []
            self.current = None

        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.depth += 1
                if dict(attrs).get("class") == "table-notes":
                    assert self.current is None
                    assert "open" not in dict(attrs)
                    self.current = []
                    self.notes.append(self.current)

        def handle_data(self, text):
            if self.current is not None:
                self.current.append(text)

        def handle_endtag(self, tag):
            if tag == "details":
                self.depth -= 1
                if not self.depth:
                    self.current = None

    viewer = NotebookViewer()
    viewer.sessions = two_participants()
    viewer._refresh_table()
    for markup in (viewer.nodwell_table.value, viewer.first_entry_table.value):
        parser = NotesParser()
        parser.feed(markup)
        assert len(parser.notes) == 1
        text = " ".join(parser.notes[0])
        for heading in ("Field key", "Individual sessions", "Analysis scope.",
                        "Contributing targets and omissions"):
            assert heading in text
        assert "Source files" not in text
        assert "A.json" in text and "B.json" in text
        assert parser.depth == 0


def test_field_key_rows_align_labels_and_meanings_without_repeating_session_definitions():
    from html.parser import HTMLParser

    class FieldKeyParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside = False
            self.rows = []
            self.current = None

        def handle_starttag(self, tag, attrs):
            if tag == "table" and dict(attrs).get("class") == "field-key":
                self.inside = True
            if self.inside and tag == "tr":
                self.current = []
                self.rows.append(self.current)
            if self.inside and tag in ("td", "th"):
                self.current.append(dict(attrs))

        def handle_endtag(self, tag):
            if tag == "table":
                self.inside = False

    viewer = NotebookViewer()
    viewer.sessions = two_participants()
    viewer._refresh_table()
    markup = viewer.first_entry_table.value
    parser = FieldKeyParser()
    parser.feed(markup)
    assert markup.count('class="field-key"') == 1
    assert "<dl>" not in markup
    assert len(parser.rows) == len(csv_rows(markup, "first_entry_stats.csv")[0]) + 1
    assert all(len(row) == 2 for row in parser.rows)
    assert all(row[0]["scope"] == "row" for row in parser.rows[1:])
    assert all("vertical-align:top" in cell["style"] for row in parser.rows for cell in row)
    assert markup.count("Contact uses linear interpolation") == 2  # Header hover and field key.
    assert "Calculation notes</h4>" not in markup
    assert "not the preceding trial" in markup
    escaped = NotebookViewer._table_html(
        [{"participant": "P"}], "test.csv", "Download",
        field_notes={"participant": '<unsafe & "quoted">'},
    )
    assert "&lt;unsafe &amp; &quot;quoted&quot;&gt;" in escaped
