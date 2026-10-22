"""Unit tests for core/dsp_engine.py.

Convention: positive gains make the cursor follow the head in both modes
(yaw left/+ -> cursor left, pitch up/+ -> cursor up).
"""

import numpy as np
import pytest

from core import quaternion_math as qm
from core.dsp_engine import AutoZeroConfig, DSPEngine, OneEuroConfig, YawDriftConfig

SCREEN_W = 1920
SCREEN_H = 1080


def make_engine(kx=8.0, ky=8.0):
    return DSPEngine(kx=kx, ky=ky, screen_width=SCREEN_W, screen_height=SCREEN_H)


def test_neutral_pose_maps_to_screen_center():
    engine = make_engine()
    px, py = engine.process_absolute(qm.IDENTITY_QUATERNION)
    assert px == pytest.approx(SCREEN_W / 2.0)
    assert py == pytest.approx(SCREEN_H / 2.0)


def test_yaw_only_maps_to_expected_pixel_x():
    engine = make_engine(kx=8.0, ky=8.0)
    q = qm.from_euler_zyx(np.radians(10.0), 0.0, 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0 - 8.0 * 10.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0, abs=1e-6)


def test_pitch_only_maps_to_expected_pixel_y():
    engine = make_engine(kx=8.0, ky=8.0)
    q = qm.from_euler_zyx(0.0, np.radians(5.0), 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0, abs=1e-6)
    # pitch is positive (looking up) -> pixel_y should decrease (toward screen top)
    assert py == pytest.approx(SCREEN_H / 2.0 - 8.0 * 5.0, abs=1e-6)


def test_combined_yaw_and_pitch():
    engine = make_engine(kx=8.0, ky=8.0)
    q = qm.from_euler_zyx(np.radians(-15.0), np.radians(10.0), 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0 + 8.0 * 15.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0 - 8.0 * 10.0, abs=1e-6)


def test_large_left_yaw_clamps_to_left_edge():
    engine = make_engine(kx=50.0, ky=8.0)
    q = qm.from_euler_zyx(np.radians(30.0), 0.0, 0.0)  # 50*30=1500 >> half-screen; must clamp
    px, _py = engine.process_absolute(q)
    assert px == pytest.approx(0.0)


def test_large_right_yaw_clamps_to_right_edge():
    engine = make_engine(kx=50.0, ky=8.0)
    q = qm.from_euler_zyx(np.radians(-30.0), 0.0, 0.0)
    px, _py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W - 1)


def test_recenter_makes_current_pose_the_new_center():
    engine = make_engine()
    q_offset = qm.from_euler_zyx(np.radians(20.0), np.radians(-8.0), 0.0)
    engine.recenter(q_offset)
    px, py = engine.process_absolute(q_offset)
    assert px == pytest.approx(SCREEN_W / 2.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0, abs=1e-6)


def test_recenter_shifts_subsequent_readings_relative_to_new_neutral():
    engine = make_engine(kx=8.0, ky=8.0)
    q_offset = qm.from_euler_zyx(np.radians(20.0), 0.0, 0.0)
    engine.recenter(q_offset)

    q_current = qm.from_euler_zyx(np.radians(25.0), 0.0, 0.0)
    px, _py = engine.process_absolute(q_current)
    # only the 5 degree difference from the new neutral should register
    assert px == pytest.approx(SCREEN_W / 2.0 - 8.0 * 5.0, abs=1e-6)


def test_set_screen_geometry_updates_center():
    engine = make_engine()
    engine.set_screen_geometry(1280, 720)
    px, py = engine.process_absolute(qm.IDENTITY_QUATERNION)
    assert px == pytest.approx(640.0)
    assert py == pytest.approx(360.0)


def test_screen_origin_is_included_in_absolute_mapping():
    engine = DSPEngine(kx=8.0, ky=8.0, screen_width=1280, screen_height=720, origin_x=-1280, origin_y=0)
    px, py = engine.process_absolute(qm.IDENTITY_QUATERNION)
    assert px == pytest.approx(-640.0)
    assert py == pytest.approx(360.0)


def test_from_config_reads_absolute_gains():
    config = {"absolute": {"kx": 5.0, "ky": 3.0}}
    engine = DSPEngine.from_config(config, screen_width=800, screen_height=600)
    assert engine.kx == 5.0
    assert engine.ky == 3.0
    assert engine.screen_width == 800
    assert engine.screen_height == 600


def test_velocity_mode_integrates_deadband_and_gamma():
    engine = DSPEngine(0, 0, SCREEN_W, SCREEN_H, mode="velocity",
                       velocity_kx=2, velocity_ky=1, deadband_deg_s=2, gamma=2)
    engine.process_velocity(qm.IDENTITY_QUATERNION, dt=0.1)
    q = qm.from_euler_zyx(np.radians(1.0), 0, 0)  # 10 deg/s -> 8 * (1 + 8)
    px, py = engine.process_velocity(q, dt=0.1)
    assert px == pytest.approx(SCREEN_W / 2 - 2 * 72 * 0.1)
    assert py == pytest.approx(SCREEN_H / 2)


def test_velocity_deadband_suppresses_small_motion():
    engine = DSPEngine(0, 0, SCREEN_W, SCREEN_H, mode="velocity", deadband_deg_s=3)
    engine.process_velocity(qm.IDENTITY_QUATERNION, dt=0.1)
    px, _ = engine.process_velocity(qm.from_euler_zyx(np.radians(.2), 0, 0), dt=0.1)
    assert px == pytest.approx(SCREEN_W / 2)


def test_from_config_reads_velocity_and_filter_settings():
    config = {"mode": "velocity", "absolute": {"kx": 8, "ky": 8},
              "velocity": {"kx": 2, "ky": 3, "gamma": 1.4, "deadband_deg_s": 2},
              "one_euro_filter": {"enabled": True, "min_cutoff": 1, "beta": .2, "d_cutoff": 1}}
    engine = DSPEngine.from_config(config, SCREEN_W, SCREEN_H)
    assert (engine.mode, engine.velocity_kx, engine.velocity_ky) == ("velocity", 2, 3)
    assert engine.one_euro_filter.enabled and engine.gamma == pytest.approx(1.4)


def test_one_euro_filter_reduces_a_single_velocity_spike():
    raw = DSPEngine(0, 0, SCREEN_W, SCREEN_H, mode="velocity")
    filtered = DSPEngine(0, 0, SCREEN_W, SCREEN_H, mode="velocity",
                         one_euro_filter=OneEuroConfig(True, 1, 0, 1))
    for engine in (raw, filtered):
        engine.process_velocity(qm.IDENTITY_QUATERNION, dt=.01)
    q = qm.from_euler_zyx(np.radians(10), 0, 0)
    raw_x, _ = raw.process_velocity(q, dt=.01)
    filtered_x, _ = filtered.process_velocity(q, dt=.01)
    center = SCREEN_W / 2.0
    assert center - filtered_x < center - raw_x


def test_one_euro_filter_reduces_absolute_stationary_jitter():
    raw = make_engine(kx=20.0)
    filtered = DSPEngine(
        20.0,
        8.0,
        SCREEN_W,
        SCREEN_H,
        one_euro_filter=OneEuroConfig(True, min_cutoff=0.5, beta=0.0, d_cutoff=1.0),
    )
    noise_deg = [0.0, 0.4, -0.3, 0.35, -0.25, 0.3, -0.2, 0.25, -0.15]
    raw_x = []
    filtered_x = []
    for index, yaw in enumerate(noise_deg):
        q = qm.from_euler_zyx(np.radians(yaw), 0.0, 0.0)
        timestamp = index / 60.0
        raw_x.append(raw.process(q, timestamp=timestamp)[0])
        filtered_x.append(filtered.process(q, timestamp=timestamp)[0])

    assert np.ptp(filtered_x[2:]) < np.ptp(raw_x[2:])
    assert np.std(filtered_x[2:]) < np.std(raw_x[2:])


def test_absolute_one_euro_beta_improves_fast_step_response():
    fixed = DSPEngine(
        10.0,
        8.0,
        SCREEN_W,
        SCREEN_H,
        one_euro_filter=OneEuroConfig(True, min_cutoff=0.5, beta=0.0, d_cutoff=1.0),
    )
    adaptive = DSPEngine(
        10.0,
        8.0,
        SCREEN_W,
        SCREEN_H,
        one_euro_filter=OneEuroConfig(True, min_cutoff=0.5, beta=0.8, d_cutoff=1.0),
    )
    for engine in (fixed, adaptive):
        engine.process(qm.IDENTITY_QUATERNION, timestamp=0.0)
    step = qm.from_euler_zyx(np.radians(10.0), 0.0, 0.0)
    fixed_x, _ = fixed.process(step, timestamp=1.0 / 60.0)
    adaptive_x, _ = adaptive.process(step, timestamp=1.0 / 60.0)

    center = SCREEN_W / 2.0
    assert center - adaptive_x > center - fixed_x


def test_disabled_absolute_filter_preserves_direct_mapping_with_timestamp():
    engine = make_engine(kx=8.0, ky=8.0)
    q = qm.from_euler_zyx(np.radians(5.0), np.radians(3.0), 0.0)
    assert engine.process(q, timestamp=123.0) == pytest.approx(engine.process_absolute(q))


def test_absolute_and_velocity_filter_histories_are_independent():
    engine = DSPEngine(
        10.0,
        8.0,
        SCREEN_W,
        SCREEN_H,
        one_euro_filter=OneEuroConfig(True, min_cutoff=0.5, beta=0.0, d_cutoff=1.0),
    )
    engine.process_absolute(qm.from_euler_zyx(np.radians(8.0), 0.0, 0.0), timestamp=0.0)
    engine.mode = "velocity"
    position = engine.process(qm.IDENTITY_QUATERNION, timestamp=1.0)
    assert position == pytest.approx((SCREEN_W / 2.0, SCREEN_H / 2.0))


def test_stationary_auto_zero_and_yaw_drift_options_are_safe():
    engine = DSPEngine(0, 0, SCREEN_W, SCREEN_H, mode="velocity",
                       auto_zero=AutoZeroConfig(True, 100, 2),
                       yaw_drift_correction=YawDriftConfig(True, 1))
    q = qm.from_euler_zyx(np.radians(10), 0, 0)
    engine.process_velocity(q, dt=.01)
    first = engine.process_velocity(q, dt=.1)
    for _ in range(5):
        last = engine.process_velocity(q, dt=.1)
    assert last == pytest.approx(first)


def test_zero_deadband_allows_slow_motion_when_corrections_disabled():
    engine = DSPEngine(1, 1, SCREEN_W, SCREEN_H, mode="velocity")
    engine.process(qm.IDENTITY_QUATERNION, timestamp=0)
    pose = qm.from_euler_zyx(np.radians(1), np.radians(1), 0)
    assert engine.process(pose, timestamp=1) == pytest.approx(
        (SCREEN_W / 2 - 1, SCREEN_H / 2 - 1)
    )


@pytest.mark.parametrize("correction", ["auto_zero", "yaw_drift", "both"])
def test_velocity_corrections_reduce_sustained_drift_without_reverse_motion(correction):
    engine = DSPEngine(
        1, -1, SCREEN_W, SCREEN_H, mode="velocity",
        auto_zero=AutoZeroConfig(correction in ("auto_zero", "both"), 200, 2),
        yaw_drift_correction=YawDriftConfig(correction in ("yaw_drift", "both"), 0.5),
    )
    baseline = DSPEngine(1, 1, SCREEN_W, SCREEN_H, mode="velocity")
    traces = [[], []]
    for i in range(101):
        # 1 degree/second sensor drift around an arbitrary nonzero posture.
        pose = qm.from_euler_zyx(np.radians(30 + i * 0.1), 0, 0)
        for trace, dsp in zip(traces, (engine, baseline)):
            trace.append(dsp.process(pose, timestamp=i * 0.1)[0])
    corrected, raw = traces
    # Positive yaw drift moves the cursor left (negative x); corrections must shrink it
    # without producing rightward (reverse) motion.
    assert corrected[0] - corrected[-1] < (raw[0] - raw[-1]) * 0.2
    assert np.max(np.diff(corrected)) <= 1e-9
    before = engine.process(pose, timestamp=10.1)
    assert engine.process(pose, timestamp=10.2) == pytest.approx(before)


def test_filter_reconfiguration_preserves_integrated_cursor_position():
    engine = DSPEngine(1, 1, SCREEN_W, SCREEN_H, mode="velocity")
    engine.process(qm.IDENTITY_QUATERNION, timestamp=0)
    pose = qm.from_euler_zyx(np.radians(10), 0, 0)
    position = engine.process(pose, timestamp=1)
    engine.set_filter_configs(OneEuroConfig(True))
    assert engine.process(pose, timestamp=2) == pytest.approx(position)


@pytest.mark.parametrize("mode", ["absolute", "velocity"])
def test_positive_gains_make_cursor_follow_head_in_both_modes(mode):
    engine = DSPEngine(10.0, 10.0, SCREEN_W, SCREEN_H, mode=mode, velocity_kx=10.0, velocity_ky=10.0)
    engine.process(qm.IDENTITY_QUATERNION, dt=0.1)
    left_up = qm.from_euler_zyx(np.radians(5.0), np.radians(5.0), 0.0)  # turn left, look up
    px, py = engine.process(left_up, dt=0.1)
    assert px < SCREEN_W / 2.0
    assert py < SCREEN_H / 2.0
