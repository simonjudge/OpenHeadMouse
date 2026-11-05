"""Focused tests for provider behavior that differs across operating systems."""

import pytest

from providers.airpods import airpods_provider


class _FakeSocket:
    def __init__(self, *, bind_error=None):
        self.options = []
        self.bind_error = bind_error
        self.closed = False

    def setsockopt(self, level, option, value):
        self.options.append((level, option, value))

    def bind(self, address):
        if self.bind_error:
            raise self.bind_error

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True


def test_airpods_udp_socket_uses_exclusive_bind_on_windows(monkeypatch):
    exclusive_option = 0x1234
    fake_socket = _FakeSocket()
    monkeypatch.setattr(airpods_provider.os, "name", "nt")
    monkeypatch.setattr(
        airpods_provider.socket, "SO_EXCLUSIVEADDRUSE", exclusive_option, raising=False
    )
    monkeypatch.setattr(airpods_provider.socket, "socket", lambda *_: fake_socket)
    provider = airpods_provider.AirPodsProvider(udp_port=0)
    provider._stop_event.set()

    provider._run()

    assert fake_socket.options == [
        (airpods_provider.socket.SOL_SOCKET, exclusive_option, 1)
    ]
    assert fake_socket.closed


def test_airpods_bind_failure_preserves_original_socket_error(monkeypatch):
    bind_error = OSError("address already in use")
    fake_socket = _FakeSocket(bind_error=bind_error)
    monkeypatch.setattr(airpods_provider.socket, "socket", lambda *_: fake_socket)
    provider = airpods_provider.AirPodsProvider(udp_port=0)

    with pytest.raises(OSError) as exc_info:
        provider._run()

    assert exc_info.value is bind_error
    assert fake_socket.closed
