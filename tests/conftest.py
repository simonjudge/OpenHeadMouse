import os

# Must be set before PySide6/Qt is imported anywhere, so GUI tests can build
# real widgets without a real display (no windowing system in this sandbox/CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def headless_native_geometry(monkeypatch):
    """Offscreen Qt windows do not have Windows monitor handles."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        monkeypatch.setattr("main._windows_monitor_geometry", lambda window_id=None: None)


@pytest.fixture(autouse=True)
def unexpected_critical_dialog(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def fail(parent, title, message, *args, **kwargs):
        pytest.fail(f"Unexpected critical dialog: {title}: {message}")

    monkeypatch.setattr(QMessageBox, "critical", fail)


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_fitts_settings(tmp_path, monkeypatch):
    """Keep the Fitts settings a test saves out of the repository's own file."""
    from fitts_law import fitts_settings

    monkeypatch.setattr(
        fitts_settings, "_SETTINGS_PATH", tmp_path / "fitts_settings" / "fitts_settings.json"
    )


@pytest.fixture(autouse=True)
def isolated_recordings_dir(tmp_path, monkeypatch):
    """Never read (or record into) the developer's real recordings folder.

    Yields the un-stubbed folder chooser, for the tests that cover it.
    """
    from types import SimpleNamespace

    from core import recording_paths

    monkeypatch.setattr(
        recording_paths,
        "default_config_path",
        lambda: tmp_path / "recordings_config" / "config.json",
    )
    # A real folder chooser is modal and would hang the suite, so it is
    # stubbed to pick a temporary folder unless a test replaces it.
    real_prompt = recording_paths.prompt_for_base_dir
    monkeypatch.setattr(
        recording_paths,
        "prompt_for_base_dir",
        lambda parent=None, reason="", **_: tmp_path / "recordings",
    )
    return SimpleNamespace(real_prompt=real_prompt)
