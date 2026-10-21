"""Standalone viewer launch and packaging contract."""

from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from session_viewer.__main__ import main
from session_viewer import session_viewer
from session_viewer.mesh_visualizer import download_mediapipe_model, load_obj_wireframe


ROOT = Path(__file__).resolve().parents[1]


def test_module_help_does_not_start_a_gui():
    result = subprocess.run(
        [sys.executable, "-m", "session_viewer", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "head-movement recordings" in result.stdout
    assert "Fitts' Law playback" in result.stdout


def test_launcher_opens_empty_viewer_and_runs_event_loop(qt_app, monkeypatch):
    windows = []
    window_type = session_viewer.SessionVisualizerWindow

    def create_window():
        window = window_type()
        windows.append(window)
        return window

    monkeypatch.setattr(session_viewer, "SessionVisualizerWindow", create_window)
    monkeypatch.setattr(QApplication, "exec", lambda self: 23)
    try:
        assert main([]) == 23
        assert len(windows) == 1
        assert windows[0].isVisible()
        assert windows[0].recorded_samples == []
    finally:
        for window in windows:
            window.close()


def test_viewer_spec_bundles_mesh_and_uses_windowed_module_entry():
    captured = {}

    def analysis(scripts, **kwargs):
        captured["scripts"] = scripts
        captured["analysis"] = kwargs
        return SimpleNamespace(scripts=scripts, pure=[], binaries=[], datas=kwargs["datas"])

    def exe(*args, **kwargs):
        captured["exe"] = kwargs

    runpy.run_path(
        str(ROOT / "SessionViewer.spec"),
        init_globals={
            "SPECPATH": str(ROOT),
            "Analysis": analysis,
            "PYZ": lambda pure: pure,
            "EXE": exe,
        },
    )
    assert captured["scripts"] == [str(ROOT / "session_viewer" / "__main__.py")]
    assert captured["exe"]["name"] == "SessionViewer"
    assert captured["exe"]["console"] is False
    assert captured["analysis"]["datas"] == [
        (str(ROOT / "models" / "canonical_face_model.obj"), "models")
    ]


def test_bundled_mesh_is_loaded_without_network_or_writes(tmp_path, monkeypatch):
    import urllib.request

    mesh = tmp_path / "models" / "canonical_face_model.obj"
    mesh.parent.mkdir()
    mesh.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    def no_download(*args, **kwargs):
        raise AssertionError("A bundled mesh must not be downloaded")

    monkeypatch.setattr(urllib.request, "urlopen", no_download)
    assert download_mediapipe_model(mesh) == mesh
    vertices, faces, edges = load_obj_wireframe(mesh)
    assert vertices.shape == (3, 3)
    assert faces.shape == (1, 3)
    assert edges.shape == (3, 2)
