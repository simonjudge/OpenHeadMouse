"""Tests for xIMU3 timestamps, Wi-Fi reconnection, and AHRS configuration.

These use synthetic SDK messages and fake connections instead of real hardware.
"""

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from core import quaternion_math as qm
from core.dsp_engine import DSPEngine
from core.recorder import Recorder
from providers import ximu3_provider
from providers.base_provider import ProviderSample, ProviderState
from providers.ximu3_provider import Ximu3Provider, discover_ximu3_devices


class _FakeConnection:
    def __init__(self):
        self.sent_commands = []

    def send_command(self, cmd):
        self.sent_commands.append(cmd)

    def get_statistics(self):
        return object()


def test_ahrs_ignore_magnetometer_defaults_to_false():
    provider = Ximu3Provider()
    assert provider.ahrs_ignore_magnetometer is False


def test_configure_ahrs_ignore_magnetometer_sends_expected_command():
    provider = Ximu3Provider(ahrs_ignore_magnetometer=True)
    connection = _FakeConnection()

    provider._configure_ahrs_ignore_magnetometer(connection)

    assert connection.sent_commands == ['{"ahrsIgnoreMagnetometer": true}']


def test_configure_ahrs_ignore_magnetometer_false_sends_expected_command():
    provider = Ximu3Provider(ahrs_ignore_magnetometer=False)
    connection = _FakeConnection()

    provider._configure_ahrs_ignore_magnetometer(connection)

    assert connection.sent_commands == ['{"ahrsIgnoreMagnetometer": false}']


def test_set_ahrs_ignore_magnetometer_raises_when_not_connected():
    provider = Ximu3Provider()

    with pytest.raises(RuntimeError, match="not connected"):
        provider.set_ahrs_ignore_magnetometer(True)


def test_set_ahrs_ignore_magnetometer_updates_live_connection():
    provider = Ximu3Provider(ahrs_ignore_magnetometer=False)
    connection = _FakeConnection()
    provider._connection = connection

    provider.set_ahrs_ignore_magnetometer(True)

    assert provider.ahrs_ignore_magnetometer is True
    assert connection.sent_commands == ['{"ahrsIgnoreMagnetometer": true}']


@pytest.mark.parametrize("connection_type", ["usb", "wifi_ap"])
def test_quaternion_timestamps_are_seconds_on_both_connections(connection_type, tmp_path):
    provider = Ximu3Provider(connection_type=connection_type)
    if connection_type == "wifi_ap":
        provider._connected_generation = provider._configured_generation = 1
        provider._link_status = "waiting for data"
    for timestamp, yaw in ((1_000_000, 0.0), (1_019_960, 1.0)):
        q = qm.from_euler_zyx(np.radians(yaw), 0.0, 0.0)
        provider._on_quaternion(SimpleNamespace(timestamp=timestamp, w=q[0], x=q[1], y=q[2], z=q[3]))
    first = provider.output_queue.get_nowait()
    second = provider.output_queue.get_nowait()
    assert first.timestamp == pytest.approx(1.0)
    assert second.timestamp - first.timestamp == pytest.approx(0.01996)
    assert provider.output_queue.empty()
    recorder = Recorder(output_dir=tmp_path)
    for sample in (first, second):
        recorder.record_sample(sample.timestamp, sample.quaternion, raw_metadata=sample.raw_metadata)
    recorded = json.loads(recorder.save("ximu.json").read_text())["samples"]
    assert [sample["timestamp"] for sample in recorded] == pytest.approx([1.0, 1.01996])


def test_quaternion_without_device_timestamp_uses_monotonic_seconds(monkeypatch):
    monkeypatch.setattr(ximu3_provider.time, "monotonic", lambda: 42.5)
    provider = Ximu3Provider()
    provider._on_quaternion(SimpleNamespace(w=1.0, x=0.0, y=0.0, z=0.0))
    assert provider.output_queue.get_nowait().timestamp == pytest.approx(42.5)


def test_ximu_velocity_matches_other_second_timestamped_providers():
    provider = Ximu3Provider()
    poses = [
        qm.from_euler_zyx(np.radians(yaw), 0.0, 0.0)
        for yaw in (0.0, 1.0, 2.0)
    ]
    for timestamp, q in zip((1_000_000, 1_050_000, 1_100_000), poses):
        provider._on_quaternion(SimpleNamespace(timestamp=timestamp, w=q[0], x=q[1], y=q[2], z=q[3]))
    ximu_samples = [provider.output_queue.get_nowait() for _ in poses]
    other_samples = [ProviderSample(10.0 + i * 0.05, q) for i, q in enumerate(poses)]

    def cursor_x(samples):
        engine = DSPEngine(
            0, 0, 1920, 1080, mode="velocity",
            velocity_kx=2, gamma=1.1, deadband_deg_s=1,
        )
        for sample in samples:
            position = engine.process(sample.quaternion, timestamp=sample.timestamp)
        return position[0]

    expected = cursor_x(other_samples)
    assert expected < 960.0
    assert cursor_x(ximu_samples) == pytest.approx(expected)


class _FakeUdpConnection:
    """Mimics the ximu3 1.9 Connection callback/status surface used by the provider."""

    def __init__(self, config):
        self.config = config
        self.commands = []
        self.callback = None
        self.status_callback = None
        self.status = ximu3_provider.ximu3.CONNECTION_STATUS_DISCONNECTED
        self.closed = False

    def add_quaternion_callback(self, callback):
        self.callback = callback
        return 1

    def add_status_callback(self, callback):
        self.status_callback = callback
        return 2

    def remove_callback(self, callback_id):
        assert callback_id in (1, 2)

    def get_status(self):
        return self.status

    def set_status(self, status):
        self.status = status
        self.status_callback(status)

    def ping(self):
        return object()

    def send_command(self, command):
        self.commands.append(command)
        return type("Response", (), {"error": ""})()

    def close(self):
        self.closed = True

    def emit(self):
        self.callback(type("Quaternion", (), dict(w=1, x=0, y=0, z=0, timestamp=1.0))())


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_discovery_uses_advertised_host_udp_ports(monkeypatch):
    class Scanner:
        @staticmethod
        def scan():
            return []

    class Announcement:
        ip_address = "192.168.1.1"
        device_name = "Head IMU"
        udp_send = 8001
        udp_receive = 9000

        def to_udp_connection_config(self):
            return ximu3_provider.ximu3.UdpConnectionConfig(self.ip_address, 9000, 8001)

    class Network:
        def get_messages(self):
            return [Announcement()]

    monkeypatch.setattr(ximu3_provider.ximu3, "PortScanner", Scanner)
    monkeypatch.setattr(ximu3_provider.ximu3, "NetworkAnnouncement", Network)
    monkeypatch.setattr(ximu3_provider.time, "sleep", lambda _: None)
    devices = discover_ximu3_devices()
    wifi = [device for device in devices if device.connection_type == "wifi_ap"]
    assert len(wifi) == 1
    assert (wifi[0].port, wifi[0].receive_port) == (9000, 8001)


def test_wifi_reconnect_reconfigures_and_drops_stale_samples(monkeypatch):
    Connection = _FakeUdpConnection

    class KeepOpen:
        def __init__(self, connection):
            self.callback = connection.set_status
            self.callback(ximu3_provider.ximu3.CONNECTION_STATUS_CONNECTED)

    monkeypatch.setattr(ximu3_provider.ximu3, "Connection", Connection)
    monkeypatch.setattr(ximu3_provider.ximu3, "KeepOpen", KeepOpen)
    provider = Ximu3Provider(connection_type="wifi_ap", wifi_port_send=9033, wifi_port_receive=8033)
    provider.start()
    try:
        _wait_until(lambda: provider.link_status == "waiting for data")
        connection = provider._connection
        assert (connection.config.send_port, connection.config.receive_port) == (9033, 8033)
        assert connection.commands == [
            '{"axesAlignment": 1}', '{"ahrsAxesConvention": 2}',
            '{"ahrsIgnoreMagnetometer": false}', '{"apply": null}',
        ]
        connection.emit()
        _wait_until(lambda: provider.link_status == "streaming")
        assert provider.state == ProviderState.RUNNING
        with provider._link_lock:
            provider._last_data_time = time.monotonic() - 4.0
        _wait_until(lambda: provider.link_status == "waiting for data")
        assert provider.output_queue.empty()
        connection.emit()
        _wait_until(lambda: provider.link_status == "streaming")

        provider._keep_open.callback(ximu3_provider.ximu3.CONNECTION_STATUS_DISCONNECTED)
        assert provider.state == ProviderState.TRACKING_LOST
        assert provider.output_queue.empty()
        connection.emit()
        assert provider.output_queue.empty()
        provider._keep_open.callback(ximu3_provider.ximu3.CONNECTION_STATUS_CONNECTED)
        _wait_until(lambda: len(connection.commands) == 8)
        connection.emit()
        _wait_until(lambda: provider.link_status == "streaming")
        assert provider.output_queue.qsize() == 1
    finally:
        provider.stop()
    assert connection.closed
    assert provider.link_status == "stopped"


def test_wifi_rejected_command_is_visible_error(monkeypatch):
    class Connection(_FakeUdpConnection):
        def send_command(self, command):
            return type("Response", (), {"error": "invalid value"})()

    class KeepOpen:
        def __init__(self, connection):
            connection.set_status(ximu3_provider.ximu3.CONNECTION_STATUS_CONNECTED)

    monkeypatch.setattr(ximu3_provider.ximu3, "Connection", Connection)
    monkeypatch.setattr(ximu3_provider.ximu3, "KeepOpen", KeepOpen)
    provider = Ximu3Provider(connection_type="wifi_ap")
    with pytest.raises(ValueError, match="invalid value"):
        provider._run_wifi()
    assert "invalid value" in provider.connection_error


def test_wifi_stop_interrupts_reconnect_wait(monkeypatch):
    Connection = _FakeUdpConnection

    class KeepOpen:
        def __init__(self, connection):
            connection.set_status(ximu3_provider.ximu3.CONNECTION_STATUS_DISCONNECTED)

    monkeypatch.setattr(ximu3_provider.ximu3, "Connection", Connection)
    monkeypatch.setattr(ximu3_provider.ximu3, "KeepOpen", KeepOpen)
    provider = Ximu3Provider(connection_type="wifi_ap")
    provider.start()
    _wait_until(lambda: provider._keep_open is not None)
    connection = provider._connection
    started = time.monotonic()
    provider.stop()
    assert time.monotonic() - started < 1.0
    assert not provider._thread.is_alive()
    assert connection.closed
