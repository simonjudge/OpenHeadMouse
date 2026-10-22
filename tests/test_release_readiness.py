from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

from core.model_assets import download_verified
from tools.check_publication import findings


def test_verified_download_checks_existing_asset_without_network(tmp_path, monkeypatch):
    path = tmp_path / "mesh.obj"
    path.write_bytes(b"mesh")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network"))
    assert download_verified("https://example.invalid", path, hashlib.sha256(b"mesh").hexdigest()) == path
    with pytest.raises(ValueError, match="Checksum mismatch"):
        download_verified("https://example.invalid", path, "0" * 64)


def test_failed_download_does_not_leave_an_asset(tmp_path, monkeypatch):
    path = tmp_path / "models" / "mesh.obj"
    calls = []

    def response(url, **kwargs):
        calls.append(kwargs)
        return io.BytesIO(b"wrong")

    monkeypatch.setattr("urllib.request.urlopen", response)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        download_verified("https://example.invalid", path, "0" * 64)
    assert not path.exists()
    assert calls == [{"timeout": 60}]


def test_verified_download_saves_expected_bytes(tmp_path, monkeypatch):
    path = tmp_path / "models" / "mesh.obj"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"mesh"))
    download_verified("https://example.invalid", path, hashlib.sha256(b"mesh").hexdigest())
    assert path.read_bytes() == b"mesh"


def test_publication_findings_are_redacted():
    secret = "ghp_" + "A" * 36
    result = findings(secret.encode(), "settings.txt")
    assert result == ["GitHub token"]
    assert secret not in repr(result)
    notebook = {"cells": [{"execution_count": 1, "outputs": [{"text": ["private"]}]}]}
    assert findings(json.dumps(notebook).encode(), "viewer.ipynb") == ["saved notebook output/execution"]
    assert findings(b"\0binary", "asset") == []


def test_notebook_is_clean_and_publication_defaults_are_safe():
    root = Path(__file__).resolve().parents[1]
    for name in ("config.json", "colab_session_viewer/session_viewer.ipynb"):
        assert findings((root / name).read_bytes(), name) == []


def test_frozen_diagnostics_capture_errors_and_rotate(qt_app, tmp_path, monkeypatch):
    from core import diagnostics

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(diagnostics, "log_directory", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    (tmp_path / "SessionViewer.log").write_text("previous run", encoding="utf-8")
    messages = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.critical",
        lambda *args: messages.append(args),
    )
    path = diagnostics.configure_frozen_diagnostics("SessionViewer")
    try:
        print("diagnostic message")
        error = ValueError("test error")
        sys.excepthook(type(error), error, None)
        assert "diagnostic message" in path.read_text(encoding="utf-8")
        assert "ValueError: test error" in path.read_text(encoding="utf-8")
        assert str(path) in messages[0][2]
        assert path.with_suffix(".previous.log").read_text() == "previous run"
    finally:
        sys.stdout.close()


def test_source_diagnostics_preserve_console(monkeypatch):
    from core.diagnostics import configure_frozen_diagnostics

    monkeypatch.delattr(sys, "frozen", raising=False)
    stream = sys.stdout
    assert configure_frozen_diagnostics("OpenHeadMouse") is None
    assert sys.stdout is stream


def test_camera_runtime_dependencies_import():
    import cv2
    from mediapipe.tasks.python.vision import FaceLandmarker

    assert cv2.__version__
    assert callable(cv2.VideoCapture)
    assert callable(FaceLandmarker.create_from_options)


def test_packaged_smoke_checks_all_editions_with_existing_config(tmp_path, monkeypatch):
    from tools import smoke_release

    for name in (
        "OpenHeadMouse-windows-amd64.exe",
        "OpenHeadMouse-headmouse-only-windows-amd64.exe",
        "DwellClick-windows-amd64.exe",
        "SessionViewer-windows-amd64.exe",
        "OpenHeadMouse-windows-amd64-setup.exe",
    ):
        (tmp_path / name).touch()
    monkeypatch.setattr(sys, "argv", ["smoke_release.py", str(tmp_path)])
    calls = []

    def run(command, *, env, check, timeout):
        assert "--smoke-test" in command
        assert env["QT_QPA_PLATFORM"] == "offscreen"
        assert check is True and timeout == 60
        if Path(command[0]).name.startswith("OpenHeadMouse-"):
            config = Path(command[command.index("--config") + 1])
            assert json.loads(config.read_text())["recorder"]["base_dir"] is None
        calls.append(command)

    monkeypatch.setattr(smoke_release.subprocess, "run", run)
    smoke_release.main()
    assert len(calls) == 4
    assert not any(command[0].endswith("-setup.exe") for command in calls)


def test_packaged_smoke_refuses_incomplete_editions(tmp_path, monkeypatch):
    from tools import smoke_release

    monkeypatch.setattr(sys, "argv", ["smoke_release.py", str(tmp_path)])
    with pytest.raises(RuntimeError, match="Expected four"):
        smoke_release.main()


def test_notice_export_replaces_stale_generated_licenses(tmp_path, monkeypatch):
    from tools import prepare_release

    monkeypatch.setattr(prepare_release, "distributions", lambda: [])
    monkeypatch.setattr(prepare_release.sys, "base_prefix", str(tmp_path / "python"))
    destination = tmp_path / "notices"
    prepare_release.export_notices(destination)
    stale = destination / "licenses" / "old-version" / "LICENSE"
    stale.parent.mkdir(parents=True)
    stale.write_text("old licence", encoding="utf-8")
    stale_python = destination / "PYTHON-LICENSE.txt"
    stale_python.write_text("old Python licence", encoding="utf-8")
    prepare_release.export_notices(destination)
    assert not stale.exists()
    assert not stale_python.exists()
    manifest = json.loads((destination / "build-environment.json").read_text())
    assert manifest["packages"] == []
    assert manifest["generated_by"] == "OpenHeadMouse prepare_release"


def test_notice_export_does_not_erase_unmanaged_files(tmp_path, monkeypatch):
    from tools import prepare_release

    destination = tmp_path / "unmanaged"
    destination.mkdir()
    existing = destination / "my-document"
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="unmanaged"):
        prepare_release.export_notices(destination)
    assert existing.read_text() == "keep"
