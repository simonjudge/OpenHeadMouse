"""Tests for core/recorder.py: save/load round-trip and stats."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from core import quaternion_math as qm
from core.recorder import Recorder, RecordedSample


def test_recorder_accumulates_samples():
    recorder = Recorder()
    q1 = qm.from_euler_zyx(np.radians(10), np.radians(5), 0)
    q2 = qm.from_euler_zyx(np.radians(-5), np.radians(-8), 0)

    recorder.record_sample(1.0, q1)
    recorder.record_sample(2.0, q2)

    assert recorder.sample_count == 2
    assert recorder.has_samples


def test_recorder_json_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="json")
        q = qm.from_euler_zyx(np.radians(15), np.radians(-10), np.radians(3))
        recorder.record_sample(1.5, q)

        filepath = recorder.save("test.json")
        assert filepath.exists()

        loaded = recorder.load(filepath)
        assert len(loaded) == 1
        assert loaded[0].timestamp == 1.5
        assert np.isclose(loaded[0].yaw_deg, 15.0, atol=1e-6)
        assert np.isclose(loaded[0].pitch_deg, -10.0, atol=1e-6)
        assert np.isclose(loaded[0].roll_deg, 3.0, atol=1e-6)


def test_recorder_preserves_provider_metadata_in_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="json")
        recorder.record_sample(
            1.5,
            qm.IDENTITY_QUATERNION,
            metadata={"gyro": np.array([1.0, 2.0, 3.0]), "confidence": np.float32(0.5)},
        )

        loaded = recorder.load(recorder.save("metadata.json"))

        assert loaded[0].metadata == {"gyro": [1.0, 2.0, 3.0], "confidence": 0.5}


def test_recorder_csv_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="csv")
        q1 = qm.from_euler_zyx(np.radians(10), 0, 0)
        q2 = qm.from_euler_zyx(0, np.radians(20), 0)

        recorder.record_sample(1.0, q1)
        recorder.record_sample(2.0, q2)

        filepath = recorder.save("test.csv")
        assert filepath.exists()

        loaded = recorder.load(filepath)
        assert len(loaded) == 2
        assert np.isclose(loaded[0].yaw_deg, 10.0, atol=1e-6)
        assert np.isclose(loaded[1].pitch_deg, 20.0, atol=1e-6)


def test_recorder_preserves_provider_metadata_in_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="csv")
        recorder.record_sample(1.0, qm.IDENTITY_QUATERNION, raw_metadata={"angular_rate": [1, 2, 3]})

        loaded = recorder.load(recorder.save("metadata.csv"))

        assert loaded[0].metadata == {"angular_rate": [1, 2, 3]}


def test_recorder_starts_without_samples():
    recorder = Recorder()

    assert recorder.sample_count == 0
    assert not recorder.has_samples


def test_recorder_json_schema_format():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="json")
        q = qm.IDENTITY_QUATERNION
        recorder.record_sample(0.0, q)

        filepath = recorder.save("test.json")
        with open(filepath) as f:
            data = json.load(f)

        assert data["format_version"] == "1.0"
        assert data["sample_count"] == 1
        assert "samples" in data
        assert data["samples"][0]["quaternion_w"] == 1.0


def test_recorder_empty_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="csv")
        filepath = recorder.save("empty.csv")
        assert filepath.exists()
        assert filepath.stat().st_size == 0


def test_recorder_handles_large_batch():
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = Recorder(output_dir=tmpdir, format="json")
        rng = np.random.default_rng(seed=42)

        n = 100
        for i in range(n):
            q = qm.normalize(rng.normal(size=4))
            recorder.record_sample(float(i) / 100.0, q)

        filepath = recorder.save("large.json")
        loaded = recorder.load(filepath)
        assert len(loaded) == n
        assert np.isclose(loaded[0].timestamp, 0.0, atol=1e-6)
        assert np.isclose(loaded[-1].timestamp, 0.99, atol=1e-6)


def _calibration_inputs():
    samples = [
        (10.0 + i * 0.1, qm.from_euler_zyx(np.radians(5 + d), np.radians(-3), np.radians(1)))
        for i, d in enumerate((-0.5, 0.0, 0.5))
    ]
    neutral = qm.mean_quaternion(q for _, q in samples)
    return samples, neutral


def test_recorder_json_stores_calibration_separately_from_movement(tmp_path):
    recorder = Recorder(output_dir=tmp_path, format="json")
    samples, neutral = _calibration_inputs()
    recorder.set_calibration(samples, neutral, duration_s=5.0)
    recorder.record_sample(20.0, qm.IDENTITY_QUATERNION)
    assert recorder.has_calibration

    filepath = recorder.save("calib.json")
    data = json.loads(filepath.read_text())
    assert data["format_version"] == "1.1"
    assert data["sample_count"] == 1
    assert len(data["samples"]) == 1
    calibration = data["calibration"]
    assert calibration["sample_count"] == 3
    assert calibration["duration_s"] == 5.0
    assert calibration["start_timestamp"] == pytest.approx(10.0)
    assert calibration["end_timestamp"] == pytest.approx(10.2)
    assert calibration["neutral_yaw_deg"] == pytest.approx(5.0, abs=1e-6)
    assert calibration["neutral_pitch_deg"] == pytest.approx(-3.0, abs=1e-6)
    assert calibration["neutral_roll_deg"] == pytest.approx(1.0, abs=1e-6)
    assert calibration["yaw_std_deg"] > 0
    assert len(calibration["samples"]) == 3
    assert calibration["samples"][0]["yaw_deg"] == pytest.approx(4.5, abs=1e-6)

    loaded = Recorder()
    movement = loaded.load(filepath)
    assert len(movement) == 1
    assert loaded.calibration["sample_count"] == 3
    assert len(loaded.calibration_samples) == 3


def test_recorder_csv_phase_column_only_with_calibration(tmp_path):
    recorder = Recorder(output_dir=tmp_path, format="csv")
    recorder.record_sample(20.0, qm.IDENTITY_QUATERNION)
    legacy = recorder.save("legacy.csv")
    assert "phase" not in legacy.read_text().splitlines()[0]

    samples, neutral = _calibration_inputs()
    recorder.set_calibration(samples, neutral)
    filepath = recorder.save("calib.csv")
    lines = filepath.read_text().splitlines()
    assert lines[0].endswith(",phase")
    assert [line.rsplit(",", 1)[1] for line in lines[1:]] == ["calibration"] * 3 + ["movement"]

    loaded = Recorder()
    movement = loaded.load(filepath)
    assert len(movement) == 1
    assert loaded.calibration["sample_count"] == 3
    assert loaded.calibration["neutral_yaw_deg"] == pytest.approx(5.0, abs=1e-6)


def test_recorder_json_without_calibration_is_unchanged(tmp_path):
    recorder = Recorder(output_dir=tmp_path, format="json")
    recorder.record_sample(0.0, qm.IDENTITY_QUATERNION)
    data = json.loads(recorder.save("plain.json").read_text())
    assert "calibration" not in data
    assert data["format_version"] == "1.0"
