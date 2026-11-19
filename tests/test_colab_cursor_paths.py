import base64
import re
import xml.etree.ElementTree as ET

import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.cursor_paths import (
    cursor_path_plot, format_target_selection, numbered_targets, parse_target_selection,
)
from colab_session_viewer.data import Session, fitts_screen_dimensions
from fitts_law.results import TrialResult


def trial(index, *, path=True, host=None):
    return TrialResult(
        condition_label="A", block_type="circle", amplitude_px=100, width_px=20,
        start_pos=(10, 10), target_pos=(110, 10), click_pos=(109, 11),
        start_time=index, end_time=index + 0.5, movement_time_s=0.5,
        hit=True, trial_index=index, trajectory=[[0, 10, 10], [.4, 80, 9]] if path else [],
        start_host_time=None if host is None else host + index,
    )


def session(participant="P", name="Test", size=(200, 100), count=3, host=None):
    return Session(
        participant, name, trials=[trial(i, path=i != 1, host=host) for i in range(count)],
        metadata={"screen_width_px": size[0], "screen_height_px": size[1]},
        fitts_filename=name + ".json",
    )


def extract(markup):
    encoded = re.search(r'data:image/svg\+xml;base64,([^"]+)', markup).group(1)
    return ET.fromstring(base64.b64decode(encoded))


def test_plot_numbers_targets_across_files_with_exclusion_and_downloadable_svg():
    first, second = session(), session(name="Second")
    markup = cursor_path_plot([first, second], [2, 3, 5, 6], 1)
    root = extract(markup)
    ns = {"s": "http://www.w3.org/2000/svg"}
    titles = [el.text for el in root.findall(".//s:g/s:title", ns)]
    assert titles == ["Target 2 (Test, file target 2) - hit", "Target 3 (Test, file target 3) - hit",
                      "Target 5 (Second, file target 2) - hit", "Target 6 (Second, file target 3) - hit"]
    assert "targets 2-3, 5-6" in root.attrib["aria-label"]
    lines = root.findall(".//s:polyline", ns)
    assert len(lines) == 4
    assert len(lines[0].attrib["points"].split()) == 1
    assert len(lines[1].attrib["points"].split()) == 3
    assert lines[0].attrib["stroke"] != lines[1].attrib["stroke"]
    assert 'download="cumulative_cursor_paths_P.svg"' in markup
    assert "Test.json" in markup and "Second.json" in markup
    assert "No retained Fitts targets" in cursor_path_plot([first, second], [1, 4], 1)


def test_two_twelve_target_tests_give_targets_one_to_twenty_four_in_recording_order():
    later, earlier = session(name="Later", count=12, host=500), session(name="Earlier", count=12, host=100)
    numbered = numbered_targets([later, earlier], 0)
    assert [n for n, *_ in numbered] == list(range(1, 25))
    assert [(s.name, f) for _, s, f, _, _ in numbered][11:13] == [("Earlier", 12), ("Later", 1)]
    titles = "".join(extract(cursor_path_plot([later, earlier], range(1, 25), 0)).itertext())
    assert "Target 13 (Later, file target 1)" in titles and "Target 24 (Later, file target 12)" in titles


def test_target_selection_parsing_and_formatting():
    assert parse_target_selection("1-12", 24) == list(range(1, 13))
    assert parse_target_selection(" 1, 2 ,24 ", 24) == [1, 2, 24]
    assert parse_target_selection("3–5; 4, 9", 24) == [3, 4, 5, 9]
    assert parse_target_selection("", 3) == parse_target_selection("All", 3) == [1, 2, 3]
    for bad in ("a", "5-2", "0", "25", "1-30", ",", "1-2-3"):
        with pytest.raises(ValueError):
            parse_target_selection(bad, 24)
    assert format_target_selection([9, 1, 2, 3, 7, 10]) == "1-3, 7, 9-10"


def test_different_and_estimated_screens_are_separate_and_proportional():
    first = session()
    second = session(name="Two", size=(400, 400))
    missing = session(name="No metadata", size=(None, None))
    assert fitts_screen_dimensions(first) == (200, 100, False)
    assert fitts_screen_dimensions(missing)[2]
    root = extract(cursor_path_plot([first, second, missing], range(1, 10), 0))
    ns = {"s": "http://www.w3.org/2000/svg"}
    rects = root.findall("./s:rect", ns)[1:]
    assert len(rects) == 3
    assert float(rects[0].attrib["width"]) / float(rects[0].attrib["height"]) == pytest.approx(2)
    assert float(rects[1].attrib["width"]) / float(rects[1].attrib["height"]) == pytest.approx(1)
    markup = cursor_path_plot([first, second, missing], range(1, 10), 0)
    assert "estimated from recording" in markup
    assert markup.index("</svg>") < markup.index(">Download image") < markup.index("Screen 200 x 100")
    assert "Screen " not in "".join(root.itertext())


def test_labels_are_escaped_and_invalid_ranges_rejected():
    item = session(participant="<P>", name='A <script> "quoted"')
    root = extract(cursor_path_plot([item], [1], 0))
    assert "<P>" in root.attrib["aria-label"]
    assert "<script>" in "".join(root.itertext())
    with pytest.raises(ValueError, match="Target selection"):
        cursor_path_plot([item], [], 0)
    with pytest.raises(ValueError, match="one participant"):
        cursor_path_plot([item, session("Q")], [1, 2], 0)


def test_clip_paths_are_unique_between_participants():
    ns = {"s": "http://www.w3.org/2000/svg"}
    p = extract(cursor_path_plot([session("P")], [1, 2, 3], 0))
    q = extract(cursor_path_plot([session("Q")], [1, 2, 3], 0))
    p_id = p.find(".//s:clipPath", ns).attrib["id"]
    q_id = q.find(".//s:clipPath", ns).attrib["id"]
    assert p_id != q_id
    assert p.find(f'.//s:g[@clip-path="url(#{p_id})"]', ns) is not None
    assert q.find(f'.//s:g[@clip-path="url(#{q_id})"]', ns) is not None


def test_participant_text_controls_stepping_and_exclusions_are_independent():
    viewer = NotebookViewer()
    viewer._add_sessions([session(), session("Q", "Other", count=2)])
    assert viewer.session_widget.children.index(viewer.table) < viewer.session_widget.children.index(viewer.cursor_paths)
    p, q = viewer._cursor_path_controls["P"], viewer._cursor_path_controls["Q"]
    assert (p.text.value, q.text.value) == ("1-3", "1-2")
    original_q = q.output.value
    p.text.value = "2-3"
    assert "Target 1 " not in p.output.value
    assert "Target 2 (Test" in p.output.value
    assert q.output.value == original_q
    p.text.value = "1,x"
    assert "Cannot draw cursor paths" in p.output.value and q.output.value == original_q
    p.text.value = "2-3"
    viewer.exclude_targets.value = 2
    assert p.text.value == "2-3"
    assert "Target 2 " not in p.output.value
    assert "Target 3 (Test" in p.output.value
    assert "No retained Fitts targets" in q.output.value
    viewer._add_sessions([session(name="Third", count=5)])
    assert p.text.value == "2-3"
    p.show_all.click()
    assert p.text.value == "1-8"
    assert "Target 8 (Third, file target 5)" in p.output.value
    p.up.click()
    assert p.text.value == "3"
    p.up.click()
    assert p.text.value == "6"
    p.up.click(); p.up.click(); p.up.click()
    assert p.text.value == "8"
    assert "Target 8 (Third" in p.output.value and "Target 7 " not in p.output.value
    p.down.click()
    assert p.text.value == "7"
    p.text.value = "1-3"
    p.down.click()
    assert p.text.value == "3"
