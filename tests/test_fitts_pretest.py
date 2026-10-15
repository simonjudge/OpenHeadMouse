from __future__ import annotations

import json
import queue
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from core import quaternion_math as qm
from core.headmouse import TrackingState
from core.recorder import Recorder
from fitts_law.fitts_test import MODE_ISO_STANDARD, TASK_COMBINED, FittsTestWindow
from fitts_law.pretest import PreTestTaskOverlay, build_pretest_stages
from main import App
from providers.base_provider import ProviderSample


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def _move_event(x: float, y: float) -> QMouseEvent:
    point = QPointF(x, y)
    return QMouseEvent(
        QEvent.MouseMove,
        point,
        point,
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )


def _configure_small_test(window: FittsTestWindow) -> None:
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._stimulus_combo.setCurrentText("Circle (ISO standard)")
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("3")
    # The pre-test tasks are only offered while head movement is recorded.
    _set_checked_without_saving(window._record_head_checkbox, True)


def _set_checked_without_saving(checkbox, checked: bool) -> None:
    blocked = checkbox.blockSignals(True)
    checkbox.setChecked(checked)
    checkbox.blockSignals(blocked)


def test_pretest_stage_builder_uses_shared_move_and_hold_timings():
    stages = build_pretest_stages(True, True, 2.5, 1.5)

    assert [stage["phase"] for stage in stages] == [
        "initial_rest.move",
        "initial_rest.hold",
        "range_of_movement.left.move",
        "range_of_movement.left.hold",
        "range_of_movement.right.move",
        "range_of_movement.right.hold",
        "range_of_movement.up.move",
        "range_of_movement.up.hold",
        "range_of_movement.down.move",
        "range_of_movement.down.hold",
    ]
    assert [stage["duration_s"] for stage in stages] == [
        2.5,
        1.5,
        2.5,
        1.5,
        2.5,
        1.5,
        2.5,
        1.5,
        2.5,
        1.5,
    ]


def test_pretest_overlay_records_phase_boundaries_and_cursor_trajectory(qt_app):
    clock = FakeClock(10.0)
    host_clock = FakeClock(1000.0)
    overlay = PreTestTaskOverlay(
        build_pretest_stages(True, False, 2.0, 3.0),
        clock=clock,
        host_clock=host_clock,
        cursor_position=lambda: None,
    )
    phases = []
    completed = []
    overlay.phase_changed.connect(phases.append)
    overlay.completed.connect(lambda: completed.append(True))

    overlay.start()
    overlay.stop()
    host_clock.now = 1000.25
    overlay.mouseMoveEvent(_move_event(123.0, 456.0))

    clock.now = 12.0
    host_clock.now = 1002.0
    overlay._tick()
    clock.now = 15.0
    host_clock.now = 1005.0
    overlay._tick()

    assert phases == ["initial_rest.move", "initial_rest.hold", ""]
    assert completed == [True]
    assert overlay.records[0]["start_host_time"] == 1000.0
    assert overlay.records[0]["end_host_time"] == 1002.0
    assert overlay.records[0]["cursor_trajectory"] == [
        {"host_time": 1000.25, "x_px": 123.0, "y_px": 456.0}
    ]
    assert overlay.records[1]["start_host_time"] == 1002.0
    assert overlay.records[1]["end_host_time"] == 1005.0


def test_pretest_overlay_polls_cursor_each_tick(qt_app):
    from PySide6.QtCore import QPointF

    clock = FakeClock(10.0)
    host_clock = FakeClock(1000.0)
    position = QPointF(10.0, 20.0)
    overlay = PreTestTaskOverlay(
        build_pretest_stages(True, False, 2.0, 3.0),
        clock=clock, host_clock=host_clock, cursor_position=lambda: position,
    )
    overlay.start()
    overlay.stop()
    host_clock.now = 1000.5
    overlay._tick()
    position = QPointF(15.0, 25.0)
    host_clock.now = 1001.0
    overlay._tick()

    assert overlay.records[0]["cursor_trajectory"] == [
        {"host_time": 1000.5, "x_px": 10.0, "y_px": 20.0},
        {"host_time": 1001.0, "x_px": 15.0, "y_px": 25.0},
    ]


def test_fitts_pretest_runs_before_trials_and_labels_fitts_task(qt_app):
    window = FittsTestWindow()
    _configure_small_test(window)
    window._task_combo.setCurrentText(TASK_COMBINED)
    phases = []
    window.set_head_phase_provider(
        lambda phase, completed=True: phases.append((phase, completed))
    )

    window._on_start_clicked()

    overlay = window._pretest_overlay
    assert overlay is not None
    overlay.stop()
    assert window._canvas is None
    assert phases == [("initial_rest.move", True)]

    overlay._phase_start -= overlay.current_stage["duration_s"]
    overlay._tick()
    assert phases[-1] == ("initial_rest.hold", True)
    while window._pretest_overlay is not None:
        overlay._phase_start -= overlay.current_stage["duration_s"]
        overlay._tick()

    assert window._pretest_overlay is None
    assert window._canvas is not None
    assert phases[-2:] == [(None, True), ("fitts_test", True)]
    metadata = window._logger.metadata
    assert [record["phase"] for record in metadata["pretest_phases"]][:2] == [
        "initial_rest.move",
        "initial_rest.hold",
    ]
    assert metadata["fitts_task"]["start_host_time"] is not None
    assert metadata["fitts_task"]["end_host_time"] is None

    window._on_back_to_setup_clicked()
    assert phases[-1] == (None, False)
    assert metadata["fitts_task"]["completed"] is False


def test_pretest_metadata_is_saved_with_fitts_results(qt_app, tmp_path):
    window = FittsTestWindow()
    _configure_small_test(window)
    window._task_combo.setCurrentText(TASK_COMBINED)
    window._on_start_clicked()
    overlay = window._pretest_overlay
    overlay.stop()

    while overlay.current_stage is not None:
        overlay._phase_start -= overlay.current_stage["duration_s"]
        overlay._tick()
        if window._pretest_overlay is None:
            break

    path = window._logger.save(tmp_path / "pretest.json")
    data = json.loads(path.read_text())
    records = data["metadata"]["pretest_phases"]
    assert len(records) == 10
    assert records[0]["phase"] == "initial_rest.move"
    assert records[-1]["phase"] == "range_of_movement.down.hold"
    assert data["metadata"]["fitts_task"]["phase"] == "fitts_test"


class FakeHeadMouse:
    def __init__(self):
        self.state = TrackingState.ACTIVE
        self.cursor_position = (100.0, 100.0)

    def set_tracking_state(self, active):
        self.state = TrackingState.ACTIVE if active else TrackingState.PAUSED

    def process_target(self, x, y, timestamp):
        self.cursor_position = (x, y)


class FakeDsp:
    origin_x = 0.0
    origin_y = 0.0
    screen_width = 200.0
    screen_height = 200.0

    def __init__(self):
        self.neutral = None

    def reset(self, position):
        pass

    def recenter(self, quaternion):
        self.neutral = np.asarray(quaternion)

    def relative_euler_deg(self, quaternion):
        return 0.0, 0.0, 0.0


def _make_app_stub(tmp_path, samples):
    app = App.__new__(App)
    app._provider = SimpleNamespace(output_queue=queue.Queue())
    for sample in samples:
        app._provider.output_queue.put(sample)
    app._recorder = Recorder(output_dir=tmp_path)
    app._last_sample = None
    app._fitts_recording_phase = None
    app._fitts_calibration_buffer = []
    app._fitts_rest_calibrating = False
    app._mouse_was_active = False
    app._tracking_ready_callbacks = []
    app._calibrating = False
    app._ximu_waiting_to_start = False
    app._ximu_wifi_provider = False
    app._qm = qm
    app.dsp = FakeDsp()
    app.headmouse = FakeHeadMouse()
    app.window = SimpleNamespace(
        controls=SimpleNamespace(
            recording_enabled=True,
            mouse_control_enabled=False,
        ),
        update_telemetry=lambda *args: None,
    )
    app._show_calibration_warning = lambda text: pytest.fail(text)
    return app


def test_poll_records_every_queued_sample_with_the_current_task_phase(tmp_path):
    samples = [
        ProviderSample(
            timestamp=float(index),
            quaternion=qm.from_euler_zyx(index * 0.01, 0.0, 0.0),
            raw_metadata={"sequence": index},
        )
        for index in range(5)
    ]
    app = _make_app_stub(tmp_path, samples)
    app._fitts_recording_phase = "range_of_movement.left.move"

    app._poll()

    assert app._recorder.sample_count == 5
    path = app._recorder.save("all_samples.json")
    saved = json.loads(path.read_text())["samples"]
    assert [sample["metadata"]["sequence"] for sample in saved] == list(range(5))
    assert {
        sample["metadata"]["task_phase"] for sample in saved
    } == {"range_of_movement.left.move"}


def test_initial_rest_hold_samples_recalibrate_headmouse(tmp_path):
    rest = [
        ProviderSample(
            timestamp=10.0 + index,
            quaternion=qm.from_euler_zyx(0.2 + index * 0.01, -0.1, 0.0),
        )
        for index in range(3)
    ]
    app = _make_app_stub(tmp_path, [])

    app.set_fitts_recording_phase("initial_rest.move")
    assert app.headmouse.state == TrackingState.PAUSED
    app.set_fitts_recording_phase("initial_rest.hold")
    app._record_provider_samples(rest, "initial_rest.hold")
    app._last_sample = rest[-1]
    app.set_fitts_recording_phase("range_of_movement.left.move")

    expected = qm.mean_quaternion(sample.quaternion for sample in rest)
    np.testing.assert_allclose(app.dsp.neutral, expected)
    assert app.headmouse.state == TrackingState.ACTIVE
    assert app._recorder.has_calibration
    assert app._recorder.calibration["sample_count"] == 3


def test_cancelling_initial_rest_restores_tracking_without_calibrating(tmp_path):
    app = _make_app_stub(tmp_path, [])

    app.set_fitts_recording_phase("initial_rest.move")
    app.set_fitts_recording_phase(None, completed=False)

    assert app.headmouse.state == TrackingState.ACTIVE
    assert app._fitts_rest_calibrating is False
    assert not app._recorder.has_calibration
