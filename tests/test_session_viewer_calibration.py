"""Tests for the session viewer's Calibration section."""

import numpy as np
import pytest
from types import SimpleNamespace

from core import quaternion_math as qm
from core.recorder import Recorder
from session_viewer.mesh_visualizer import calculate_neutral_quaternion


def _save_session(tmp_path, with_calibration: bool):
    recorder = Recorder(output_dir=tmp_path, format="json")
    for i in range(5):
        recorder.record_sample(20.0 + i * 0.1, qm.from_euler_zyx(np.radians(i), 0.0, 0.0))
    if with_calibration:
        samples = [
            (10.0 + i * 0.1, qm.from_euler_zyx(np.radians(5 + d), np.radians(-3), np.radians(1)))
            for i, d in enumerate((-0.5, 0.0, 0.5))
        ]
        recorder.set_calibration(samples, qm.mean_quaternion(q for _, q in samples), duration_s=5.0)
    return recorder.save("session.json")


def _open_in_viewer(filepath):
    from session_viewer.session_viewer import SessionVisualizerWindow

    recorder = Recorder()
    samples = recorder.load(filepath)
    window = SessionVisualizerWindow()
    window.load_session(samples, "test", recorder=recorder)
    return window


def test_viewer_shows_calibration_mean_and_spread(qt_app, tmp_path):
    window = _open_in_viewer(_save_session(tmp_path, with_calibration=True))
    try:
        text = window.calibration_label.text()
        assert "<b>Yaw:</b> +5.0° ± 0.41°" in text
        assert "<b>Pitch:</b> -3.0° ± 0.0" in text
        assert "<b>Roll:</b> +1.0° ± 0.0" in text
        assert "3 samples over 5.0s" in text
    finally:
        window.close()


def test_viewer_without_calibration(qt_app, tmp_path):
    window = _open_in_viewer(_save_session(tmp_path, with_calibration=False))
    try:
        assert "First samples" in window.calibration_label.text()
        np.testing.assert_allclose(
            window.neutral_q,
            qm.mean_quaternion(sample.quaternion for sample in window.recorded_samples),
        )
    finally:
        window.close()


def test_viewer_uses_saved_calibration_without_recenter_event(qt_app, tmp_path):
    window = _open_in_viewer(_save_session(tmp_path, with_calibration=True))
    try:
        np.testing.assert_allclose(window.neutral_q, window.recorder.calibration["neutral_quaternion"])
    finally:
        window.close()


def test_neutral_uses_initial_rest_instead_of_range_of_movement():
    recorder = Recorder()
    rest_q = qm.from_euler_zyx(0.4, -0.2, 0.1)
    for index in range(60):
        rest = 5 <= index < 10
        recorder.record_sample(
            index * 0.1, rest_q if rest else qm.from_euler_zyx(1.5, 0.6, 0.5),
            metadata={"task_phase": "initial_rest.hold" if rest else "range_of_movement.left.hold"},
        )
    np.testing.assert_allclose(calculate_neutral_quaternion(recorder._samples), rest_q)


def test_fallback_averages_only_first_second_and_at_most_30_samples():
    recorder = Recorder()
    initial = qm.from_euler_zyx(0.3, 0.2, -0.1)
    for index in range(100):
        recorder.record_sample(index * 0.01, initial if index < 30 else qm.from_euler_zyx(1.5, 0, 0))
    np.testing.assert_allclose(calculate_neutral_quaternion(recorder._samples), initial)
    slow = Recorder()
    for index in range(10):
        slow.record_sample(index * 0.4, initial if index < 3 else qm.from_euler_zyx(1.5, 0, 0))
    np.testing.assert_allclose(calculate_neutral_quaternion(slow._samples), initial)


def test_saved_calibration_wins_over_rest_and_fallback():
    reference = qm.from_euler_zyx(0.3, 0.2, 0.1)
    recorder = Recorder()
    recorder.set_calibration([(0, reference)], reference)
    recorder.record_sample(
        1, qm.from_euler_zyx(1.5, 0, 0), metadata={"task_phase": "initial_rest.hold"}
    )
    np.testing.assert_allclose(calculate_neutral_quaternion(recorder._samples, recorder), reference)


def test_recenter_selection_uses_start_of_shown_data():
    recorder = Recorder()
    poses = [qm.from_euler_zyx(yaw, 0, 0) for yaw in (0.1, 0.2)]
    for index, pose in enumerate(poses):
        recorder.record_event(index * 10, "recenter", {"quaternion": pose.tolist()})
    sample = SimpleNamespace(timestamp=15, host_time=None)
    np.testing.assert_allclose(calculate_neutral_quaternion([sample], recorder), poses[1])


def test_trimmed_playback_uses_original_samples_for_fallback():
    recorder = Recorder()
    initial = qm.from_euler_zyx(0.2, 0.1, 0)
    for index in range(50):
        recorder.record_sample(index * 0.1, initial if index < 30 else qm.from_euler_zyx(1.4, 0, 0))
    np.testing.assert_allclose(
        calculate_neutral_quaternion(recorder._samples[40:], reference_samples=recorder._samples), initial
    )


@pytest.mark.parametrize("quaternion", [[0, 0, 0, 0], [1, 0, 0], [float("nan"), 0, 0, 0]])
def test_invalid_saved_reference_is_reported(quaternion):
    recorder = SimpleNamespace(calibration={"neutral_quaternion": quaternion})
    with pytest.raises(ValueError, match="invalid neutral quaternion"):
        calculate_neutral_quaternion([], recorder)
