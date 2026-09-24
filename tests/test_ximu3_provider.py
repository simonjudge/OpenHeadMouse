"""Tests for the xIMU3 provider's AHRS ignore-magnetometer toggle.

These avoid real hardware: the startup `_configure_*` helper is exercised
against a fake connection object, and the public runtime setter is checked
for both its happy path (delegates to the same helper) and its guard
against being called before a connection exists.
"""

import pytest

from providers.ximu3_provider import Ximu3Provider


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

    assert connection.sent_commands == ["ahrsIgnoreMagnetometer true"]


def test_configure_ahrs_ignore_magnetometer_false_sends_expected_command():
    provider = Ximu3Provider(ahrs_ignore_magnetometer=False)
    connection = _FakeConnection()

    provider._configure_ahrs_ignore_magnetometer(connection)

    assert connection.sent_commands == ["ahrsIgnoreMagnetometer false"]


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
    assert connection.sent_commands == ["ahrsIgnoreMagnetometer true"]
