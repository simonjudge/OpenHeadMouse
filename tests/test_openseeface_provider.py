"""Binary protocol, physical camera geometry and real UDP receiver tests."""

import logging
import copy
import queue
import socket
import struct
import time

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from core import quaternion_math as qm
from providers import openSeeFace
from providers.base_provider import ProviderState
from providers.openSeeFace import OpenSeeFaceProvider, _wire_quaternion_to_head


def _packet(timestamp=100.0, face_id=0, quaternion=None, got_3d=1):
    """Build upstream's packed 1785-byte record independently of receiver constants."""
    if quaternion is None:
        quaternion = [np.sqrt(0.5), -np.sqrt(0.5), 0.0, 0.0]
    packet = bytearray()
    packet.extend(struct.pack("<d", timestamp))
    packet.extend(struct.pack("<i", face_id))
    for value in [640.0, 480.0, 0.8, 0.9]:
        packet.extend(struct.pack("<f", value))
    packet.extend(struct.pack("<B", got_3d))
    for value in [2.0, *quaternion, *([0.0] * 6)]:
        packet.extend(struct.pack("<f", value))
    packet.extend(struct.pack("<68f", *([0.95] * 68)))
    packet.extend(bytes(68 * 2 * 4 + 70 * 3 * 4 + 14 * 4))
    assert len(packet) == 1785
    return bytes(packet)


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for the OpenSeeFace receiver")
        time.sleep(0.005)


def _projected_wire_pose(yaw, pitch, roll):
    """Solve upstream PnP from physical FLU points projected into a real camera.

    The source swaps image (u, v) to (v, u) before solvePnP and transmits
    the inverse of that rotation. No receiver conversion constants are used.
    """
    head_points = np.array([
        [0.0, 0.0, 0.0],
        [-0.08, 0.12, 0.02],
        [-0.08, -0.12, 0.02],
        [-0.06, 0.04, 0.08],
        [-0.06, -0.04, 0.08],
        [-0.04, 0.04, -0.07],
        [-0.04, -0.04, -0.07],
        [-0.10, 0.0, -0.12],
    ], dtype=np.float64)
    # An independent matrix construction for physical left/up/left-ear-down.
    rotation = Rotation.from_euler("ZYX", [yaw, -pitch, -roll], degrees=True)
    moved = rotation.apply(head_points)
    camera_points = np.column_stack([moved[:, 1], -moved[:, 2], 1.0 - moved[:, 0]])
    pixels = camera_points[:, :2] / camera_points[:, 2, None] * 640.0 + 320.0
    model_points = np.column_stack([-head_points[:, 1], head_points[:, 2], head_points[:, 0]])
    camera = np.array([[640.0, 0, 320.0], [0, 640.0, 320.0], [0, 0, 1.0]])
    success, rvec, _ = cv2.solvePnP(
        model_points, np.ascontiguousarray(pixels[:, ::-1]), camera, np.zeros(4)
    )
    assert success
    xyzw = Rotation.from_rotvec(rvec.ravel()).inv().as_quat()
    return xyzw, rotation


@pytest.mark.parametrize("angles", [
    (0, 0, 0), (20, 0, 0), (-20, 0, 0), (0, 20, 0),
    (0, -20, 0), (0, 0, 20), (0, 0, -20), (25, -15, 30),
    (-60, 40, -25),
])
def test_pose_conversion_matches_physical_camera_projection(angles):
    wire, expected_rotation = _projected_wire_pose(*angles)
    provider = OpenSeeFaceProvider()
    provider._process_packet(_packet(quaternion=wire))
    result = provider.output_queue.get_nowait().quaternion
    actual_rotation = Rotation.from_quat(result[[1, 2, 3, 0]])
    np.testing.assert_allclose(
        actual_rotation.as_matrix(), expected_rotation.as_matrix(), atol=1e-6
    )
    np.testing.assert_allclose(np.degrees(qm.to_euler_zyx(result)), angles, atol=1e-5)


def test_wire_quaternion_normalizes_and_handles_double_cover():
    wire, _ = _projected_wire_pose(20, 10, -5)
    positive = _wire_quaternion_to_head(wire * 2.0)
    negative = _wire_quaternion_to_head(-wire * 3.0)
    assert np.linalg.norm(positive) == pytest.approx(1.0)
    np.testing.assert_allclose(positive, -negative, atol=1e-10)


@pytest.mark.parametrize("quaternion", [
    [0, 0, 0, 0], [np.nan, 0, 0, 1], [0, np.inf, 0, 1], [0, 0, 1],
])
def test_invalid_quaternion_is_rejected(quaternion):
    with pytest.raises(ValueError, match="quaternion"):
        _wire_quaternion_to_head(np.array(quaternion))


def test_source_timestamp_and_metadata_are_preserved():
    provider = OpenSeeFaceProvider()
    provider._process_packet(_packet(timestamp=1234567890.125))
    sample = provider.output_queue.get_nowait()
    assert sample.timestamp == 1234567890.125
    assert sample.raw_metadata == {
        "source": "openseeface",
        "face_id": 0,
        "camera_resolution": [640.0, 480.0],
        "right_eye_open": pytest.approx(0.8),
        "left_eye_open": pytest.approx(0.9),
        "fit_3d_error": 2.0,
        "confidence": pytest.approx(0.95),
    }


def test_multiple_faces_selects_configured_id_without_face_hopping():
    provider = OpenSeeFaceProvider(face_id=7)
    provider._process_packet(_packet(face_id=0) + _packet(face_id=7))
    assert provider.output_queue.get_nowait().raw_metadata["face_id"] == 7
    assert provider.output_queue.empty()
    provider._process_packet(_packet(timestamp=101, face_id=0))
    assert provider.output_queue.empty()


@pytest.mark.parametrize("data", [b"", b"x", _packet()[:-1], _packet() + b"x"])
def test_malformed_lengths_are_rejected(data):
    with pytest.raises(ValueError, match="bytes"):
        OpenSeeFaceProvider()._process_packet(data)


@pytest.mark.parametrize("offset,fmt,value", [
    (0, "<d", np.nan), (12, "<f", -1.0), (16, "<f", np.inf),
    (20, "<f", np.nan), (28, "<B", 2), (29, "<f", -1.0),
    (33, "<f", np.nan), (73, "<f", np.nan),
])
def test_invalid_pose_fields_are_rejected(offset, fmt, value):
    data = bytearray(_packet())
    struct.pack_into(fmt, data, offset, value)
    with pytest.raises(ValueError):
        OpenSeeFaceProvider()._process_packet(bytes(data))


def test_invalid_second_record_does_not_emit_partial_datagram():
    provider = OpenSeeFaceProvider()
    with pytest.raises(ValueError, match="non-finite"):
        provider._process_packet(_packet() + _packet(timestamp=np.nan))
    assert provider.output_queue.empty()


def test_failed_fit_never_emits_zero_or_held_pose(monkeypatch):
    provider = OpenSeeFaceProvider(tracking_lost_hold_ms=500)
    now = 10.0
    monkeypatch.setattr(openSeeFace.time, "monotonic", lambda: now)
    provider._process_packet(_packet())
    provider.output_queue.get_nowait()
    now += 0.4
    provider._process_packet(_packet(timestamp=101, got_3d=0, quaternion=[0, 0, 0, 0]))
    assert provider.output_queue.empty()
    assert provider.state == ProviderState.RUNNING
    now += 0.11
    provider._update_tracking_state()
    assert provider.state == ProviderState.TRACKING_LOST
    provider._process_packet(_packet(timestamp=102))
    assert provider.state == ProviderState.RUNNING
    assert provider.output_queue.get_nowait().timestamp == 102


def test_duplicate_and_backwards_samples_do_not_refresh_tracking(monkeypatch, caplog):
    provider = OpenSeeFaceProvider()
    now = 10.0
    monkeypatch.setattr(openSeeFace.time, "monotonic", lambda: now)
    provider._process_packet(_packet())
    provider.output_queue.get_nowait()
    now += 1.0
    with caplog.at_level(logging.WARNING):
        provider._process_packet(_packet() + _packet(timestamp=99))
    assert provider.output_queue.empty()
    assert provider.state == ProviderState.TRACKING_LOST
    assert "backwards" in caplog.text
    provider._process_packet(_packet(timestamp=101))
    assert provider.state == ProviderState.RUNNING


def test_zero_hold_allows_valid_pose_then_immediate_loss():
    provider = OpenSeeFaceProvider(tracking_lost_hold_ms=0)
    provider._process_packet(_packet())
    assert provider.state == ProviderState.RUNNING
    provider._update_tracking_state()
    assert provider.state == ProviderState.TRACKING_LOST


@pytest.mark.parametrize("kwargs", [
    {"udp_host": ""}, {"udp_port": -1}, {"udp_port": 65536}, {"udp_port": 1.5},
    {"face_id": -1}, {"face_id": 0.5}, {"tracking_lost_hold_ms": -1},
    {"tracking_lost_hold_ms": np.nan}, {"queue_maxsize": 0},
])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        OpenSeeFaceProvider(**kwargs)


def test_real_udp_loss_recovery_stop_and_restart(caplog):
    provider = OpenSeeFaceProvider(udp_port=0, tracking_lost_hold_ms=60)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        with caplog.at_level(logging.WARNING):
            provider.start()
            try:
                _wait_until(lambda: provider._sock is not None)
                address = provider._sock.getsockname()
                assert provider.state == ProviderState.TRACKING_LOST
                sender.sendto(b"malformed", address)
                sender.sendto(_packet(timestamp=500), address)
                sample = provider.output_queue.get(timeout=3.0)
                assert sample.timestamp == 500
                assert provider.state == ProviderState.RUNNING
                _wait_until(lambda: provider.state == ProviderState.TRACKING_LOST)
                assert provider.output_queue.empty()
                sender.sendto(_packet(timestamp=501), address)
                assert provider.output_queue.get(timeout=3.0).timestamp == 501
                assert provider.state == ProviderState.RUNNING
            finally:
                provider.stop()
        assert "Discarding malformed OpenSeeFace packet" in caplog.text
        assert provider.state == ProviderState.STOPPED
        assert provider._sock is None
        assert not provider._thread.is_alive()
        # The old port is released and a restarted sender may use a lower clock.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(address)
        provider.start()
        try:
            _wait_until(lambda: provider._sock is not None)
            sender.sendto(_packet(timestamp=1), provider._sock.getsockname())
            assert provider.output_queue.get(timeout=3.0).timestamp == 1
        finally:
            provider.stop()


def test_socket_bind_failure_is_reported_and_not_swallowed(caplog):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        provider = OpenSeeFaceProvider(udp_port=occupied.getsockname()[1])
        with caplog.at_level(logging.ERROR), pytest.raises(OSError):
            provider._run_wrapper()
    assert provider.state == ProviderState.ERROR
    assert provider._sock is None
    assert "UDP receiver failed" in caplog.text


def test_factory_and_selection(qt_app):
    from gui.setup_gui import SetupPanel
    from main import ensure_mouse_profiles, make_provider

    config = {
        "active_provider": "uniface",
        "providers": {"openseeface": {
            "udp_host": "127.0.0.2", "udp_port": 12345,
            "face_id": 4, "tracking_lost_hold_ms": 250,
        }},
        "mouse_profiles": {"uniface": {"mode": "velocity"}},
    }
    provider = make_provider("openseeface", config)
    assert isinstance(provider, OpenSeeFaceProvider)
    assert (provider.udp_host, provider.udp_port, provider.face_id) == ("127.0.0.2", 12345, 4)
    assert provider.tracking_lost_hold_ms == 250
    profiles = ensure_mouse_profiles(config)
    assert profiles["openseeface"] == profiles["uniface"]
    assert profiles["openseeface"] is not profiles["uniface"]
    panel = SetupPanel(initial_provider="openseeface")
    seen = []
    panel.start_requested.connect(seen.append)
    panel._start_button.click()
    assert seen == ["openseeface"]
    panel.close()


def test_factory_works_without_new_settings_in_existing_user_config():
    from main import make_provider

    provider = make_provider("openseeface", {})
    assert provider.udp_host == "127.0.0.1"
    assert provider.udp_port == 11573
    assert provider.face_id == 0
    assert provider.tracking_lost_hold_ms == 500


def test_app_receives_udp_pose_and_displays_link_state(qt_app, tmp_path):
    from main import App, load_config

    class Mouse:
        position = (400.0, 400.0)

    config = copy.deepcopy(load_config())
    config["active_provider"] = "openseeface"
    config["providers"]["openseeface"].update(udp_port=0, tracking_lost_hold_ms=60)
    config["ui"].update(
        recording_enabled=False, mouse_control_enabled=False, calibration_enabled=False,
    )
    app = App(
        config, auto_start_listeners=False, mouse_controller=Mouse(),
        config_path=tmp_path / "config.json",
    )
    try:
        app.window.setup_panel._start_button.click()
        provider = app._provider
        assert isinstance(provider, OpenSeeFaceProvider)
        _wait_until(lambda: provider._sock is not None)
        app._poll()
        assert app.window.setup_panel._connection_status.text() == "OpenSeeFace: tracking_lost"
        wire, _ = _projected_wire_pose(20, 10, -5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(_packet(timestamp=123.25, quaternion=wire), provider._sock.getsockname())
        _wait_until(lambda: not provider.output_queue.empty())
        app._poll()
        assert app._last_sample.timestamp == 123.25
        assert app.window.setup_panel._connection_status.text() == "OpenSeeFace: running"
        np.testing.assert_allclose(
            app.dsp.relative_euler_deg(app._last_sample.quaternion), [20, 10, -5], atol=1e-5
        )
        _wait_until(lambda: provider.state == ProviderState.TRACKING_LOST)
        app._poll()
        assert app.window.setup_panel._connection_status.text() == "OpenSeeFace: tracking_lost"
        app.window.setup_panel._stop_button.click()
        assert app._provider is None
        assert not provider._thread.is_alive()
    finally:
        app.shutdown()


def test_queue_overflow_keeps_latest_pose():
    provider = OpenSeeFaceProvider(queue_maxsize=1)
    provider._process_packet(_packet(timestamp=100) + _packet(timestamp=101))
    assert provider.output_queue.get_nowait().timestamp == 101
    with pytest.raises(queue.Empty):
        provider.output_queue.get_nowait()
