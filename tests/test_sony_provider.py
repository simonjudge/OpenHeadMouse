"""Sony v2 JSON, frame conversion, UDP lifecycle and application integration."""

import copy
import json
import logging
import socket
import time

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from core import quaternion_math as qm
from providers import sony_provider
from providers.base_provider import ProviderSample, ProviderState
from providers.sony_provider import SonyProvider


def _packet(quaternion=None, **fields):
    payload = {
        "version": 2,
        "device": "WH-1000XM5",
        "quaternion": [1, 0, 0, 0] if quaternion is None else list(quaternion),
        "rotationVector": [0, 0, 0],
        "yprDegrees": [0, 0, 0],
        "gyroscope": None,
        "accelerometer": None,
        "resetCounter": 0,
        "packetsPerSecond": 25,
        "receiveLatencyMs": -1,
    }
    payload.update(fields)
    return json.dumps(payload).encode()


def _wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for Sony receiver")
        time.sleep(0.005)


@pytest.mark.parametrize("angles", [
    (0, 0, 0), (20, 0, 0), (-20, 0, 0), (0, 20, 0), (0, -20, 0),
    (0, 0, 20), (0, 0, -20), (25, -15, 30), (-60, 40, -25),
])
def test_pose_conversion_matches_independent_physical_rotation(angles):
    yaw, pitch, roll = angles
    physical = Rotation.from_euler("ZYX", [yaw, -pitch, -roll], degrees=True)
    # Head axes (forward, left, up) expressed in the neutral Android frame.
    android = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    wire = Rotation.from_matrix(android @ physical.as_matrix() @ android.T).as_quat()
    provider = SonyProvider()
    provider._process_packet(_packet(wire[[3, 0, 1, 2]]))
    sample = provider.output_queue.get_nowait()
    assert isinstance(sample, ProviderSample)
    np.testing.assert_allclose(np.degrees(qm.to_euler_zyx(sample.quaternion)), angles, atol=1e-6)


def test_receive_timestamp_and_metadata_are_explicit(monkeypatch):
    monkeypatch.setattr(sony_provider.time, "monotonic", lambda: 123.25)
    provider = SonyProvider()
    provider._process_packet(_packet(timestamp=999, extra_future_field="ignored"))
    sample = provider.output_queue.get_nowait()
    assert sample.timestamp == 123.25
    assert sample.raw_metadata == {
        "source": "sony", "device": "WH-1000XM5", "bridge_version": 2,
        "timestamp_source": "host_monotonic_receive",
    }
    assert provider.state == ProviderState.RUNNING


@pytest.mark.parametrize("scale", [2, -3, 1e300, 1e-300])
def test_normalization_and_double_cover(scale):
    provider = SonyProvider()
    provider._process_packet(_packet([scale, 0, 0, scale]))
    actual = provider.output_queue.get_nowait().quaternion
    assert np.linalg.norm(actual) == pytest.approx(1)
    np.testing.assert_allclose(abs(actual), [np.sqrt(0.5), 0, 0, np.sqrt(0.5)])


@pytest.mark.parametrize("packet", [
    b"", b"\xff", b"not json", b"null", b"[]", b'{"version":2}',
    _packet(version=1), _packet(version=3), _packet(version=True), _packet(version=2.0),
    _packet([0, 0, 0, 0]), _packet([1, 2, 3]), _packet([1, 2, 3, 4, 5]),
    _packet([np.nan, 0, 0, 1]), _packet([1, np.inf, 0, 0]),
    _packet(["1", 0, 0, 0]), _packet([True, 0, 0, 0]),
    _packet([None, 0, 0, 0]), _packet([10**400, 0, 0, 1]), _packet(device=123),
    json.dumps({"version": 2, "quaternion": {"w": 1, "x": 0, "y": 0, "z": 0}}).encode(),
])
def test_invalid_packets_do_not_emit_or_refresh_tracking(packet):
    provider = SonyProvider()
    with pytest.raises(ValueError):
        provider._process_packet(packet)
    assert provider.output_queue.empty()
    assert provider._last_timestamp is None


def test_missing_device_label_is_supported():
    provider = SonyProvider()
    provider._process_packet(b'{"version":2,"quaternion":[1,0,0,0]}')
    assert provider.output_queue.get_nowait().raw_metadata["device"] is None


def test_hold_threshold_and_recovery_use_real_receive_time(monkeypatch):
    now = 10.0
    monkeypatch.setattr(sony_provider.time, "monotonic", lambda: now)
    provider = SonyProvider(tracking_lost_hold_ms=500)
    provider._process_packet(_packet())
    now = 10.499
    provider._update_tracking_state()
    assert provider.state == ProviderState.RUNNING
    now = 10.5
    provider._update_tracking_state()
    assert provider.state == ProviderState.TRACKING_LOST
    provider._process_packet(_packet())
    assert provider.state == ProviderState.RUNNING


def test_queue_overflow_and_non_increasing_timestamps(caplog):
    provider = SonyProvider(queue_maxsize=1)
    provider._process_packet(_packet(), 1)
    provider._process_packet(_packet(), 2)
    with caplog.at_level(logging.WARNING):
        provider._process_packet(_packet(), 2)
        provider._process_packet(_packet(), 1)
    assert provider.output_queue.get_nowait().timestamp == 2
    assert provider.output_queue.empty()
    assert "non-increasing" in caplog.text


@pytest.mark.parametrize("kwargs", [
    {"udp_host": ""}, {"udp_host": None}, {"udp_port": -1}, {"udp_port": 65536},
    {"udp_port": 1.5}, {"udp_port": True}, {"tracking_lost_hold_ms": 0},
    {"tracking_lost_hold_ms": -1}, {"tracking_lost_hold_ms": np.nan},
    {"tracking_lost_hold_ms": np.inf}, {"queue_maxsize": 0},
    {"auto_start_bridge": "yes"}, {"startup_timeout_s": 0},
    {"startup_timeout_s": np.nan}, {"startup_timeout_s": True},
    {"udp_host": "127.0.0.2"}, {"udp_port": 0}, {"udp_port": 1},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        SonyProvider(**kwargs)


def test_udp_loss_malformed_packet_recovery_stop_and_restart(caplog):
    provider = SonyProvider(udp_port=0, tracking_lost_hold_ms=100, auto_start_bridge=False)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        with caplog.at_level(logging.WARNING):
            provider.start()
            try:
                _wait_until(lambda: provider._sock is not None)
                address = provider._sock.getsockname()
                assert provider.state == ProviderState.TRACKING_LOST
                before = time.monotonic()
                sender.sendto(b"malformed", address)
                sender.sendto(_packet(), address)
                sample = provider.output_queue.get(timeout=3)
                assert before <= sample.timestamp <= time.monotonic()
                assert provider.state == ProviderState.RUNNING
                _wait_until(lambda: provider.state == ProviderState.TRACKING_LOST)
                assert provider.output_queue.empty()
                sender.sendto(_packet(), address)
                recovered = provider.output_queue.get(timeout=3)
                assert recovered.timestamp > sample.timestamp
                assert provider.state == ProviderState.RUNNING
            finally:
                provider.stop()
        assert "Discarding malformed Sony packet" in caplog.text
        assert provider.state == ProviderState.STOPPED
        assert provider._sock is None
        assert not provider._thread.is_alive()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(address)
        provider._emit(sample)
        provider.start()
        try:
            _wait_until(lambda: provider._sock is not None)
            assert provider.output_queue.empty()
            sender.sendto(_packet(), provider._sock.getsockname())
            assert provider.output_queue.get(timeout=3).timestamp > recovered.timestamp
        finally:
            provider.stop()


def test_bind_failure_is_visible(caplog):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        provider = SonyProvider(udp_port=occupied.getsockname()[1])
        with caplog.at_level(logging.ERROR):
            provider._run_wrapper()
    assert provider.state == ProviderState.ERROR
    assert provider._sock is None
    assert "Sony provider failed" in caplog.text
    assert provider.status_text.startswith("error: cannot listen for Sony JSON")


def test_factory_selection_and_independent_profile(qt_app):
    from gui.setup_gui import SetupPanel
    from main import ensure_mouse_profiles, make_provider

    config = {
        "active_provider": "uniface",
        "providers": {"sony": {"udp_host": "127.0.0.2", "udp_port": 12345,
                               "tracking_lost_hold_ms": 250, "auto_start_bridge": False,
                               "startup_timeout_s": 30}},
        "mouse_profiles": {"uniface": {"mode": "absolute", "absolute": {"kx": 10}}},
    }
    provider = make_provider("sony", config)
    assert isinstance(provider, SonyProvider)
    assert (provider.udp_host, provider.udp_port) == ("127.0.0.2", 12345)
    assert provider.tracking_lost_hold_ms == 250
    assert not provider.auto_start_bridge
    assert provider.startup_timeout_s == 30
    default = make_provider("sony", {})
    assert (default.udp_host, default.udp_port) == ("127.0.0.1", 4243)
    assert default.auto_start_bridge
    profiles = ensure_mouse_profiles(config)
    profiles["sony"]["absolute"]["kx"] = 20
    assert profiles["uniface"]["absolute"]["kx"] == 10
    panel = SetupPanel(initial_provider="sony")
    try:
        seen = []
        panel.start_requested.connect(seen.append)
        panel._start_button.click()
        assert seen == ["sony"]
    finally:
        panel.close()


def test_app_receives_pose_reports_loss_and_stops(qt_app, tmp_path):
    from main import App, load_config

    class Mouse:
        position = (400.0, 400.0)

    config = copy.deepcopy(load_config())
    config["active_provider"] = "sony"
    config["providers"]["sony"].update(udp_port=0, tracking_lost_hold_ms=100,
                                       auto_start_bridge=False)
    config["ui"].update(recording_enabled=False, mouse_control_enabled=False,
                        calibration_enabled=False)
    app = App(config, auto_start_listeners=False, mouse_controller=Mouse(),
              config_path=tmp_path / "config.json")
    try:
        app.window.setup_panel._start_button.click()
        provider = app._provider
        assert isinstance(provider, SonyProvider)
        _wait_until(lambda: provider._sock is not None)
        app._poll()
        assert "waiting for Sony Head Tracker" in app.window.setup_panel._connection_status.text()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(_packet([np.cos(0.2), 0, 0, np.sin(0.2)]),
                          provider._sock.getsockname())
        _wait_until(lambda: not provider.output_queue.empty())
        app._poll()
        np.testing.assert_allclose(
            app.dsp.relative_euler_deg(app._last_sample.quaternion), [np.degrees(0.4), 0, 0],
            atol=1e-6,
        )
        assert "tracking (receive timestamps)" in app.window.setup_panel._connection_status.text()
        _wait_until(lambda: provider.state == ProviderState.TRACKING_LOST)
        app._poll()
        assert app._last_sample is None
        assert not app._cursor_driven
        app.window.setup_panel._stop_button.click()
        assert app._provider is None
        assert not provider._thread.is_alive()
    finally:
        app.shutdown()


class _BridgeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode


@pytest.fixture
def sony_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_managed_bridge_command_valid_tracking_and_cleanup(monkeypatch, tmp_path, sony_port):
    executable = tmp_path / "sony-head-tracker.exe"
    monkeypatch.setattr(sony_provider, "ensure_bridge", lambda: executable)
    process = _BridgeProcess()
    calls = []

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(sony_provider.subprocess, "Popen", launch)
    provider = SonyProvider(udp_port=sony_port)
    provider.start()
    try:
        _wait_until(lambda: provider._bridge_process is not None)
        assert not provider.has_received_sample
        assert provider.state == ProviderState.TRACKING_LOST
        assert calls[0][0] == [
            str(executable), "bridge", "--port", str(sony_port - 1),
            "--axis-map", "XYZ", "--invert", "none", "--smoothing", "1",
        ]
        assert calls[0][1]["stdout"] is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(_packet(device="WF-1000XM5"), provider._sock.getsockname())
        assert provider.output_queue.get(timeout=3).raw_metadata["device"] == "WF-1000XM5"
        assert provider.has_received_sample
        assert provider.state == ProviderState.RUNNING
    finally:
        provider.stop()
    assert process.terminated
    assert provider._bridge_process is None


def test_managed_bridge_exit_reports_output_and_repair_hint(monkeypatch, sony_port):
    monkeypatch.setattr(sony_provider, "ensure_bridge", lambda: "bridge")
    process = _BridgeProcess()

    def launch(command, **kwargs):
        kwargs["stdout"].write(b"No verified tracker found")
        kwargs["stdout"].flush()
        process.returncode = 2
        return process

    monkeypatch.setattr(sony_provider.subprocess, "Popen", launch)
    provider = SonyProvider(udp_port=sony_port)
    provider.start()
    try:
        _wait_until(lambda: provider.state == ProviderState.ERROR)
        assert "code 2" in provider.connection_error
        assert "No verified tracker found" in provider.connection_error
        assert "Repair Tracker" in provider.connection_error
    finally:
        provider.stop()


def test_invalid_packets_cannot_satisfy_startup_check():
    provider = SonyProvider(udp_port=0, auto_start_bridge=False, startup_timeout_s=0.1)
    provider.start()
    try:
        _wait_until(lambda: provider._sock is not None)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"not JSON", provider._sock.getsockname())
        _wait_until(lambda: provider.state == ProviderState.ERROR)
        assert not provider.has_received_sample
        assert "within 0.1 seconds" in provider.connection_error
        assert "v2 JSON" in provider.connection_error
    finally:
        provider.stop()


def test_dependency_failure_is_reported(monkeypatch, sony_port):
    def fail():
        raise OSError("Dependency download failed")

    monkeypatch.setattr(sony_provider, "ensure_bridge", fail)
    provider = SonyProvider(udp_port=sony_port)
    provider.start()
    try:
        _wait_until(lambda: provider.state == ProviderState.ERROR)
        assert "Dependency download failed" in provider.connection_error
    finally:
        provider.stop()


def test_managed_startup_timeout_stops_owned_process(monkeypatch, sony_port):
    monkeypatch.setattr(sony_provider, "ensure_bridge", lambda: "bridge")
    process = _BridgeProcess()
    monkeypatch.setattr(sony_provider.subprocess, "Popen", lambda *a, **k: process)
    provider = SonyProvider(udp_port=sony_port, startup_timeout_s=0.1)
    provider.start()
    try:
        _wait_until(lambda: provider._thread is not None and not provider._thread.is_alive())
        assert provider.state == ProviderState.ERROR
        assert process.terminated
        assert provider._sock is None
        assert provider._bridge_process is None
    finally:
        provider.stop()


def test_stop_during_dependency_setup_does_not_orphan_thread(monkeypatch, sony_port):
    import threading

    entered = threading.Event()
    release = threading.Event()

    def prepare():
        entered.set()
        assert release.wait(timeout=3)
        return "bridge"

    monkeypatch.setattr(sony_provider, "ensure_bridge", prepare)
    monkeypatch.setattr(sony_provider.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not launch after Stop"))
    provider = SonyProvider(udp_port=sony_port)
    provider.start()
    try:
        assert entered.wait(timeout=3)
        with pytest.raises(RuntimeError, match="still finishing"):
            provider.stop(timeout=0.01)
        assert provider._thread.is_alive()
    finally:
        release.set()
        provider.stop()
    assert not provider._thread.is_alive()
    assert provider._sock is None


@pytest.mark.parametrize("receive_sample", [False, True])
def test_app_waits_before_calibration_and_reports_failed_start(
    qt_app, tmp_path, monkeypatch, receive_sample,
):
    from core.headmouse import TrackingState
    from main import App, load_config

    class Mouse:
        position = (400.0, 400.0)

    config = copy.deepcopy(load_config())
    config["active_provider"] = "sony"
    config["providers"]["sony"].update(
        udp_port=0, auto_start_bridge=False, startup_timeout_s=0.2,
    )
    config["ui"].update(recording_enabled=False, mouse_control_enabled=False,
                        calibration_enabled=True)
    messages = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical",
                        lambda *args: messages.append(args))
    app = App(config, auto_start_listeners=False, mouse_controller=Mouse(),
              config_path=tmp_path / "config.json")
    ready = []
    try:
        assert app.start_head_tracking(ready.append, mouse_control=False, recording=False)
        provider = app._provider
        _wait_until(lambda: provider._sock is not None)
        app._poll()
        assert not app._calibrating
        assert app.headmouse.state != TrackingState.ACTIVE
        assert ready == []
        if receive_sample:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(_packet(), provider._sock.getsockname())
            _wait_until(lambda: provider.has_received_sample)
            app._poll()
            assert app._calibrating
            assert not app._sony_waiting_to_start
            assert messages == []
        else:
            _wait_until(lambda: provider.state == ProviderState.ERROR)
            app._poll()
            assert app._provider is None
            assert app.window.setup_panel._start_button.isEnabled()
            assert ready == [False]
            assert len(messages) == 1
            assert messages[0][1] == "Sony Connection Failed"
            assert "Repair Tracker" in messages[0][2]
            app._poll()
            assert len(messages) == 1
    finally:
        app.shutdown()
