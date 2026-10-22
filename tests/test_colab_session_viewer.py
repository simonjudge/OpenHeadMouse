"""Synthetic multi-participant notebook imports, statistics and playback parity."""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from colab_session_viewer.data import (
    Session, participant_rows, payload_json, playback_payload, retained_trials, summary_csv,
)
from core import quaternion_math as qm
from core.recorder import Recorder
from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def mesh_file(tmp_path, monkeypatch):
    path = tmp_path / "mesh.obj"
    path.write_text("v -1 -1 0\nv 1 -1 0\nv 0 1 1\nf 1 2 3\n")
    monkeypatch.setattr("colab_session_viewer.data.OBJ_PATH", path)
    return path


def make_fitts(tmp_path, name="fitts.json", count=5, offset=1000, host=True):
    logger = FittsResultsLogger(tmp_path)
    logger.metadata.update(screen_width_px=800, screen_height_px=600)
    for i in range(count):
        logger.add_trial(TrialResult(
            condition_label="A" if i % 2 == 0 else "B", block_type="circle",
            amplitude_px=100, width_px=30, start_pos=(100, 100),
            target_pos=(200, 100), click_pos=(198 + i, 100 + i),
            start_time=offset + i * 2, end_time=offset + i * 2 + 1,
            movement_time_s=1 + i * 0.1, hit=i != 0, trial_index=i,
            repetition_index=0,  # Proves exclusion is NOT a repetition filter.
            trajectory=[[0, 100, 100], [0.4, 160, 104], [0.7, 190, 99]],
            start_host_time=offset + i * 2 if host else None,
            end_host_time=offset + i * 2 + 1 if host else None,
        ))
    return logger.save(name)


def make_head(tmp_path, name="head.json", offset=1000):
    recorder = Recorder(tmp_path)
    recorder.set_calibration(
        [(0, qm.from_euler_zyx(0.1, 0, 0))], qm.from_euler_zyx(0.1, 0, 0)
    )
    for i in range(101):
        recorder.record_sample(
            i * 0.1, qm.from_euler_zyx(0.1 + i * 0.002, i * 0.001, 0),
            host_time=offset + i * 0.1, metadata={"task_phase": "fitts_test"},
        )
    recorder.format = Path(name).suffix[1:]
    return recorder.save(name)


def archive_bytes(manifest, files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if manifest is not None:
            archive.writestr("manifest.csv", manifest)
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_first_targets_per_file_and_pooled_summary(tmp_path):
    first = Session.load("P01", "Test 1", fitts_path=make_fitts(tmp_path))
    second = Session.load("P01", "Test 2", fitts_path=make_fitts(tmp_path, "test2.json", count=3, offset=2000))
    other = Session.load("P02", "Test 1", fitts_path=make_fitts(tmp_path, "other.json"))
    rows = participant_rows([first, second, other], {"P01": 2})
    assert len(rows) == 2
    row = rows[0]
    assert row["participant"] == "P01"
    assert row["sessions"] == 2
    assert row["targets_recorded"] == 8
    assert row["targets_excluded"] == 4
    assert row["targets_analysed"] == 4
    assert [t.trial_index for t in retained_trials(first, 2)] == [2, 3, 4]
    assert [t.trial_index for t in retained_trials(first, 1)] == [1, 2, 3, 4]
    assert len(first.trials) == 5
    assert rows[1]["targets_analysed"] == 5
    expected = FittsStats(first.trials[2:] + second.trials[2:]).summary()
    for key, value in expected.items():
        if key.startswith("overall_"):
            assert row[key.removeprefix("overall_")] == pytest.approx(value)
    assert row["path_trial_count"] == 4
    assert row["error_rate"] == 0
    assert rows[1]["error_rate"] == pytest.approx(0.2)


def test_exhausted_file_has_missing_not_success_shaped_measures(tmp_path):
    session = Session.load("P", "one", fitts_path=make_fitts(tmp_path, count=1))
    row = participant_rows([session], {"P": 2})[0]
    assert row["targets_analysed"] == 0
    assert row["targets_excluded"] == 1
    assert row["throughput_bits_per_s"] is None
    assert row["mean_movement_time_s"] is None
    assert row["error_rate"] is None
    assert row["path_mv_px"] is None
    for invalid in (-1, 1.5, True):
        with pytest.raises(ValueError, match="non-negative integer"):
            retained_trials(session, invalid)


@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_head_json_csv_and_native_orientation_parity(tmp_path, qt_app, suffix, mesh_file, monkeypatch):
    from session_viewer.session_viewer import SessionVisualizerWindow

    monkeypatch.setattr("session_viewer.session_viewer.download_mediapipe_model", lambda: mesh_file)
    session = Session.load("P", "head", head_path=make_head(tmp_path, f"head.{suffix}"))
    payload = playback_payload(session)
    native = SessionVisualizerWindow()
    try:
        native.load_session(session.samples, recorder=session.recorder)
        np.testing.assert_allclose([f["t"] for f in payload["frames"]], native.timestamps)
        for index, frame in enumerate(payload["frames"]):
            expected = native.frame_data[index]
            np.testing.assert_allclose(frame["angles"], [
                expected["yaw_rel_deg"], expected["pitch_rel_deg"], expected["roll_rel_deg"],
            ], atol=1e-10)
            assert frame["speed"] == pytest.approx(native.velocities[index])
            assert frame["acceleration"] == pytest.approx(native.accelerations[index])
        assert payload["duration"] == 10
        assert payload["geometry"]["vertices"]
        assert len(payload["head_graphs"]) == 4
    finally:
        native.close()


def test_synchronised_native_and_browser_states_match(tmp_path, qt_app):
    from session_viewer.fitts_playback import FittsPlaybackWindow
    from session_viewer.playback_clock import check_sync

    session = Session.load("P", "pair", head_path=make_head(tmp_path),
                           fitts_path=make_fitts(tmp_path))
    assert check_sync(session.samples, session.trials, session.metadata).ok
    payload = playback_payload(session)
    assert payload["synced"]
    assert payload["duration"] == 9
    assert payload["frames"][-1]["t"] == 9
    native = FittsPlaybackWindow()
    checks = []
    try:
        native.load_fitts_session(session.trials, session.metadata)
        for relative in (0, 0.4, 0.7, 1, 1.5, 2, 2.4, 2.7, 4.9, 9):
            native.clock.seek(1000 + relative)
            checks.append({
                "t": relative, "trial": native.canvas.trial_index,
                "path": native.canvas.path.tolist(), "cursor": list(native.canvas.cursor),
                "clicked": native.canvas.clicked,
                "frame": max(0, int(np.searchsorted(
                    [f["t"] + payload["clock_origin"] for f in payload["frames"]],
                    1000 + relative, side="right")) - 1),
                "measures": FittsStats.path_measures(
                    session.trials[native.canvas.trial_index], native.canvas.path),
            })
        script = """
const fs = require('node:fs');
const {stateAt} = require('./colab_session_viewer/viewer.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(input.checks.map(c => {
    const state = stateAt(input.payload, c.t);
    return {t:c.t, trial:state.trialIndex, path:state.points, cursor:state.cursor,
        clicked:state.clicked, frame:state.frameIndex, measures:state.measures};
})));
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True,
                                input=json.dumps({"payload": payload, "checks": checks}),
                                capture_output=True, check=True)
        actual = json.loads(result.stdout)
        for expected, state in zip(checks, actual):
            assert state["trial"] == expected["trial"]
            assert state["clicked"] == expected["clicked"]
            assert state["frame"] == expected["frame"]
            if expected["measures"] is None:
                assert state["measures"] is None
            else:
                assert state["measures"] == pytest.approx(expected["measures"])
            np.testing.assert_allclose(state["path"], expected["path"])
            np.testing.assert_allclose(state["cursor"], expected["cursor"])
    finally:
        native.close()


def test_legacy_standalone_and_mismatched_pair_refusal(tmp_path):
    head = make_head(tmp_path)
    legacy = make_fitts(tmp_path, host=False)
    session = Session.load("P", "legacy", fitts_path=legacy)
    assert not playback_payload(session)["synced"]
    with pytest.raises(ValueError, match="shared host timestamps"):
        Session.load("P", "bad pair", head_path=head, fitts_path=legacy)
    later = make_fitts(tmp_path, "later.json", offset=2000)
    with pytest.raises(ValueError, match="do not overlap"):
        Session.load("P", "bad pair", head_path=head, fitts_path=later)


def test_pretest_only_and_head_summary_no_cross_file_derivatives(tmp_path):
    logger = FittsResultsLogger(tmp_path)
    logger.metadata["pretest_phases"] = [{
        "phase": "initial_rest.hold", "task": "initial_rest", "segment": "hold",
        "start_host_time": 1000, "end_host_time": 1002,
        "cursor_trajectory": [{"host_time": 1000.5, "x_px": 120, "y_px": 130}],
    }, {
        "phase": "range_of_movement.left.hold", "task": "range_of_movement",
        "segment": "hold", "direction": "left", "start_host_time": 1001, "end_host_time": 1002,
        "cursor_trajectory": [{"host_time": 1001.5, "x_px": 140, "y_px": 130}],
    }]
    session = Session.load("P", "head exercise", head_path=make_head(tmp_path),
                           fitts_path=logger.save("exercise.json"))
    payload = playback_payload(session)
    assert payload["duration"] == 2
    assert payload["trials"] == []
    assert payload["pretests"][0]["points"] == [[0.5, 120, 130]]
    assert payload["session_summary"]["targets_analysed"] == 0
    other = Session.load("P", "head only", head_path=make_head(tmp_path, "other.json", offset=90000))
    row = participant_rows([session, other])[0]
    stats = participant_rows([other])[0]
    assert row["head_duration_s"] == pytest.approx(20)
    assert row["head_samples"] == 202
    assert row["head_speed_deg_s_max"] == pytest.approx(stats["head_speed_deg_s_max"])
    assert row["head_speed_deg_s_mean"] == pytest.approx(stats["head_speed_deg_s_mean"])
    assert row["head_yaw_range_deg"] == pytest.approx(stats["head_yaw_range_deg"])
    assert not any(key.startswith("cursor_") for key in row)


def test_archive_multiple_datasets_and_atomic_duplicates(tmp_path):
    from colab_session_viewer.app import NotebookViewer, load_archive

    content = make_fitts(tmp_path).read_bytes()
    archive = archive_bytes(
        "participant,session,head_file,fitts_file\nP01,Test 1,,a/test.json\nP01,Test 2,,b/test.json\n",
        {"a/test.json": content, "b/test.json": content},
    )
    viewer = NotebookViewer()
    viewer._add_sessions(load_archive(archive))
    viewer._add_sessions(load_archive(archive_bytes(
        "participant,session,head_file,fitts_file\nP02,Test 1,,test.json\n", {"test.json": content})))
    assert len(viewer.sessions) == 3
    assert len(viewer.summary_rows()) == 2
    assert viewer.participant.options == ("P01", "P02")
    assert viewer.session.options == ("Test 1",)
    viewer.participant.value = "P01"
    assert viewer.session.options == ("Test 1", "Test 2")
    viewer.session.value = "Test 2"
    children = viewer.widget.children
    assert next(i for i, child in enumerate(children)
                if getattr(child, "value", "").startswith("<h3>Import recordings")) < children.index(viewer.viewer)
    assert children.index(viewer.archive_review) < children.index(viewer.viewer) < children.index(viewer.table)
    assert sum(child is viewer.viewer for child in viewer.widget.children) == 1
    viewer.exclusion_controls.children[0].value = 2
    assert viewer.summary_rows()[0]["targets_analysed"] == 6
    assert viewer.summary_rows()[1]["targets_analysed"] == 5
    assert "Download summary CSV" in viewer.table.value
    with pytest.raises(ValueError, match="Duplicate"):
        viewer._add_sessions(load_archive(archive))
    assert len(viewer.sessions) == 3
    bad_batch = archive_bytes(
        "participant,session,head_file,fitts_file\nP03,Good,,ok.json\nP03,Bad,,missing.json\n",
        {"ok.json": content},
    )
    with pytest.raises(ValueError, match="missing file"):
        viewer._add_sessions(load_archive(bad_batch))
    assert len(viewer.sessions) == 3


def test_archive_paths_errors_and_html_escaping(tmp_path):
    from colab_session_viewer.app import load_archive, viewer_document

    with pytest.raises(ValueError, match="relative ZIP paths"):
        load_archive(archive_bytes(
            "participant,session,head_file,fitts_file\nP,Test,,../file.json\n", {}))
    with pytest.raises(ValueError, match="columns"):
        load_archive(archive_bytes("participant,session\nP,Test\n", {}))
    session = Session.load("</script><img src=x>", "test", fitts_path=make_fitts(tmp_path))
    payload = playback_payload(session)
    encoded = payload_json(payload)
    assert "</script><img" not in encoded
    assert json.loads(encoded)["title"] == payload["title"]
    document = viewer_document(payload)
    assert "__PAYLOAD__" not in document
    assert "</script><img" not in document
    text = summary_csv([{"participant": "=1+1", "unavailable": None}])
    assert "'=1+1" in text
    assert "None" not in text


def test_repeated_widget_uploads_clear_stale_pairs_and_preserve_labels(tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from colab_session_viewer.app import NotebookViewer, _uploads

    source = make_fitts(tmp_path)
    raw = source.read_bytes()
    assert _uploads(SimpleNamespace(value={"legacy.json": {"content": raw}})) == [("legacy.json", raw)]
    viewer = NotebookViewer()
    for name in ("Test 1", "Test 2"):
        viewer.import_participant.value = "P"
        viewer.import_session.value = name
        viewer.fitts_upload.value = ({
            "name": "original.json", "type": "application/json", "size": len(raw),
            "content": memoryview(raw), "last_modified": datetime.now(timezone.utc),
        },)
        viewer._import_session(None)
        assert viewer.fitts_upload.value == ()
    assert len(viewer.sessions) == 2
    assert viewer.sessions[0].fitts_filename == "original.json"
    assert viewer.session.value == "Test 2"


def test_exclusion_uses_chronology_not_json_order_or_trial_indices(tmp_path):
    source = make_fitts(tmp_path)
    raw = json.loads(source.read_text())
    raw["trials"].reverse()
    source.write_text(json.dumps(raw))
    session = Session.load("P", "test", fitts_path=source)
    assert [t.trial_index for t in retained_trials(session, 2)] == [2, 3, 4]


def test_invalid_recordings_report_explicit_errors(tmp_path):
    with pytest.raises(ValueError, match="names are required"):
        Session.load("", "test")
    with pytest.raises(ValueError, match="Choose a head"):
        Session.load("P", "test")
    path = make_head(tmp_path)
    data = json.loads(path.read_text())
    data["samples"][0]["quaternion_w"] = float("nan")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="non-finite"):
        Session.load("P", "test", head_path=path)
    from colab_session_viewer.app import NotebookViewer
    viewer = NotebookViewer()
    viewer._import_session(None)
    assert "Import failed" in viewer.message.value
    assert not viewer.sessions


def test_notebook_code_valid_and_imports_without_qt():
    notebook = json.loads((ROOT / "colab_session_viewer" / "session_viewer.ipynb").read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), cell["id"], "exec")
    result = subprocess.run([
        sys.executable, "-c",
        "import sys; import colab_session_viewer.app; "
        "assert not any(n.startswith(('PySide6', 'pynput', 'cv2')) for n in sys.modules)",
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manifestless_zip_pairs_by_content_and_timestamps_not_names(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    files = {
        "a/unrelated-name.json": make_head(tmp_path).read_bytes(),
        "b/head-sounding-name.json": make_fitts(tmp_path).read_bytes(),
        "c/head.csv": make_head(tmp_path, "second.csv", offset=3000).read_bytes(),
        "d/test.json": make_fitts(tmp_path, "second-fitts.json", offset=3000).read_bytes(),
    }
    dataset = ArchiveDataset.read(archive_bytes(None, files))
    assert not dataset.has_manifest
    assert len(dataset.assignments) == 2
    assert {(a.head_file, a.fitts_file) for a in dataset.assignments} == {
        ("a/unrelated-name.json", "b/head-sounding-name.json"), ("c/head.csv", "d/test.json"),
    }
    assert all(not a.participant for a in dataset.assignments)
    for index, assignment in enumerate(dataset.assignments):
        assignment.participant = "P01"
        assignment.session = f"Test {index}"
    sessions = dataset.build_sessions(dataset.assignments)
    assert all(playback_payload(s)["synced"] for s in sessions)
    assert sessions[0].head_filename == "a/unrelated-name.json"
    assert "host timestamps 1000." in dataset.describe("a/unrelated-name.json")
    assert "5 targets" in dataset.describe("b/head-sounding-name.json")


def test_zip_pairing_uses_overlap_ranking_and_keeps_ambiguous_files_separate(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    head = make_head(tmp_path).read_bytes()
    fitts = make_fitts(tmp_path).read_bytes()
    dataset = ArchiveDataset.read(archive_bytes(None, {
        "head1.json": head, "head2.json": head, "test.json": fitts,
    }))
    assert len(dataset.assignments) == 3
    assert not any(a.head_file and a.fitts_file for a in dataset.assignments)
    assert any("multiple possible timestamp matches" in note for note in dataset.warnings)
    # Both overlap, but the later-starting head covers less of the test.
    dataset = ArchiveDataset.read(archive_bytes(None, {
        "best.json": head,
        "partial.json": make_head(tmp_path, "partial.json", offset=1008).read_bytes(),
        "test.json": fitts,
    }))
    paired = next(a for a in dataset.assignments if a.fitts_file)
    assert paired.head_file == "best.json"
    assert len(dataset.assignments) == 2


def test_zip_pairing_includes_pretest_only_files_and_excludes_nonoverlap(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    logger = FittsResultsLogger(tmp_path)
    logger.metadata["pretest_phases"] = [{
        "phase": "initial_rest.hold", "task": "initial_rest", "segment": "hold",
        "start_host_time": 1000, "end_host_time": 1002, "cursor_trajectory": [],
    }]
    dataset = ArchiveDataset.read(archive_bytes(None, {
        "head.json": make_head(tmp_path).read_bytes(),
        "exercise.json": logger.save("exercise.json").read_bytes(),
        "unrelated.json": make_fitts(tmp_path, offset=5000).read_bytes(),
    }))
    assert len(dataset.assignments) == 2
    exercise = next(a for a in dataset.assignments if a.fitts_file == "exercise.json")
    assert exercise.head_file == "head.json"
    unrelated = next(a for a in dataset.assignments if a.fitts_file == "unrelated.json")
    assert unrelated.head_file == ""


def test_zip_discovery_reports_invalid_files_and_legacy_unmatched_streams(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    head = make_head(tmp_path)
    data = json.loads(head.read_text())
    for sample in data["samples"]:
        sample.pop("host_time", None)
    dataset = ArchiveDataset.read(archive_bytes(None, {
        "head.json": json.dumps(data),
        "legacy.json": make_fitts(tmp_path, host=False).read_bytes(),
        "settings.json": '{"setting": true}', "broken.json": "{",
        "notes.txt": "not a recording",
    }))
    assert len(dataset.recordings) == 2
    assert len(dataset.assignments) == 2
    assert not any(a.head_file and a.fitts_file for a in dataset.assignments)
    assert len(dataset.warnings) == 2
    assert "No shared host timestamps" in dataset.describe("head.json")
    with pytest.raises(ValueError, match="no usable recordings"):
        ArchiveDataset.read(archive_bytes(None, {"config.json": "{}"}))


def test_review_labels_manifest_roundtrip_and_timestamp_offset(tmp_path):
    import base64
    import re
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.archive import ArchiveDataset, manifest_csv

    # The provider clock starts at zero; host clock and Fitts start differ by 2s.
    files = {
        "participant/head.json": make_head(tmp_path, offset=998).read_bytes(),
        "test/fitts.json": make_fitts(tmp_path).read_bytes(),
    }
    dataset = ArchiveDataset.read(archive_bytes(None, files))
    viewer = NotebookViewer()
    viewer._show_archive_review(dataset)
    assert not viewer.sessions
    assert len(viewer._review_rows) == 1
    viewer._confirm_archive(None)
    assert "names are required" in viewer.message.value
    row = viewer._review_rows[0]
    row["participant"].value = "P,01"
    row["session"].value = "Mouse test"
    assert "head samples" in row["details"].value
    viewer._confirm_archive(None)
    assert len(viewer.sessions) == 1
    assert viewer._pending_dataset is None
    assert not viewer.archive_review.children
    payload = playback_payload(viewer.sessions[0])
    assert payload["clock_origin"] == 1000
    assert payload["frames"][0]["t"] == 0
    # First retained head sample was at provider time 2s, not the file's first frame.
    assert payload["frames"][0]["angles"][0] == pytest.approx(np.degrees(0.04))
    link = viewer.manifest_downloads.children[0].value
    encoded = re.search(r"base64,([^\" ]+)", link).group(1)
    manifest = base64.b64decode(encoded).decode("utf-8-sig")
    assert '"P,01"' in manifest
    assert "participant/head.json" in manifest
    restored = ArchiveDataset.read(archive_bytes(manifest, files))
    sessions = restored.build_sessions(restored.assignments)
    assert sessions[0].participant == "P,01"
    assert sessions[0].name == "Mouse test"
    assert playback_payload(sessions[0]) == payload
    assert manifest_csv(restored.assignments) == manifest


def test_review_repair_pair_and_cancel_are_atomic(tmp_path):
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.archive import ArchiveDataset

    dataset = ArchiveDataset.read(archive_bytes(None, {
        "head1.json": make_head(tmp_path).read_bytes(),
        "head2.json": make_head(tmp_path, "later-head.json", offset=5000).read_bytes(),
        "fitts.json": make_fitts(tmp_path).read_bytes(),
    }))
    viewer = NotebookViewer()
    viewer._show_archive_review(dataset)
    first, second = viewer._review_rows
    first["participant"].value = second["participant"].value = "P"
    second["session"].value = "Head only"
    first["head_file"].value = "head2.json"
    viewer._confirm_archive(None)
    assert "do not overlap" in viewer.message.value
    assert not viewer.sessions
    first["head_file"].value = "head1.json"
    second["include"].value = False
    viewer._confirm_archive(None)
    assert len(viewer.sessions) == 1
    viewer._show_archive_review(dataset)
    viewer._cancel_archive(None)
    assert viewer._pending_dataset is None
    assert len(viewer.sessions) == 1


def test_review_splitting_a_pair_can_add_row_and_reject_reuse(tmp_path):
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.archive import ArchiveDataset

    dataset = ArchiveDataset.read(archive_bytes(None, {
        "head.json": make_head(tmp_path).read_bytes(), "fitts.json": make_fitts(tmp_path).read_bytes(),
    }))
    viewer = NotebookViewer()
    viewer._show_archive_review(dataset)
    viewer.archive_review.children[-1].children[1].click()  # Add assignment row.
    assert len(viewer._review_rows) == 2
    first, second = viewer._review_rows
    for index, row in enumerate((first, second)):
        row["participant"].value = "P"
        row["session"].value = f"Test {index}"
    second["head_file"].value = "head.json"
    viewer._confirm_archive(None)
    assert "assigned more than once" in viewer.message.value
    assert not viewer.sessions
    first["head_file"].value = ""
    viewer._confirm_archive(None)
    assert len(viewer.sessions) == 2
    assert not any(s.samples and s.trials for s in viewer.sessions)


def test_zip_upload_enters_review_but_manifest_upload_still_imports_directly(tmp_path):
    from datetime import datetime, timezone
    from colab_session_viewer.app import NotebookViewer

    files = {"fitts.json": make_fitts(tmp_path).read_bytes()}
    viewer = NotebookViewer()
    viewer.import_participant.value = "  Default participant  "
    def upload(content):
        viewer.archive_upload.value = ({
            "name": "data.zip", "type": "application/zip", "size": len(content),
            "content": memoryview(content), "last_modified": datetime.now(timezone.utc),
        },)
        viewer._import_archive(None)
    upload(archive_bytes(None, files))
    assert viewer._pending_dataset is not None
    assert not viewer.sessions
    assert viewer._review_rows[0]["participant"].value == "Default participant"
    viewer.archive_review.children[-1].children[1].click()
    assert viewer._review_rows[1]["participant"].value == "Default participant"
    viewer._review_rows[1]["include"].value = False
    viewer._import_archive(None)
    assert "Import or cancel" in viewer.message.value
    row = viewer._review_rows[0]
    row["participant"].value = "P01"
    row["session"].value = "First"
    viewer._confirm_archive(None)
    upload(archive_bytes("participant,session,head_file,fitts_file\nP02,Second,,fitts.json\n", files))
    assert len(viewer.sessions) == 2
    assert viewer._pending_dataset is None
    assert len(viewer.manifest_downloads.children) == 2
    assert viewer.sessions[-1].participant == "P02"  # Manifest labels override input defaults.


def test_split_tables_keep_one_row_per_participant_and_rom_task_only(tmp_path):
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.data import participant_tables

    recorder = Recorder(tmp_path)
    labelled_angles = [
        ("initial_rest.hold", 5, 2),
        ("initial_rest.hold", 7, 4),
        ("range_of_movement.left.move", 10, 3),
        ("range_of_movement.left.hold", 20, 5),
        ("range_of_movement.right.hold", -15, -2),
        ("fitts_test", 120, 65),  # Must not inflate ROM or initial position.
        (None, -120, -65),
    ]
    recorder.set_calibration([(0, qm.IDENTITY_QUATERNION)], qm.IDENTITY_QUATERNION)
    for index, (phase, yaw, pitch) in enumerate(labelled_angles):
        recorder.record_sample(
            index, qm.from_euler_zyx(*np.radians([yaw, pitch, 0])),
            metadata={"task_phase": phase}, host_time=1000 + index,
        )
    head = Session.load("P01", "Head", head_path=recorder.save("labelled.json"))
    mouse = Session.load("P02", "Mouse", fitts_path=make_fitts(tmp_path))
    rows = participant_rows([head, mouse])
    tables = participant_tables(rows)
    assert list(tables) == [
        "Session summary", "Initial head positions", "Range of movement stats",
        "Head movement stats", "Fitts targets and hits", "Fitts stats",
    ]
    for table in tables.values():
        assert [r["participant"] for r in table] == ["P01", "P02"]
    initial = tables["Initial head positions"][0]
    assert initial["head_initial_rest_yaw_absolute_mean_deg_session_mean"] == pytest.approx(6)
    assert initial["head_initial_rest_yaw_relative_mean_deg_session_mean"] == pytest.approx(6)
    rom = tables["Range of movement stats"][0]
    assert rom["head_range_of_movement_task_yaw_range_deg_session_mean"] == pytest.approx(35)
    assert rom["head_range_of_movement_task_pitch_range_deg_session_mean"] == pytest.approx(7)
    assert rom["head_range_of_movement_directions_left_angle_mean_deg_session_mean"] == pytest.approx(20)
    assert tables["Range of movement stats"][1]["head_range_of_movement_task_yaw_range_deg_session_mean"] is None
    assert not any(key.startswith("head_range_of_movement") for key in tables["Session summary"][0])
    assert tables["Fitts targets and hits"][1]["targets_analysed"] == 5
    assert tables["Fitts stats"][0]["mean_movement_time_s"] is None
    viewer = NotebookViewer()
    viewer._add_sessions([head, mouse])
    markup = viewer.table.value
    assert markup.count("<table ") == 7  # Six summary tables plus expandable file diagnostics.
    assert markup.count("<svg ") == 5  # Four reference views plus the participant pose.
    assert markup.index("<svg ") > markup.index("<h3>Initial head positions</h3>")
    assert markup.index("<svg ") < markup.index("<h3>Range of movement stats</h3>")
    assert "Angle zero references (schematic head mesh)" in markup
    assert "Webcam pitch/roll are camera-referenced" in markup
    for title in tables:
        assert f"<h3>{title}</h3>" in markup
    for filename in ("session_summary.csv", "initial_head_positions.csv",
                     "range_of_movement_stats.csv", "fitts_stats.csv"):
        assert f'download="{filename}"' in markup
    viewer.exclusion_controls.children[1].value = 2
    updated = participant_tables(viewer.summary_rows())
    assert updated["Fitts targets and hits"][1]["targets_analysed"] == 3
    assert updated["Range of movement stats"] == tables["Range of movement stats"]
    pretest_html = playback_payload(head)["pretest_html"]
    assert "px" not in pretest_html
    assert "+6.0" in pretest_html
    assert "+6.00" not in pretest_html


def test_initial_pose_reference_diagram_uses_mesh_and_distinguishes_references():
    import xml.etree.ElementTree as ET
    from colab_session_viewer.data import initial_pose_reference_diagram

    markup = initial_pose_reference_diagram()
    root = ET.fromstring("<section>" + markup.replace("<br>", "<br/>") + "</section>")
    svgs = root.findall(".//{http://www.w3.org/2000/svg}svg")
    assert len(svgs) == 4
    for svg in svgs:
        assert svg.attrib["role"] == "img"
        assert len(svg.findall(".//{http://www.w3.org/2000/svg}line")) == 3
        assert "0.0 deg" in svg.attrib["aria-label"]
    assert "Camera axes follow the camera" in markup
    assert "IMU tilt assumes correct sensor mounting/fit" in markup
    assert "Calibration mean yaw uses this same provider reference" in markup
    assert "Illustrative heading only; not a participant measurement" in markup
    assert "L110.0 20.0" in markup  # Provider yaw zero points forward.
    assert "L155.9 34.5" in markup  # Relative yaw zero follows the illustrated calibration.


def test_rom_table_does_not_fall_back_to_unlabelled_head_ranges(tmp_path):
    from colab_session_viewer.data import participant_tables

    head = Session.load("P", "No ROM", head_path=make_head(tmp_path))
    row = participant_rows([head])[0]
    assert row["head_yaw_range_deg"] > 0
    rom = participant_tables([row])["Range of movement stats"][0]
    assert all(value is None for key, value in rom.items() if key != "participant")


def test_head_only_tables_numeric_precision_and_calibration_pose_wireframes(tmp_path):
    import re
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.data import initial_pose_wireframes, participant_tables

    rows = [{
        "participant": "<P>",
        "head_initial_rest_yaw_absolute_mean_deg_session_mean": 70,
        "head_initial_rest_yaw_relative_mean_deg_session_mean": 39.74,
        "head_initial_rest_yaw_calibration_mean_deg_session_mean": 30.26,
        "head_initial_rest_pitch_mean_deg_session_mean": -10.34,
        "head_initial_rest_roll_mean_deg_session_mean": 5.15,
        "cursor_initial_rest_std_x_px_session_mean": 1234,
        "cursor_range_of_movement_horizontal_range_px_session_mean": 500,
        "head_range_of_movement_yaw_range_deg_session_mean": 23.456,
        "head_speed_deg_s_mean": 4.567, "head_yaw_range_deg": 56.78,
        "targets_analysed": 3, "hits": 2,
    }]
    tables = participant_tables(rows)
    for label in ("Initial head positions", "Range of movement stats"):
        assert not any("cursor" in key or "px" in key for key in tables[label][0])
    assert "head_speed_deg_s_mean" not in tables["Session summary"][0]
    assert tables["Head movement stats"][0]["head_yaw_range_deg"] == 56.78
    assert "hits" not in tables["Fitts stats"][0]
    svg = initial_pose_wireframes(tables["Initial head positions"])
    assert "&lt;P&gt;" in svg
    assert "30.3 / -10.3 / 5.2 deg" in svg
    neutral = [dict(rows[0], head_initial_rest_yaw_calibration_mean_deg_session_mean=0,
                    head_initial_rest_pitch_mean_deg_session_mean=0,
                    head_initial_rest_roll_mean_deg_session_mean=0)]
    assert re.findall(r'<line [^>]+', svg) != re.findall(r'<line [^>]+', initial_pose_wireframes(neutral))
    assert "No initial head pose recorded" in initial_pose_wireframes([{"participant": "No head"}])
    rendered = NotebookViewer._table_html(
        [{"participant": "P01", "sessions": 2, "mean_movement_time_s": 1.2345, "error_rate": None}], "test.csv", "CSV"
    )
    assert "<td>2.0</td>" in rendered
    assert "<td>1.2</td>" in rendered
    assert "<td></td>" in rendered
    assert summary_csv([{"value": 1.2345, "count": 2}]).splitlines()[1] == "1.2,2.0"


def test_initial_heading_calibration_reference_and_missing_calibration(tmp_path):
    from colab_session_viewer.data import head_pretest_summary

    def recording(name, heading, calibrated=True):
        recorder = Recorder(tmp_path)
        if calibrated:
            reference = qm.from_euler_zyx(*np.radians([heading, 10, 5]))
            recorder.set_calibration([(0, reference)], reference)
        for index in range(3):
            recorder.record_sample(index, qm.from_euler_zyx(*np.radians([heading + 7, 20, -4])),
                                   metadata={"task_phase": "initial_rest.hold"}, host_time=1000 + index)
        return Session.load("P", name, head_path=recorder.save(name + ".json"))
    for heading in (0, 175, -120):
        rest = head_pretest_summary(recording(str(heading), heading))["head_initial_rest"]
        assert rest["yaw_mean_deg"] == pytest.approx(7)
        assert rest["yaw_relative_mean_deg"] == pytest.approx(7)
        assert rest["yaw_calibration_mean_deg"] == pytest.approx(heading)
        assert rest["yaw_absolute_mean_deg"] == pytest.approx((heading + 7 + 180) % 360 - 180)
        assert rest["pitch_mean_deg"] == pytest.approx(20)
        assert rest["roll_mean_deg"] == pytest.approx(-4)
    missing = recording("missing", 50, calibrated=False)
    rest = head_pretest_summary(missing)["head_initial_rest"]
    assert rest["yaw_mean_deg"] is None
    assert rest["yaw_absolute_mean_deg"] == pytest.approx(57)
    assert rest["yaw_relative_mean_deg"] is None
    assert rest["pitch_mean_deg"] == pytest.approx(20)
    assert "head_initial_rest_yaw_mean_deg_session_mean" not in participant_rows([missing])[0]


def test_table_order_compact_headers_and_sources(tmp_path):
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.data import participant_tables, table_sources

    first = {"participant": "P", "head_initial_rest_roll_mean_deg_session_mean": 4,
             "head_initial_rest_yaw_absolute_mean_deg_session_mean": 1,
             "head_initial_rest_pitch_mean_deg_session_mean": 2}
    tables = participant_tables([first])
    reverse = participant_tables([{key: first[key] for key in reversed(first)}])
    assert list(tables["Initial head positions"][0]) == list(reverse["Initial head positions"][0])
    assert list(tables["Initial head positions"][0])[1:4] == [
        f"head_initial_rest_yaw_{kind}_mean_deg_session_mean" for kind in ("absolute", "relative", "calibration")]
    assert list(tables["Range of movement stats"][0])[1:4] == [
        f"head_range_of_movement_task_{axis}_range_deg_session_mean" for axis in ("yaw", "pitch", "roll")
    ]
    session = Session.load("P", "pair", head_path=make_head(tmp_path),
                           fitts_path=make_fitts(tmp_path))
    session.head_filename = "study/head.json"
    session.fitts_filename = "study/fitts.json"
    assert table_sources([session], "Fitts stats") == {"P": ["study/fitts.json"]}
    assert table_sources([session], "Head movement stats") == {"P": ["study/head.json"]}
    assert table_sources([session], "Initial head positions") == {"P": []}
    viewer = NotebookViewer()
    viewer._add_sessions([session])
    markup = viewer.table.value
    assert markup.count("<summary><b>Source files</b></summary>") == 6
    assert "table-layout:auto" in markup
    assert "table-layout:fixed" not in markup
    assert "overflow-wrap:anywhere" in markup
    assert "Yaw absolute (deg)" in markup
    assert "gravity does not determine yaw" in markup


def test_collapsed_reference_sources_and_complete_field_keys(tmp_path):
    from html.parser import HTMLParser
    from colab_session_viewer.app import NotebookViewer
    from colab_session_viewer.data import field_description, head_range_diagnostics, participant_tables

    session = Session.load("P", "pair", head_path=make_head(tmp_path), fitts_path=make_fitts(tmp_path))
    viewer = NotebookViewer()
    viewer._add_sessions([session])

    class ReportParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.details = []
            self.headers = []

        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.details.append(dict(attrs))
            elif tag == "th":
                self.headers.append(dict(attrs))

    parser = ReportParser()
    parser.feed(viewer.table.value)
    assert parser.details
    assert all("open" not in attrs for attrs in parser.details)
    assert all("font-weight:bold" in attrs["style"] for attrs in parser.headers)
    assert viewer.table.value.count("<summary><b>Field key</b></summary>") == 7
    for table in participant_tables(viewer.summary_rows()).values():
        for key in table[0]:
            assert field_description(key)
            assert f"<code>{key}</code>" in viewer.table.value
    for key in head_range_diagnostics(session):
        assert field_description(key)
        assert f"<code>{key}</code>" in viewer.table.value
    for text in ("4.133", "log2(Ae / We + 1)", "Target re-entry (TRE)",
                 "Task-axis crossing (TAC)", "Movement-direction change (MDC)",
                 "Orthogonal-direction change (ODC)", "Movement variability (MV)",
                 "Movement error (ME)", "Movement offset (MO)"):
        assert text in viewer.table.value
    assert "calibration-heading-relative when calibration is available" in field_description(
        "head_range_of_movement_directions_left_angle_mean_deg_session_mean",
    )


@pytest.mark.parametrize("zero_axes", [
    (), ("yaw",), ("pitch",), ("roll",), ("yaw", "pitch"),
    ("yaw", "roll"), ("pitch", "roll"), ("yaw", "pitch", "roll"),
])
def test_pose_zero_axes_redraw_without_mutating_measurements(zero_axes):
    import copy
    import re
    from colab_session_viewer.data import initial_pose_wireframes

    rows = [{
        "participant": "<P>",
        "head_initial_rest_yaw_calibration_mean_deg_session_mean": 30,
        "head_initial_rest_pitch_mean_deg_session_mean": 20,
        "head_initial_rest_roll_mean_deg_session_mean": -10,
    }]
    before = copy.deepcopy(rows)
    markup = initial_pose_wireframes(rows, zero_axes=zero_axes)
    expected = [0 if axis in zero_axes else value
                for axis, value in zip(("yaw", "pitch", "roll"), (30, 20, -10))]
    assert " / ".join(f"{v:.1f}" for v in expected) + " deg" in markup
    assert rows == before
    for axis in ("X", "Y", "Z"):
        assert f">+{axis}</text>" in markup
        assert f">-{axis}</text>" in markup
    assert "Yaw: + left / - right" in markup
    assert "Pitch: + up / - down" in markup
    assert "Roll: + left ear down / - right ear down" in markup
    if zero_axes:
        original = initial_pose_wireframes(rows)
        assert re.findall(r"<line [^>]+", markup) != re.findall(r"<line [^>]+", original)


def test_pose_controls_redraw_restore_and_preserve_exports_and_playback(tmp_path):
    from colab_session_viewer.app import NotebookViewer

    recorder = Recorder(tmp_path)
    q = qm.from_euler_zyx(*np.radians([30, 20, -10]))
    recorder.set_calibration([(0, q)], q)
    for i in range(2):
        recorder.record_sample(i, q, metadata={"task_phase": "initial_rest.hold"}, host_time=1000 + i)
    session = Session.load("P", "Head", head_path=recorder.save("pose.json"))
    viewer = NotebookViewer()
    viewer._add_sessions([session])
    before_markup = viewer.table.value
    before_rows = viewer.summary_rows()
    before_csv = summary_csv(before_rows)
    before_playback = playback_payload(session)
    for control in viewer.pose_zero_controls.values():
        assert not control.value
        control.value = True
    assert "0.0 / 0.0 / 0.0 deg" in viewer.table.value
    assert viewer.table.value != before_markup
    assert viewer.summary_rows() == before_rows
    assert summary_csv(viewer.summary_rows()) == before_csv
    assert playback_payload(session) == before_playback
    for control in viewer.pose_zero_controls.values():
        control.value = False
    assert viewer.table.value == before_markup


def test_zero_missing_yaw_is_explicit_but_does_not_invent_initial_pose():
    from colab_session_viewer.data import initial_pose_wireframes

    row = {"participant": "P",
           "head_initial_rest_pitch_mean_deg_session_mean": 20,
           "head_initial_rest_roll_mean_deg_session_mean": -10}
    assert "No initial head pose recorded" in initial_pose_wireframes([row])
    assert "0.0 / 20.0 / -10.0 deg" in initial_pose_wireframes([row], zero_axes=("yaw",))
    assert "No initial head pose recorded" in initial_pose_wireframes(
        [{"participant": "No head"}], zero_axes=("yaw", "pitch", "roll"),
    )
    with pytest.raises(ValueError, match="Plot zero axes"):
        initial_pose_wireframes([row], zero_axes=("invalid",))


def test_ranges_use_quaternions_and_do_not_pool_different_euler_wrap_sheets(tmp_path):
    from colab_session_viewer.data import head_range_diagnostics

    sessions = []
    for name, degrees in (("positive", [170, 179, -175]), ("negative", [-175, -179, 170])):
        recorder = Recorder(tmp_path)
        recorder.set_calibration([(0, qm.IDENTITY_QUATERNION)], qm.IDENTITY_QUATERNION)
        for i, yaw in enumerate(degrees):
            recorder.record_sample(i, qm.from_euler_zyx(np.radians(yaw), 0, 0),
                                   metadata={"task_phase": "fitts_test"}, host_time=1000 + i)
        session = Session.load("P", name, head_path=recorder.save(name + ".json"))
        # Stored Euler columns deliberately wrong: range must use the quaternion.
        for sample in session.samples:
            sample.yaw_deg = 0
        sessions.append(session)
        diag = head_range_diagnostics(session)
        assert diag["yaw_range_deg"] == pytest.approx(15)
        assert diag["fitts_yaw_range_deg"] == pytest.approx(15)
        assert diag["near_gimbal_samples"] == 0
    row = participant_rows(sessions)[0]
    assert row["head_yaw_range_deg"] == pytest.approx(15)  # Not ~370 from pooling wrap sheets.


def test_range_diagnostics_distinguish_setup_fitts_and_euler_singularity(tmp_path):
    from colab_session_viewer.data import head_range_diagnostics

    recorder = Recorder(tmp_path)
    recorder.set_calibration([(0, qm.IDENTITY_QUATERNION)], qm.IDENTITY_QUATERNION)
    for i, (yaw, pitch, phase) in enumerate([
        (5, 0, "fitts_test"), (10, 0, "fitts_test"),
        (80, 89, "range_of_movement.up.hold"), (60, 0, "initial_rest.move"),
    ]):
        recorder.record_sample(i, qm.from_euler_zyx(*np.radians([yaw, pitch, 0])),
                               metadata={"task_phase": phase}, host_time=1000 + i)
    session = Session.load("P", "test", head_path=recorder.save("head.json"))
    diag = head_range_diagnostics(session)
    assert diag["yaw_range_deg"] == pytest.approx(75)
    assert diag["fitts_yaw_range_deg"] == pytest.approx(5)
    assert diag["near_gimbal_samples"] == 1
    assert diag["max_abs_relative_pitch_deg"] == pytest.approx(89)


def test_plot_yaw_uses_calibration_samples_mean_not_initial_rest(tmp_path):
    from colab_session_viewer.data import participant_tables, initial_pose_wireframes

    recorder = Recorder(tmp_path)
    q = qm.from_euler_zyx(*np.radians([30, 10, 5]))
    recorder.set_calibration([(0, q), (1, q)], q)
    for i in range(2):
        recorder.record_sample(i, qm.from_euler_zyx(*np.radians([45, 20, 6])),
                               metadata={"task_phase": "initial_rest.hold"}, host_time=1000 + i)
    session = Session.load("P", "test", head_path=recorder.save("head.json"))
    row = participant_tables(participant_rows([session]))["Initial head positions"][0]
    assert row["head_initial_rest_yaw_absolute_mean_deg_session_mean"] == pytest.approx(45)
    assert row["head_initial_rest_yaw_relative_mean_deg_session_mean"] == pytest.approx(15)
    assert row["head_initial_rest_yaw_calibration_mean_deg_session_mean"] == pytest.approx(30)
    assert "30.0 / 20.0 / 6.0 deg" in initial_pose_wireframes([row])


def test_tilted_neutral_couples_provider_heading_into_relative_roll_without_gimbal(tmp_path):
    from colab_session_viewer.data import head_range_diagnostics

    recorder = Recorder(tmp_path)
    neutral = qm.from_euler_zyx(*np.radians([0, 26, -35]))
    recorder.set_calibration([(0, neutral)], neutral)
    for i, yaw in enumerate(np.linspace(0, 49, 100)):
        recorder.record_sample(i, qm.from_euler_zyx(*np.radians([yaw, 26, -35])),
                               host_time=1000 + i)
    session = Session.load("P", "tilted", head_path=recorder.save("head.json"))
    diag = head_range_diagnostics(session)
    assert diag["near_gimbal_samples"] == 0
    assert diag["max_abs_relative_pitch_deg"] < 20
    assert diag["roll_range_deg"] > 25
    assert np.ptp([s.roll_deg for s in session.samples]) == pytest.approx(0, abs=1e-10)


def test_any_root_csv_manifest_imports_only_listed_recordings(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset
    from colab_session_viewer.app import NotebookViewer

    manifest = "participant,session,head_file,fitts_file\nA,Selected,,recordings/test.json\n"
    content = archive_bytes(None, {
        "A manifest.csv": manifest,
        "recordings/test.json": make_fitts(tmp_path).read_bytes(),
        "unlisted.json": "{invalid and must not be loaded",
        "measurements.csv": "time,value\n1,2\n",
        "unrelated.csv": b"\xff\xfe",
    })
    dataset = ArchiveDataset.read(content)
    assert dataset.has_manifest
    assert dataset.manifest_filename == "A manifest.csv"
    assert any("unrelated.csv" in warning for warning in dataset.warnings)
    assert list(dataset.recordings) == ["recordings/test.json"]
    sessions = dataset.build_sessions(dataset.assignments)
    assert sessions[0].participant == "A"
    viewer = NotebookViewer()
    viewer._add_sessions(sessions)
    assert viewer.table.value.count("<h3>") == 6
    for label in ("Session summary", "Initial head positions", "Range of movement stats",
                  "Head movement stats", "Fitts targets and hits", "Fitts stats"):
        assert f"<h3>{label}</h3>" in viewer.table.value


def test_root_csv_without_manifest_columns_does_not_override_discovery(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    dataset = ArchiveDataset.read(archive_bytes(None, {
        "A manifest.csv": "participant,unrelated\nA,example\n",
        "head.csv": make_head(tmp_path, "head.csv").read_bytes(),
        "nested/manifest.csv": "participant,session,head_file,fitts_file\nP,Test,,missing.json\n",
    }))
    assert not dataset.has_manifest
    assert list(dataset.recordings) == ["head.csv"]
    assert dataset.warnings


def test_invalid_or_multiple_root_manifests_do_not_import_unlisted_files(tmp_path):
    from colab_session_viewer.archive import ArchiveDataset

    with pytest.raises(ValueError, match="Invalid manifest"):
        ArchiveDataset.read(archive_bytes(None, {
            "A manifest.csv": "participant,session,head_file,fitts_file\nA,Test,,\n",
            "test.json": make_fitts(tmp_path).read_bytes(),
        }))
    with pytest.raises(ValueError, match="Multiple valid root manifests"):
        ArchiveDataset.read(archive_bytes(None, {
            "A manifest.csv": "participant,session,head_file,fitts_file\nA,Test,,test.json\n",
            "B manifest.CSV": "participant,session,head_file,fitts_file\nB,Test,,test.json\n",
            "test.json": make_fitts(tmp_path).read_bytes(),
        }))


@pytest.mark.parametrize("in_notebook_folder", [False, True])
def test_notebook_prefers_containing_project_over_nested_clone(tmp_path, monkeypatch, in_notebook_folder):
    notebook = json.loads((ROOT / "colab_session_viewer" / "session_viewer.ipynb").read_text(encoding="utf-8"))
    setup = "".join(next(c["source"] for c in notebook["cells"] if c["id"] == "setup"))
    local = tmp_path / "project"
    folder = local / "colab_session_viewer"
    folder.mkdir(parents=True)
    (folder / "app.py").touch()
    (local / "core").mkdir()
    (folder / "OpenHeadMouse").mkdir()
    monkeypatch.chdir(folder if in_notebook_folder else local)
    expression = setup[setup.index("PROJECT_DIR = next"):setup.index("\n\nif not PROJECT_DIR.exists()")]
    namespace = {"Path": Path}
    exec(expression, namespace)
    assert namespace["PROJECT_DIR"] == local
