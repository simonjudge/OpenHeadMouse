"""The base folder recordings are written to, and asking for it on first use."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import recording_paths
from core.headmouse import TrackingState
from fitts_law.fitts_test import MODE_ISO_STANDARD, FittsTestWindow
from gui.control_panel import ControlPanel
from main import App
from providers.base_provider import ProviderState


# -- path layout -----------------------------------------------------------

def test_sessions_and_fitts_results_share_one_base_folder(tmp_path):
    assert recording_paths.head_sessions_dir(tmp_path) == tmp_path / "sessions"
    assert recording_paths.fitts_sessions_dir(tmp_path) == tmp_path / "fitts_sessions"


def test_base_dir_from_config_treats_blank_as_unset():
    assert recording_paths.base_dir_from_config({}) is None
    assert recording_paths.base_dir_from_config({"recorder": {}}) is None
    assert recording_paths.base_dir_from_config({"recorder": {"base_dir": None}}) is None
    assert recording_paths.base_dir_from_config({"recorder": {"base_dir": "  "}}) is None
    assert recording_paths.base_dir_from_config(
        {"recorder": {"base_dir": "/data/study"}}
    ) == Path("/data/study")


def test_load_base_dir_reads_config_and_tolerates_a_missing_file(tmp_path):
    config_path = tmp_path / "config.json"
    assert recording_paths.load_base_dir(config_path) is None

    config_path.write_text(json.dumps({"recorder": {"base_dir": str(tmp_path / "study")}}))
    assert recording_paths.load_base_dir(config_path) == tmp_path / "study"

    config_path.write_text("not json")
    assert recording_paths.load_base_dir(config_path) is None


def test_save_base_dir_creates_the_config_and_keeps_other_settings(tmp_path):
    config_path = tmp_path / "nested" / "config.json"

    assert recording_paths.save_base_dir(tmp_path / "study", config_path) is True
    assert recording_paths.load_base_dir(config_path) == tmp_path / "study"

    config_path.write_text(
        json.dumps({"ui": {"recording_enabled": True}, "recorder": {"format": "json"}})
    )
    assert recording_paths.save_base_dir(tmp_path / "other", config_path) is True

    saved = json.loads(config_path.read_text())
    assert saved["ui"] == {"recording_enabled": True}
    assert saved["recorder"]["format"] == "json"
    assert saved["recorder"]["base_dir"] == str(tmp_path / "other")


def test_save_base_dir_repairs_an_unusable_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("not json")

    assert recording_paths.save_base_dir(tmp_path / "study", config_path) is True
    assert recording_paths.load_base_dir(config_path) == tmp_path / "study"


def test_prompt_for_base_dir_uses_a_folder_chooser(
    qt_app, tmp_path, monkeypatch, isolated_recordings_dir
):
    from PySide6.QtWidgets import QFileDialog

    captions = []

    def fake_chooser(parent=None, caption="", directory="", *args, **kwargs):
        captions.append(caption)
        return str(tmp_path / "study")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_chooser)

    chosen = isolated_recordings_dir.real_prompt(reason="why")

    assert chosen == tmp_path / "study"
    assert "recordings" in captions[0]
    assert "why" in captions[0]


def test_prompt_for_base_dir_returns_none_when_cancelled(
    qt_app, monkeypatch, isolated_recordings_dir
):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda parent=None, caption="", directory="", *a, **k: "",
    )

    assert isolated_recordings_dir.real_prompt() is None


# -- main window -----------------------------------------------------------

def test_folder_is_shown_and_changed_from_the_file_menu_only(qt_app):
    from gui.headmouse_gui import HeadMouseWindow

    window = HeadMouseWindow(initial_kx=1.0, initial_ky=1.0)
    try:
        assert not hasattr(window.controls, "set_recordings_dir")
        assert "Not set" in window.recordings_dir_text

        requested = []
        window.recordings_folder_requested.connect(lambda: requested.append(True))
        window._recordings_folder_action.trigger()
        assert requested == [True]

        window.set_recordings_dir(Path("/data/study"))
        assert window.recordings_dir_text == str(Path("/data/study"))
        assert str(Path("/data/study")) in window._recordings_folder_action.toolTip()
        assert str(Path("/data/study")) in window._recordings_folder_info_action.text()
        assert not window._recordings_folder_info_action.isEnabled()
    finally:
        window.close()


# -- host application ------------------------------------------------------

def _make_app(tmp_path, *, config_path, base_dir=None) -> App:
    app = App.__new__(App)
    config = {
        "recorder": {"output_dir": str(tmp_path / "legacy"), "format": "json"},
        "ui": {},
    }
    if base_dir is not None:
        config["recorder"]["base_dir"] = str(base_dir)
    app._config = config
    app._config_path = config_path
    app._recordings_base_dir = recording_paths.base_dir_from_config(config)
    app._provider = SimpleNamespace(state=ProviderState.RUNNING)
    app._tracking_ready_callbacks = []
    app._calibrating = False
    app._ximu_waiting_to_start = False
    app._last_sample = SimpleNamespace(timestamp=1.0)
    app.headmouse = SimpleNamespace(state=TrackingState.ACTIVE)
    app._recorder = SimpleNamespace(output_dir=Path(config["recorder"]["output_dir"]))
    shown = []
    app.window = SimpleNamespace(
        controls=ControlPanel(recording_enabled=False, mouse_control_enabled=False),
        setup_panel=SimpleNamespace(request_start=lambda: None),
        set_recordings_dir=shown.append,
        shown_recordings_dirs=shown,
    )
    return app


def test_head_sessions_go_in_the_base_folder_when_one_is_set(qt_app, tmp_path):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json", base_dir=tmp_path / "study")

    assert app._head_sessions_dir() == tmp_path / "study" / "sessions"


def test_head_sessions_fall_back_to_the_configured_default(qt_app, tmp_path):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")

    assert app._head_sessions_dir() == tmp_path / "legacy"


def test_recording_prompts_for_a_folder_once_and_remembers_it(qt_app, tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    app = _make_app(tmp_path, config_path=config_path)
    chosen = tmp_path / "study"
    prompts = []

    def fake_prompt(parent=None, reason=""):
        prompts.append(reason)
        return chosen

    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", fake_prompt)

    app._on_recording_toggled(True)

    assert len(prompts) == 1
    assert app._recordings_base_dir == chosen
    # Head sessions are redirected straight away, not just on the next run.
    assert app._recorder.output_dir == chosen / "sessions"
    assert app.window.shown_recordings_dirs[-1] == chosen
    assert json.loads(config_path.read_text())["recorder"]["base_dir"] == str(chosen)

    # Already answered, so starting a recording must not ask again.
    assert app.ensure_recordings_base_dir() == chosen
    assert len(prompts) == 1


def test_cancelling_the_prompt_keeps_the_default_folder(qt_app, tmp_path, monkeypatch):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    monkeypatch.setattr(
        recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": None
    )

    assert app.ensure_recordings_base_dir() is None
    assert app._recordings_base_dir is None
    assert app._head_sessions_dir() == tmp_path / "legacy"


def test_folder_is_remembered_even_without_a_loaded_config_file(qt_app, tmp_path, monkeypatch):
    """A packaged/embedded App still asks, and saves to the shared config."""
    shared_config = tmp_path / "shared" / "config.json"
    monkeypatch.setattr(recording_paths, "default_config_path", lambda: shared_config)
    app = _make_app(tmp_path, config_path=None)
    chosen = tmp_path / "study"
    monkeypatch.setattr(
        recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": chosen
    )

    assert app.ensure_recordings_base_dir() == chosen
    assert recording_paths.load_base_dir(shared_config) == chosen


def test_starting_head_recording_asks_for_the_folder(qt_app, tmp_path, monkeypatch):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    chosen = tmp_path / "study"
    monkeypatch.setattr(
        recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": chosen
    )

    assert app.start_head_tracking(mouse_control=False) is True

    assert app._recordings_base_dir == chosen
    assert app._recorder.output_dir == chosen / "sessions"


def test_no_prompt_when_head_tracking_without_recording(qt_app, tmp_path, monkeypatch):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    monkeypatch.setattr(
        recording_paths,
        "prompt_for_base_dir",
        lambda parent=None, reason="": pytest.fail("must not ask"),
    )

    app.start_head_tracking(recording=False)

    assert app._recordings_base_dir is None


def test_a_missing_recordings_folder_is_created_before_recording(qt_app, tmp_path, monkeypatch):
    base = tmp_path / "deleted" / "study"
    app = _make_app(tmp_path, config_path=tmp_path / "config.json", base_dir=base)
    monkeypatch.setattr(
        recording_paths,
        "prompt_for_base_dir",
        lambda parent=None, reason="": pytest.fail("a usable folder must not be asked for"),
    )

    assert app.ensure_recordings_base_dir() == base
    assert (base / "sessions").is_dir()
    assert (base / "fitts_sessions").is_dir()


def test_an_unusable_recordings_folder_asks_for_another(qt_app, tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("")
    app = _make_app(tmp_path, config_path=tmp_path / "config.json", base_dir=blocker)
    warned = []
    monkeypatch.setattr(
        recording_paths, "warn_unusable_base_dir", lambda parent, folder, problem: warned.append(folder)
    )
    chosen = tmp_path / "study"
    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": chosen)

    assert app.ensure_recordings_base_dir() == chosen
    assert warned == [blocker]
    assert app._recorder.output_dir == chosen / "sessions"


def test_head_tracking_is_not_started_to_record_without_a_folder(qt_app, tmp_path, monkeypatch):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": None)
    warnings = []
    app._warn_no_recordings_dir = warnings.append
    callback = lambda started: None

    assert app.start_head_tracking(callback, mouse_control=False) is False

    assert warnings
    assert callback not in app._tracking_ready_callbacks
    assert not app.window.controls.recording_enabled


def test_recording_is_switched_back_off_when_no_folder_is_chosen(qt_app, tmp_path, monkeypatch):
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": None)
    app.window.controls.recording_toggled.connect(app._on_recording_toggled)

    app.window.controls.set_recording_enabled(True)

    assert not app.window.controls.recording_enabled
    assert app._config["ui"]["recording_enabled"] is False


def test_choosing_a_folder_redirects_the_next_recording(qt_app, tmp_path, monkeypatch):
    from core.recorder import Recorder

    app = _make_app(tmp_path, config_path=tmp_path / "config.json", base_dir=tmp_path / "old")
    app._recorder = Recorder(output_dir=app._head_sessions_dir())
    starts = []

    def fake_prompt(parent=None, reason="", start_dir=None):
        starts.append(start_dir)
        return tmp_path / "new"

    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", fake_prompt)

    app._on_choose_recordings_dir()

    assert starts == [tmp_path / "old"]
    assert app.window.shown_recordings_dirs[-1] == tmp_path / "new"
    app._recorder.record_sample(0.0, [1.0, 0.0, 0.0, 0.0])
    assert app._save_recording().parent == tmp_path / "new" / "sessions"


def test_a_failed_save_asks_for_another_folder_instead_of_losing_the_session(
    qt_app, tmp_path, monkeypatch
):
    from core.recorder import Recorder

    blocker = tmp_path / "not_a_folder"
    blocker.write_text("")
    app = _make_app(tmp_path, config_path=tmp_path / "config.json")
    app._recorder = Recorder(output_dir=blocker / "sessions")
    app._recorder.record_sample(0.0, [1.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(recording_paths, "warn_unusable_base_dir", lambda *args: None)
    chosen = tmp_path / "rescue"
    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": chosen)

    saved = app._save_recording()

    assert saved.parent == chosen / "sessions"
    assert json.loads(saved.read_text())["sample_count"] == 1


# -- Fitts test ------------------------------------------------------------

def _configure_small_test(window: FittsTestWindow) -> None:
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._stimulus_combo.setCurrentText("Circle (ISO standard)")
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("3")


def _set_checked_without_saving(checkbox, checked: bool) -> None:
    blocked = checkbox.blockSignals(True)
    checkbox.setChecked(checked)
    checkbox.blockSignals(blocked)


def test_fitts_results_go_in_the_base_folder(qt_app, tmp_path):
    window = FittsTestWindow()
    try:
        window.set_recordings_dir_provider(lambda prompt=True: tmp_path / "study")
        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"
    finally:
        window.close()


def test_fitts_asks_for_the_folder_when_recording_head_movement(qt_app, tmp_path):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        prompts = []

        def provider(prompt=True):
            prompts.append(prompt)
            return tmp_path / "study"

        window.set_recordings_dir_provider(provider)
        # Registering the provider only reads the folder; it must not ask.
        assert prompts == [False]

        window.set_head_start_provider(
            lambda on_ready, mouse_control=True: on_ready(True) or True
        )
        window._on_start_clicked()

        assert True in prompts
        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"
    finally:
        window.close()


def test_fitts_needs_the_folder_even_when_head_movement_is_not_recorded(qt_app, tmp_path):
    """The test's own results are recordings too, so they need the folder."""
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, False)
        prompts = []

        def provider(prompt=True):
            prompts.append(prompt)
            return tmp_path / "study"

        window.set_recordings_dir_provider(provider)
        window._on_start_clicked()

        assert True in prompts
        assert window._canvas is not None
        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"
    finally:
        window.close()


def test_fitts_is_not_started_without_a_recordings_folder(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, False)
        window.set_recordings_dir_provider(lambda prompt=True: None)

        window._on_start_clicked()

        assert window._canvas is None
        assert warnings and "recordings folder" in warnings[0][2]
    finally:
        window.close()


# -- standalone Fitts test (no OpenHeadMouse window to ask on its behalf) -------

def test_standalone_fitts_asks_for_the_folder_and_remembers_it(qt_app, tmp_path, monkeypatch):
    chosen = tmp_path / "study"
    prompts = []

    def fake_prompt(parent=None, reason=""):
        prompts.append(reason)
        return chosen

    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", fake_prompt)

    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        # Building the window only reads the folder; it must not ask.
        assert prompts == []

        window._on_start_clicked()

        assert len(prompts) == 1
        assert window._logger.output_dir == chosen / "fitts_sessions"
        # Saved in both places, so neither this window nor the OpenHeadMouse
        # window asks again.
        assert window._settings.get("recordings_base_dir") == str(chosen)
        assert recording_paths.load_base_dir() == chosen
    finally:
        window.close()

    # A second standalone run picks the folder up without asking again.
    second = FittsTestWindow()
    try:
        assert second._logger.output_dir == chosen / "fitts_sessions"
        assert len(prompts) == 1
    finally:
        second.close()


def test_standalone_fitts_reuses_its_own_setting_when_config_is_unavailable(
    qt_app, tmp_path, monkeypatch
):
    """The answer survives even if config.json could not be written."""
    from fitts_law import fitts_settings

    stored = fitts_settings.FittsSettings()
    stored.set("recordings_base_dir", str(tmp_path / "study"))
    stored.save()

    monkeypatch.setattr(
        recording_paths,
        "prompt_for_base_dir",
        lambda parent=None, reason="": pytest.fail("must not ask again"),
    )

    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)

        window._on_start_clicked()

        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"
    finally:
        window.close()


def test_standalone_fitts_prefers_the_folder_set_in_the_headmouse_gui(qt_app, tmp_path):
    from fitts_law import fitts_settings

    stored = fitts_settings.FittsSettings()
    stored.set("recordings_base_dir", str(tmp_path / "old"))
    stored.save()
    recording_paths.save_base_dir(tmp_path / "shared")

    window = FittsTestWindow()
    try:
        assert window._logger.output_dir == tmp_path / "shared" / "fitts_sessions"
    finally:
        window.close()


def test_standalone_fitts_is_not_started_when_the_prompt_is_cancelled(qt_app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        recording_paths, "prompt_for_base_dir", lambda parent=None, reason="": None
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))

    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)

        window._on_start_clicked()

        assert window._canvas is None
        assert len(warnings) == 1
    finally:
        window.close()


def test_standalone_fitts_asks_for_the_folder_without_head_recording(qt_app, tmp_path, monkeypatch):
    prompts = []

    def fake_prompt(parent=None, reason=""):
        prompts.append(reason)
        return tmp_path / "study"

    monkeypatch.setattr(recording_paths, "prompt_for_base_dir", fake_prompt)

    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, False)

        window._on_start_clicked()

        assert len(prompts) == 1
        assert window._canvas is not None
        assert (tmp_path / "study" / "fitts_sessions").is_dir()
    finally:
        window.close()


def test_standalone_fitts_reads_the_shared_folder_without_asking(
    qt_app, tmp_path, monkeypatch
):
    config_path = tmp_path / "standalone" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"recorder": {"base_dir": str(tmp_path / "study")}}))
    monkeypatch.setattr(recording_paths, "default_config_path", lambda: config_path)
    monkeypatch.setattr(
        recording_paths,
        "prompt_for_base_dir",
        lambda parent=None, reason="": pytest.fail("standalone must not ask"),
    )

    window = FittsTestWindow()
    try:
        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"

        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        window._on_start_clicked()

        assert window._logger.output_dir == tmp_path / "study" / "fitts_sessions"
    finally:
        window.close()
