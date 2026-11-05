"""Pinned native Sony Head Tracker dependency for source and frozen builds."""

from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import sys
import zipfile

from core.model_assets import download_verified

VERSION = "2.2.0"
RELATIVE_DIRECTORY = Path("native") / "sony-head-tracker" / VERSION
RELATIVE_ARCHIVES = Path("native") / "sony-head-tracker" / "archives"
ASSETS = {
    "win32": {
        "archive": "sony-head-tracker-v2.2.0-windows-x64.zip",
        "sha256": "ff75f6b2bae17535c6ac8a2860129ee2b27e710972423efd64655f9d2488598e",
        "prefix": "",
        "executable": "sony-head-tracker.exe",
        "executable_sha256": "1a6c308e2c02f1039d837311eba81d1f562d0b60ec66e6f71e1b7933f2e46a55",
        "license_sha256": "4366cd852179899052185da5b9e08e80f4972181f9d33bd50aa0d036de005ef3",
    },
    "darwin": {
        "archive": "sony-head-tracker-v2.2.0-macos-universal.zip",
        "sha256": "9a3d0418a9bda4073a1312ce0622264b6cef7989cd0050a67361e44634eb2d2e",
        "prefix": "sony-head-tracker-v2.2.0-macos-universal/",
        "executable": "sony-head-tracker-macos",
        "executable_sha256": "396897fc98415992c816952fa47ad59b2074a7b69b87ff1991083b94cd9faf93",
        "license_sha256": "bff1f53da879259fb5c17daef61e09145851f061ec39ebc2077b080eb7eab5a7",
    },
}


def ensure_bridge() -> Path:
    """Verify/install the native dependency; frozen apps must already include it."""
    if sys.platform not in ASSETS:
        raise RuntimeError("Sony head tracking requires Windows 11 x64 or macOS 14+.")
    if sys.platform == "win32" and platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("The bundled Sony Head Tracker requires Windows x64.")
    asset = ASSETS[sys.platform]
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    directory = root / RELATIVE_DIRECTORY
    executable = directory / asset["executable"]
    license_path = directory / "LICENSE"
    archive_path = root / RELATIVE_ARCHIVES / asset["archive"]
    if getattr(sys, "frozen", False) and not archive_path.is_file():
        raise RuntimeError(
            "The packaged Sony Head Tracker dependency is missing. "
            "Reinstall OpenHeadMouse or rebuild it with tools/prepare_release.py --assets."
        )
    url = (
        "https://github.com/NicholasSlattery/sony-head-tracker/releases/download/"
        f"v{VERSION}/{asset['archive']}"
    )
    # Bundle the archive so PyInstaller cannot thin/re-sign Mach-O or UPX-pack PE bytes.
    archive = download_verified(url, archive_path, asset["sha256"])
    if not executable.is_file() or not license_path.is_file():
        with zipfile.ZipFile(archive) as source:
            files = {
                asset["executable"]: source.read(asset["prefix"] + asset["executable"]),
                "LICENSE": source.read(asset["prefix"] + "LICENSE"),
            }
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (directory / name).write_bytes(data)
    for path, expected in (
        (executable, asset["executable_sha256"]), (license_path, asset["license_sha256"]),
    ):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for Sony dependency {path}; reinstall it.")
    if sys.platform == "darwin":
        executable.chmod(executable.stat().st_mode | 0o111)
    return executable
