"""Tests for fitts_law/results.py: FittsResultsLogger save/load round-trip."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fitts_law.results import FittsResultsLogger, TrialResult


def _trial():
    return TrialResult(
        condition_label="A=300 W=30",
        block_type="circle",
        amplitude_px=300.0,
        width_px=30.0,
        start_pos=(100.0, 100.0),
        target_pos=(400.0, 100.0),
        click_pos=(405.0, 98.0),
        start_time=0.0,
        end_time=0.6,
        movement_time_s=0.6,
        hit=True,
        trial_index=3,
        repetition_index=1,
        trajectory=[[0.0, 100.0, 100.0], [0.3, 250.0, 99.0], [0.6, 405.0, 98.0]],
    )


def test_logger_json_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = FittsResultsLogger(output_dir=tmpdir)
        logger.add_trial(_trial())

        filepath = logger.save("test.json")
        assert filepath.exists()

        loaded = FittsResultsLogger(output_dir=tmpdir).load(filepath)
        assert len(loaded) == 1
        assert loaded[0].condition_label == "A=300 W=30"
        assert loaded[0].start_pos == (100.0, 100.0)
        assert loaded[0].click_pos == (405.0, 98.0)
        assert loaded[0].hit is True
        assert loaded[0].trial_index == 3
        assert loaded[0].repetition_index == 1
        assert loaded[0].trajectory == [[0.0, 100.0, 100.0], [0.3, 250.0, 99.0], [0.6, 405.0, 98.0]]


def test_logger_save_creates_output_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "nested" / "fitts_sessions"
        logger = FittsResultsLogger(output_dir=output_dir)
        logger.add_trial(_trial())
        filepath = logger.save("test.json")
        assert filepath.parent == output_dir
        assert filepath.exists()


def test_logger_save_honors_a_full_path_for_save_as():
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = FittsResultsLogger(output_dir=Path(tmpdir) / "default_dir")
        logger.add_trial(_trial())

        chosen_path = Path(tmpdir) / "elsewhere" / "my_results.json"
        filepath = logger.save(chosen_path)

        assert filepath == chosen_path
        assert filepath.exists()
