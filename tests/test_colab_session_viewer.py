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
    assert row["cursor_range_of_movement_directions_left_extreme_px_session_mean"] == 140


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
    assert viewer.widget.children[2] is viewer.viewer
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
            assert cell["outputs"] == []
            compile("".join(cell["source"]), cell["id"], "exec")
    result = subprocess.run([
        sys.executable, "-c",
        "import sys; import colab_session_viewer.app; "
        "assert not any(n.startswith(('PySide6', 'pynput', 'cv2')) for n in sys.modules)",
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
