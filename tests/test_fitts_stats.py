"""Hand-computed checks for fitts_law's ISO 9241-411 math."""

from __future__ import annotations

import math

import pytest

from fitts_law.conditions import max_safe_circle_radius, multi_directional_sequence
from fitts_law.results import FittsStats, TrialResult


def _trial(start, target, click, mt, block_type="circle", label="A=100 W=30", repetition_index=0):
    return TrialResult(
        condition_label=label,
        block_type=block_type,
        amplitude_px=100.0,
        width_px=30.0,
        start_pos=start,
        target_pos=target,
        click_pos=click,
        start_time=0.0,
        end_time=mt,
        movement_time_s=mt,
        hit=True,
        repetition_index=repetition_index,
    )


def test_multi_directional_sequence_is_a_permutation_for_13():
    seq = multi_directional_sequence(13)
    assert seq == [0, 6, 12, 5, 11, 4, 10, 3, 9, 2, 8, 1, 7]
    assert sorted(seq) == list(range(13))


def test_multi_directional_sequence_is_a_permutation_for_9():
    seq = multi_directional_sequence(9)
    assert seq == [0, 4, 8, 3, 7, 2, 6, 1, 5]
    assert sorted(seq) == list(range(9))


def test_multi_directional_sequence_falls_back_for_even_n():
    # 6's only units mod 6 are 1 and 5, so it degrades to a sequential walk.
    assert multi_directional_sequence(6) == [0, 1, 2, 3, 4, 5]
    # 12: center=6 isn't coprime with 12, nearest coprime step is 5.
    assert multi_directional_sequence(12) == [0, 5, 10, 3, 8, 1, 6, 11, 4, 9, 2, 7]
    # 8: center=4 isn't coprime with 8, nearest coprime step is 3.
    assert multi_directional_sequence(8) == [0, 3, 6, 1, 4, 7, 2, 5]
    for n in (3, 6, 8, 9, 12, 13):
        assert sorted(multi_directional_sequence(n)) == list(range(n))


def test_effective_width_and_throughput_hand_computed():
    # Two trials along the x-axis from (0,0) to (100,0). Click errors along
    # the axis are +5 and -5, so the sample stdev (ddof=1) is 5*sqrt(2).
    trials = [
        _trial((0, 0), (100, 0), (105, 0), mt=0.5),
        _trial((0, 0), (100, 0), (95, 0), mt=0.7),
    ]

    expected_we = 4.133 * (5 * math.sqrt(2))
    assert FittsStats.effective_width(trials) == pytest.approx(expected_we, rel=1e-9)

    expected_ae = (105 + 95) / 2  # mean straight-line distance from start to click
    assert FittsStats.effective_amplitude(trials) == pytest.approx(expected_ae, rel=1e-9)

    expected_ide = math.log2(expected_ae / expected_we + 1)
    assert FittsStats.index_of_difficulty(expected_ae, expected_we) == pytest.approx(expected_ide, rel=1e-9)

    expected_tp = expected_ide / ((0.5 + 0.7) / 2)
    assert FittsStats.throughput_bits_per_s(trials) == pytest.approx(expected_tp, rel=1e-9)


def test_error_rate():
    hit = _trial((0, 0), (100, 0), (100, 0), mt=0.5)
    miss = TrialResult(**{**hit.__dict__, "hit": False})
    assert FittsStats.error_rate([hit, hit, miss]) == pytest.approx(1 / 3)


def test_summary_groups_by_block_type_and_condition():
    trials = [
        _trial((0, 0), (100, 0), (105, 0), mt=0.5, block_type="circle", label="A=100 W=30"),
        _trial((0, 0), (100, 0), (95, 0), mt=0.7, block_type="circle", label="A=100 W=30"),
        _trial((0, 0), (200, 0), (200, 0), mt=0.9, block_type="edge", label="edge W=30"),
    ]
    summary = FittsStats(trials).summary()
    assert summary["trial_count"] == 3
    assert set(summary["conditions"].keys()) == {"circle:A=100 W=30", "edge:edge W=30"}
    assert summary["conditions"]["circle:A=100 W=30"]["trial_count"] == 2


def test_summary_can_exclude_first_repetitions_per_condition():
    trials = [
        _trial((0, 0), (100, 0), (999, 999), mt=5.0, label="A=100 W=30", repetition_index=0),  # practice, wild miss
        _trial((0, 0), (100, 0), (105, 0), mt=0.5, label="A=100 W=30", repetition_index=1),
        _trial((0, 0), (100, 0), (95, 0), mt=0.7, label="A=100 W=30", repetition_index=1),
    ]

    full_summary = FittsStats(trials).summary()
    assert full_summary["conditions"]["circle:A=100 W=30"]["trial_count"] == 3

    filtered_summary = FittsStats(trials).summary(exclude_repetitions=1)
    condition = filtered_summary["conditions"]["circle:A=100 W=30"]
    assert condition["trial_count"] == 2
    assert filtered_summary["trial_count"] == 2


def test_max_safe_circle_radius_clamps_to_fit_screen():
    # 1920x1080 screen: a requested amplitude of 600 would put vertical
    # targets above y=0 (540 - 600 = -60), so it must be clamped below 540.
    safe = max_safe_circle_radius(1920, 1080, width_px=30, margin_px=20)
    assert safe < 540
    assert safe == pytest.approx(1080 / 2 - 30 / 2 - 20)

    # A small amplitude that already fits comfortably is left untouched by
    # the caller (max_safe_circle_radius only defines the ceiling).
    small_screen_safe = max_safe_circle_radius(400, 400, width_px=20, margin_px=10)
    assert small_screen_safe == pytest.approx(400 / 2 - 20 / 2 - 10)

    # Degenerate case: width/margin larger than the screen never go negative.
    assert max_safe_circle_radius(50, 50, width_px=100, margin_px=10) == 0.0
