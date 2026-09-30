"""Tests for the session viewer's Calibration section."""

import numpy as np

from core import quaternion_math as qm
from core.recorder import Recorder


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
        assert window.calibration_label.text() == "No calibration data in this session."
    finally:
        window.close()
