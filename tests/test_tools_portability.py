from pathlib import Path
import subprocess
import sys

import pytest

from fitts_law import fitts_settings
from tools.check_publication import _decode_git_output


@pytest.mark.parametrize(
    ("platform", "environment", "expected"),
    [
        ("win32", {"APPDATA": "C:/Users/test/AppData/Roaming"},
         Path("C:/Users/test/AppData/Roaming/OpenHeadMouse/fitts_settings.json")),
        ("darwin", {}, Path("/home/test/Library/Application Support/OpenHeadMouse/fitts_settings.json")),
        ("linux", {"XDG_CONFIG_HOME": "/home/test/.config"},
         Path("/home/test/.config/OpenHeadMouse/fitts_settings.json")),
    ],
)
def test_frozen_fitts_settings_use_platform_user_config(
    monkeypatch, platform, environment, expected
):
    monkeypatch.setattr(fitts_settings.sys, "platform", platform)
    monkeypatch.setattr(fitts_settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/test"))
    for key in ("APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    assert fitts_settings._settings_path() == expected


def test_linux_deb_declares_runtime_libraries_for_accessibility_and_qt():
    workflow = Path(".github/workflows/build_release.yml").read_text(encoding="utf-8")

    assert "--depends libatspi2.0-0" in workflow
    assert 'if [[ "$EDITION" == "full" || "$EDITION" == "dwell-click" ]]' in workflow
    for dependency in (
        "libegl1",
        "libgl1",
        "libxcb-cursor0",
        "libxcb-icccm4",
        "libxcb-keysyms1",
        "libxcb-shape0",
        "libxkbcommon-x11-0",
    ):
        assert f"--depends {dependency}" in workflow


def test_git_path_output_decodes_utf8_independently_of_windows_locale():
    path = "sessions/participant-é.json"

    assert _decode_git_output(path.encode("utf-8") + b"\0") == path + "\0"


@pytest.mark.parametrize("launcher", ["session", "fitts"])
def test_standalone_launchers_share_opengl_contexts_before_app_creation(launcher):
    script = """
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
assert QApplication.instance() is None
if sys.argv[1] == "session":
    from session_viewer.__main__ import main
    assert main(["--smoke-test"]) == 0
else:
    from fitts_law.fitts_test import launch_fitts_test
    window = launch_fitts_test()
    window.close()
assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PySide6.QtGui import QOpenGLContext
assert QOpenGLContext.globalShareContext() is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script, launcher],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
