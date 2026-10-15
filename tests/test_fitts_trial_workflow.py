"""Participant-facing task selection, completion and save recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fitts_law.fitts_test import (
    FittsTestWindow,
    START_DWELL,
    START_HEADMOUSE,
    START_NORMAL,
    TASK_COMBINED,
    TASK_HEAD_ONLY,
    TASK_MOUSE_ONLY,
)


def _finish_head_tasks(window):
    overlay = window._pretest_overlay
    overlay.stop()
    while window._pretest_overlay is not None:
        overlay._phase_start -= overlay.current_stage["duration_s"]
        overlay._tick()


def test_setup_has_requested_dropdown_options(qt_app):
    window = FittsTestWindow()
    try:
        assert [window._task_combo.itemText(i) for i in range(3)] == [
            TASK_COMBINED, TASK_HEAD_ONLY, TASK_MOUSE_ONLY,
        ]
        assert [window._start_mode_combo.itemText(i) for i in range(3)] == [
            START_NORMAL, START_DWELL, START_HEADMOUSE,
        ]
        assert not hasattr(window, "_initial_rest_checkbox")
        assert not hasattr(window, "_range_of_movement_checkbox")
        assert not hasattr(window, "_start_headmouse_button")
    finally:
        window.close()


def test_window_closes_before_first_save_and_every_retry(qt_app, tmp_path, monkeypatch):
    window = FittsTestWindow()
    window.show()
    original_save = window._logger.save
    attempts = []
    warnings = []

    def save(path=None):
        assert not window.isVisible()
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("Folder is read-only")
        return original_save(path)

    monkeypatch.setattr(window._logger, "save", save)
    monkeypatch.setattr(
        "fitts_law.fitts_test.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )
    folders = iter([str(tmp_path / "first"), str(tmp_path / "second")])
    monkeypatch.setattr(
        "fitts_law.fitts_test.QFileDialog.getExistingDirectory",
        lambda *args: next(folders),
    )

    window._on_all_trials_completed()

    assert len(attempts) == 3
    assert len(warnings) == 2
    assert window._saved_path.parent == tmp_path / "second"
    assert window._saved_path.exists()
    assert not window.isVisible()


def test_cancelled_save_recovery_keeps_results_available(qt_app, monkeypatch):
    window = FittsTestWindow()
    warnings = []

    def fail(path=None):
        raise OSError("Disk full")

    monkeypatch.setattr(window._logger, "save", fail)
    monkeypatch.setattr(
        "fitts_law.fitts_test.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )
    monkeypatch.setattr(
        "fitts_law.fitts_test.QFileDialog.getExistingDirectory", lambda *args: "",
    )
    try:
        window._logger.metadata["notes"] = "Retain this data"
        window._on_all_trials_completed()
        assert window._saved_path is None
        assert window.isVisible()
        assert window._stack.currentWidget() is window._results_page
        assert window._logger.metadata["notes"] == "Retain this data"
        assert len(warnings) == 2
    finally:
        window.close()


@pytest.mark.parametrize("selection", [TASK_COMBINED, TASK_HEAD_ONLY])
def test_head_tasks_always_include_initial_pose_and_range(
    qt_app, tmp_path, selection,
):
    window = FittsTestWindow()
    try:
        window.set_recordings_dir_provider(lambda prompt: tmp_path)
        window._task_combo.setCurrentText(selection)
        if selection == TASK_HEAD_ONLY:
            window._amplitudes_edit.setText("invalid mouse settings")
            window._stimulus_combo.setCurrentText("Custom image...")
        window._on_start_clicked()
        assert window._pretest_overlay is not None
        assert len(window._pretest_overlay._stages) == 10
        assert window._logger.metadata["record_head_movement"] is True
        _finish_head_tasks(window)

        if selection == TASK_HEAD_ONLY:
            assert window._canvas is None
            assert not window.isVisible()
            data = json.loads(window._saved_path.read_text())
            assert data["trial_count"] == 0
            assert len(data["metadata"]["pretest_phases"]) == 10
            assert data["metadata"]["fitts_task"]["start_host_time"] is None
        else:
            assert window._canvas is not None
            assert window._logger.metadata["fitts_task"]["start_host_time"] is not None
    finally:
        window.close()


@pytest.mark.parametrize("stop", ["complete", "cancel", "close"])
def test_dwell_mode_only_runs_during_mouse_task(qt_app, tmp_path, stop):
    window = FittsTestWindow()
    calls = []
    try:
        window.set_dwell_provider(calls.append)
        window.set_recordings_dir_provider(lambda prompt: tmp_path)
        window._start_mode_combo.setCurrentText(START_DWELL)
        window._task_combo.setCurrentText(TASK_COMBINED)
        window._on_start_clicked()
        assert calls == []
        _finish_head_tasks(window)
        assert calls == [True]
        if stop == "complete":
            window._on_all_trials_completed()
        elif stop == "cancel":
            window._on_back_to_setup_clicked()
        else:
            window.close()
        assert calls == [True, False]
    finally:
        window.close()


def test_standalone_dwell_mode_uses_existing_service(qt_app, monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, parent):
            pass

        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    monkeypatch.setattr("dwell_click.service.DwellClickService", FakeService)
    window = FittsTestWindow()
    try:
        window._start_mode_combo.setCurrentText(START_DWELL)
        window._on_start_clicked()
        assert calls == ["start"]
    finally:
        window.close()
    assert calls == ["start", "stop"]


@pytest.mark.parametrize("start_mode", [START_NORMAL, START_HEADMOUSE])
def test_other_start_modes_leave_host_dwell_settings_untouched(qt_app, start_mode):
    window = FittsTestWindow()
    calls = []
    try:
        window.set_dwell_provider(calls.append)
        window.set_head_start_provider(
            lambda ready, mouse_control: ready(True) or True
        )
        window._start_mode_combo.setCurrentText(start_mode)
        window._on_start_clicked()
        assert window._canvas is not None
        assert calls == []
    finally:
        window.close()
    assert calls == []


def test_new_dropdown_settings_persist(qt_app):
    window = FittsTestWindow()
    window._task_combo.setCurrentText(TASK_HEAD_ONLY)
    window._start_mode_combo.setCurrentText(START_DWELL)
    window.close()
    reopened = FittsTestWindow()
    try:
        assert reopened._task_combo.currentText() == TASK_HEAD_ONLY
        assert reopened._start_mode_combo.currentText() == START_DWELL
    finally:
        reopened.close()


@pytest.mark.parametrize("mode", [START_NORMAL, START_DWELL, START_HEADMOUSE])
@pytest.mark.parametrize("stop", ["complete", "cancel", "close"])
def test_host_input_is_gated_and_stopped_with_saved_head_session(
    qt_app, tmp_path, mode, stop,
):
    window = FittsTestWindow()
    events = []
    head_path = tmp_path / "head.json"
    window.set_recordings_dir_provider(lambda prompt: tmp_path)
    window.set_mouse_task_provider(lambda active: events.append(("mouse", active)))
    window.set_dwell_provider(lambda active: events.append(("dwell", active)))
    window.set_head_start_provider(
        lambda ready, mouse_control: events.append(("start", mouse_control))
        or ready(True) or True
    )

    def stop_head():
        events.append(("stop", None))
        if stop == "complete":
            assert not window.isVisible()
        return head_path

    window.set_head_session_provider(stop_head)
    window._start_mode_combo.setCurrentText(mode)
    window._task_combo.setCurrentText(TASK_COMBINED)
    window._on_start_clicked()
    assert events[0] == ("mouse", False)
    assert ("mouse", True) not in events
    assert ("dwell", True) not in events
    _finish_head_tasks(window)
    assert ("mouse", True) in events
    if mode == START_DWELL:
        assert events.index(("mouse", True)) < events.index(("dwell", True))
    if stop == "complete":
        window._on_all_trials_completed()
    elif stop == "cancel":
        window._on_back_to_setup_clicked()
    else:
        window.close()
    assert events.count(("stop", None)) == 1
    assert events[-1] == ("mouse", None)
    assert window._head_session_path() == head_path
    assert events.count(("stop", None)) == 1
    if stop != "cancel":
        saved = json.loads(Path(window._saved_path).read_text())
        assert saved["metadata"]["head_session_file"] == str(head_path)
    window.close()
    assert events.count(("stop", None)) == 1


def test_closing_during_startup_does_not_reopen_when_tracker_becomes_ready(qt_app):
    window = FittsTestWindow()
    pending = []
    window.set_head_start_provider(
        lambda ready, mouse_control: pending.append(ready) or True
    )
    window._start_mode_combo.setCurrentText(START_HEADMOUSE)
    window.show()
    window._on_start_clicked()
    assert window._waiting_for_headmouse
    window.close()
    pending[0](True)
    assert not window.isVisible()
    assert window._canvas is None
    assert not window._waiting_for_headmouse


def test_head_only_hides_all_mouse_task_controls(qt_app):
    window = FittsTestWindow()
    try:
        window._stimulus_combo.setCurrentText("Icon")
        assert not window._icon_combo.isHidden()
        window._task_combo.setCurrentText(TASK_HEAD_ONLY)
        for field in (
            window._mode_combo, window._stimulus_combo, window._icon_combo,
            window._amplitudes_edit, window._widths_edit,
        ):
            assert field.isHidden()
        window._task_combo.setCurrentText(TASK_MOUSE_ONLY)
        assert not window._icon_combo.isHidden()
    finally:
        window.close()


def test_standalone_event_loop_survives_save_recovery_then_exits(tmp_path):
    script = """
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from core import recording_paths
from fitts_law import fitts_settings
from fitts_law.fitts_test import FittsTestWindow

folder = Path(sys.argv[1])
fitts_settings._SETTINGS_PATH = folder / "settings.json"
recording_paths.default_config_path = lambda: folder / "config.json"
app = QApplication([])
window = FittsTestWindow()
window.show()
original_save = window._logger.save
attempts = []

def save(path=None):
    assert not window.isVisible()
    attempts.append(path)
    if len(attempts) == 1:
        raise PermissionError("Read-only folder")
    return original_save(path)

def choose_folder(*args):
    app.processEvents()
    assert not app.quitOnLastWindowClosed()
    return str(folder / "results")

window._logger.save = save
QMessageBox.warning = lambda *args: None
QFileDialog.getExistingDirectory = choose_folder
QTimer.singleShot(0, window._on_all_trials_completed)
QTimer.singleShot(5000, lambda: sys.exit("Event loop did not exit"))
app.exec()
assert len(attempts) == 2
assert window._saved_path.exists()
assert app.quitOnLastWindowClosed()
print("save recovery complete")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "save recovery complete" in result.stdout


def test_legacy_pretest_settings_migrate_to_combined_tasks(qt_app, monkeypatch, tmp_path):
    path = tmp_path / "old_settings.json"
    path.write_text(json.dumps({"initial_rest_enabled": True}))
    monkeypatch.setattr("fitts_law.fitts_settings._SETTINGS_PATH", path)
    window = FittsTestWindow()
    try:
        assert window._task_combo.currentText() == TASK_COMBINED
    finally:
        window.close()
