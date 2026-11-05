import hashlib
import io
import sys
import zipfile

import pytest

from providers import sony_bridge


@pytest.fixture
def native_asset(tmp_path, monkeypatch):
    content = {"sony-head-tracker.exe": b"executable", "LICENSE": b"MIT licence"}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        for name, data in content.items():
            output.writestr(name, data)
    asset = {
        "archive": "bridge.zip", "prefix": "", "executable": "sony-head-tracker.exe",
        "sha256": hashlib.sha256(archive.getvalue()).hexdigest(),
        "executable_sha256": hashlib.sha256(content["sony-head-tracker.exe"]).hexdigest(),
        "license_sha256": hashlib.sha256(content["LICENSE"]).hexdigest(),
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sony_bridge.platform, "machine", lambda: "AMD64")
    monkeypatch.setitem(sony_bridge.ASSETS, "win32", asset)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *args, **kwargs: io.BytesIO(archive.getvalue()))
    return tmp_path, content


def test_dependency_install_and_offline_verification(native_asset, monkeypatch):
    root, content = native_asset
    executable = sony_bridge.ensure_bridge()
    assert executable == root / sony_bridge.RELATIVE_DIRECTORY / "sony-head-tracker.exe"
    assert executable.read_bytes() == content[executable.name]
    assert (executable.parent / "LICENSE").read_bytes() == content["LICENSE"]
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network"))
    assert sony_bridge.ensure_bridge() == executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sony_bridge.ensure_bridge() == executable


def test_frozen_dependency_missing_fails_without_download(native_asset, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(RuntimeError, match="packaged.*missing"):
        sony_bridge.ensure_bridge()


def test_frozen_dependency_extracts_from_bundled_archive_offline(native_asset, monkeypatch):
    executable = sony_bridge.ensure_bridge()
    executable.unlink()
    (executable.parent / "LICENSE").unlink()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network"))
    assert sony_bridge.ensure_bridge().read_bytes() == b"executable"
    assert (executable.parent / "LICENSE").read_bytes() == b"MIT licence"


def test_dependency_tampering_is_not_executed(native_asset):
    executable = sony_bridge.ensure_bridge()
    executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        sony_bridge.ensure_bridge()


def test_bad_archive_is_not_installed(native_asset, monkeypatch):
    root, _ = native_asset
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"bad zip"))
    with pytest.raises(ValueError, match="Checksum mismatch"):
        sony_bridge.ensure_bridge()
    assert not (root / sony_bridge.RELATIVE_DIRECTORY).exists()


def test_unsupported_platform_is_explicit(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Windows 11"):
        sony_bridge.ensure_bridge()
