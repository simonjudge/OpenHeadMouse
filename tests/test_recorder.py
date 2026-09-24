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
