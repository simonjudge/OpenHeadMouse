"""The Fitts test's 'Record Head Movement' option and how it starts tracking."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.headmouse import TrackingState
from fitts_law.fitts_test import MODE_ISO_STANDARD, TASK_COMBINED, TASK_MOUSE_ONLY, FittsTestWindow
from gui.control_panel import ControlPanel
from main import App
from providers.base_provider import ProviderState


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


# -- setup page ------------------------------------------------------------

def test_pretest_timings_are_only_shown_when_head_tasks_are_selected(qt_app):
    window = FittsTestWindow()
    try:
        window._setup_page.show()
        window._task_combo.setCurrentText(TASK_MOUSE_ONLY)
        for field in window._pretest_fields:
            assert field.isVisibleTo(window._setup_page) is False
            label = window._form.labelForField(field)
            if label is not None:
                assert label.isVisibleTo(window._setup_page) is False

        window._task_combo.setCurrentText(TASK_COMBINED)
        for field in window._pretest_fields:
            assert field.isVisibleTo(window._setup_page) is True
    finally:
        window.close()


def test_mouse_only_does_not_run_head_tasks(qt_app):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, False)
        window._task_combo.setCurrentText(TASK_MOUSE_ONLY)

        window._on_start_clicked()

        assert window._pretest_overlay is None
        assert window._canvas is not None
        configuration = window._logger.metadata["pretest_configuration"]
        assert configuration["initial_rest_enabled"] is False
        assert configuration["range_of_movement_enabled"] is False
        assert window._logger.metadata["record_head_movement"] is False
    finally:
        window.close()


# -- starting the test -----------------------------------------------------

def test_start_test_records_head_movement_without_mouse_control(qt_app):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        window._task_combo.setCurrentText(TASK_MOUSE_ONLY)
        window._access_method_combo.setCurrentText("EyeGaze")

        calls = {}

        def start_provider(on_ready, mouse_control=True):
            calls["on_ready"] = on_ready
            calls["mouse_control"] = mouse_control
            return True

        window.set_head_start_provider(start_provider)
        window._on_start_clicked()

        # The tracker is brought up first; the cursor stays with the access
        # method under test.
        assert calls["mouse_control"] is False
        assert window._canvas is None

        calls["on_ready"](True)

        assert window._canvas is not None
        # Head samples are labelled even though the cursor is driven by EyeGaze.
        assert window._use_head_phase_provider is True
        assert window._logger.metadata["access_method"] == "EyeGaze"
        assert window._logger.metadata["record_head_movement"] is True
    finally:
        window.close()


def test_start_test_only_starts_tracking_once_per_run(qt_app):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        starts = []

        def start_provider(on_ready, mouse_control=True):
            starts.append(mouse_control)
            on_ready(True)
            return True

        window.set_head_start_provider(start_provider)
        window._on_start_clicked()

        assert starts == [False]
        assert window._canvas is not None

        # Re-running from the results page must not restart the tracker...
        window._on_start_clicked()
        assert starts == [False]

        # ...but going back to setup re-applies the settings for the next run.
        window._on_back_to_setup_clicked()
        window._on_start_clicked()
        assert starts == [False, False]
    finally:
        window.close()


def test_start_test_without_head_recording_never_touches_the_tracker(qt_app):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, False)
        starts = []

        window.set_head_start_provider(
            lambda on_ready, mouse_control=True: starts.append(mouse_control) or True
        )
        window._on_start_clicked()

        assert starts == []
        assert window._canvas is not None
        assert window._use_head_phase_provider is False
    finally:
        window.close()


def test_start_test_runs_standalone_when_no_head_app_is_hosting(qt_app):
    """Checking the box without a host app still runs the test (and pre-tests)."""
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        window._task_combo.setCurrentText(TASK_COMBINED)

        window._on_start_clicked()

        assert window._pretest_overlay is not None
        window._pretest_overlay.stop()
    finally:
        window.close()


def test_start_test_aborts_when_the_provider_refuses(qt_app):
    window = FittsTestWindow()
    try:
        _configure_small_test(window)
        _set_checked_without_saving(window._record_head_checkbox, True)
        window.set_head_start_provider(lambda on_ready, mouse_control=True: False)

        window._on_start_clicked()

        assert window._canvas is None
        assert window._start_button.isEnabled() is True
        assert window._head_tracking_started is False
    finally:
        window.close()


# -- host application ------------------------------------------------------

def _make_tracking_app(*, recording: bool, mouse_control: bool) -> App:
    app = App.__new__(App)
    app._provider = SimpleNamespace(
        name="already running", state=ProviderState.RUNNING
    )
    app._tracking_ready_callbacks = []
    app._calibrating = False
    app._ximu_waiting_to_start = False
    app._last_sample = SimpleNamespace(timestamp=1.0)
    # The recordings folder is covered in test_recordings_folder; here it is
    # simply already chosen and ready.
    app._config_path = None
    app._recordings_base_dir = Path("study")
    app.ensure_recordings_base_dir = lambda prompt=True: app._recordings_base_dir
    app.headmouse = SimpleNamespace(state=TrackingState.ACTIVE)
    app.window = SimpleNamespace(
        controls=ControlPanel(
            recording_enabled=recording, mouse_control_enabled=mouse_control
        ),
        setup_panel=SimpleNamespace(
            request_start=lambda: pytest.fail("the provider is already running")
        ),
    )
    return app


def test_start_head_tracking_records_without_mouse_control(qt_app):
    app = _make_tracking_app(recording=False, mouse_control=True)
    ready = []

    assert app.start_head_tracking(ready.append, mouse_control=False) is True

    assert ready == [True]
    assert app.window.controls.recording_enabled is True
    assert app.window.controls.mouse_control_enabled is False


def test_start_head_tracking_enables_mouse_control_for_a_headmouse_run(qt_app):
    app = _make_tracking_app(recording=False, mouse_control=False)
    ready = []

    assert app.start_head_tracking(ready.append) is True

    assert ready == [True]
    assert app.window.controls.recording_enabled is True
    assert app.window.controls.mouse_control_enabled is True


def test_start_head_tracking_waits_for_a_live_provider_sample(qt_app):
    app = _make_tracking_app(recording=False, mouse_control=True)
    app._last_sample = None
    ready = []

    assert app.start_head_tracking(ready.append, mouse_control=False) is True

    assert ready == []
    app._last_sample = SimpleNamespace(timestamp=1.0)
    app._flush_tracking_ready()
    assert ready == [True]


def test_start_head_tracking_waits_for_provider_connection(qt_app):
    app = _make_tracking_app(recording=False, mouse_control=True)
    app._provider.state = ProviderState.STOPPED
    ready = []

    assert app.start_head_tracking(ready.append, mouse_control=False) is True

    assert ready == []
    app._provider.state = ProviderState.RUNNING
    app._flush_tracking_ready()
    assert ready == [True]


def test_start_head_tracking_leaves_recording_alone_when_not_requested(qt_app):
    app = _make_tracking_app(recording=False, mouse_control=False)

    app.start_head_tracking(recording=False)

    assert app.window.controls.recording_enabled is False
