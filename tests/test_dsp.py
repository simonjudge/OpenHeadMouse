"""Unit tests for core/dsp_engine.py (Mode A: absolute mapping)."""

import numpy as np
import pytest

from core import quaternion_math as qm
from core.dsp_engine import DSPEngine

SCREEN_W = 1920
SCREEN_H = 1080


def make_engine(kx=8.0, ky=-8.0):
    return DSPEngine(kx=kx, ky=ky, screen_width=SCREEN_W, screen_height=SCREEN_H)


def test_neutral_pose_maps_to_screen_center():
    engine = make_engine()
    px, py = engine.process_absolute(qm.IDENTITY_QUATERNION)
    assert px == pytest.approx(SCREEN_W / 2.0)
    assert py == pytest.approx(SCREEN_H / 2.0)


def test_yaw_only_maps_to_expected_pixel_x():
    engine = make_engine(kx=8.0, ky=-8.0)
    q = qm.from_euler_zyx(np.radians(10.0), 0.0, 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0 + 8.0 * 10.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0, abs=1e-6)


def test_pitch_only_maps_to_expected_pixel_y():
    engine = make_engine(kx=8.0, ky=-8.0)
    q = qm.from_euler_zyx(0.0, np.radians(5.0), 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0, abs=1e-6)
    # ky is negative, pitch is positive (looking up) -> pixel_y should decrease (toward screen top)
    assert py == pytest.approx(SCREEN_H / 2.0 - 8.0 * 5.0, abs=1e-6)


def test_combined_yaw_and_pitch():
    engine = make_engine(kx=8.0, ky=-8.0)
    q = qm.from_euler_zyx(np.radians(-15.0), np.radians(10.0), 0.0)
    px, py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W / 2.0 + 8.0 * -15.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0 - 8.0 * 10.0, abs=1e-6)


def test_large_yaw_clamps_to_right_edge():
    engine = make_engine(kx=50.0, ky=-8.0)
    q = qm.from_euler_zyx(np.radians(30.0), 0.0, 0.0)  # 50*30=1500 >> half-screen; must clamp
    px, _py = engine.process_absolute(q)
    assert px == pytest.approx(SCREEN_W - 1)


def test_large_negative_yaw_clamps_to_left_edge():
    engine = make_engine(kx=50.0, ky=-8.0)
    q = qm.from_euler_zyx(np.radians(-30.0), 0.0, 0.0)
    px, _py = engine.process_absolute(q)
    assert px == pytest.approx(0.0)


def test_recenter_makes_current_pose_the_new_center():
    engine = make_engine()
    q_offset = qm.from_euler_zyx(np.radians(20.0), np.radians(-8.0), 0.0)
    engine.recenter(q_offset)
    px, py = engine.process_absolute(q_offset)
    assert px == pytest.approx(SCREEN_W / 2.0, abs=1e-6)
    assert py == pytest.approx(SCREEN_H / 2.0, abs=1e-6)


def test_recenter_shifts_subsequent_readings_relative_to_new_neutral():
    engine = make_engine(kx=8.0, ky=-8.0)
    q_offset = qm.from_euler_zyx(np.radians(20.0), 0.0, 0.0)
    engine.recenter(q_offset)

    q_current = qm.from_euler_zyx(np.radians(25.0), 0.0, 0.0)
    px, _py = engine.process_absolute(q_current)
    # only the 5 degree difference from the new neutral should register
    assert px == pytest.approx(SCREEN_W / 2.0 + 8.0 * 5.0, abs=1e-6)


def test_set_screen_geometry_updates_center():
    engine = make_engine()
    engine.set_screen_geometry(1280, 720)
    px, py = engine.process_absolute(qm.IDENTITY_QUATERNION)
    assert px == pytest.approx(640.0)
    assert py == pytest.approx(360.0)


def test_from_config_reads_absolute_gains():
    config = {"absolute": {"kx": 5.0, "ky": -3.0}}
    engine = DSPEngine.from_config(config, screen_width=800, screen_height=600)
    assert engine.kx == 5.0
    assert engine.ky == -3.0
    assert engine.screen_width == 800
    assert engine.screen_height == 600
