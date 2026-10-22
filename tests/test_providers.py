"""Tests for providers/base_provider.py and providers/mock_provider.py."""

import queue
import time
from types import SimpleNamespace

import numpy as np
import pytest

from providers.base_provider import BaseProvider, ProviderSample, ProviderState
from providers.mock_provider import MockProvider


def test_mock_provider_emits_unit_quaternions():
    provider = MockProvider(rate_hz=100.0)
    samples = []
    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while len(samples) < 20:
            samples.append(provider.output_queue.get(timeout=max(0.0, deadline - time.monotonic())))
    finally:
        provider.stop()

    assert len(samples) == 20
    for s in samples:
        assert isinstance(s, ProviderSample)
        assert np.isclose(np.linalg.norm(s.quaternion), 1.0, atol=1e-6)


@pytest.mark.parametrize("rate_hz", [50.0, 100.0])
def test_mock_provider_uses_configured_sample_period(monkeypatch, rate_hz):
    provider = MockProvider(rate_hz=rate_hz)
    now = 0.0
    periods = []

    def sleep(period):
        nonlocal now
        periods.append(period)
        now += period
        if len(periods) == 3:
            provider._stop_event.set()

    monkeypatch.setattr(
        "providers.mock_provider.time",
        SimpleNamespace(monotonic=lambda: now, sleep=sleep),
    )
    provider._run()
    assert periods == [1.0 / rate_hz] * 3
    assert [provider.output_queue.get_nowait().timestamp for _ in periods] == pytest.approx(
        [0.0, 1.0 / rate_hz, 2.0 / rate_hz]
    )


def test_mock_provider_state_transitions():
    provider = MockProvider(rate_hz=50.0)
    assert provider.state == ProviderState.STOPPED
    provider.start()
    time.sleep(0.05)
    assert provider.state == ProviderState.RUNNING
    provider.stop()
    assert provider.state == ProviderState.STOPPED


class _CountingProvider(BaseProvider):
    """Minimal concrete provider for exercising the base class's queue overflow behavior."""

    def _run(self):
        i = 0
        while not self._stop_event.is_set():
            self._emit(ProviderSample(timestamp=float(i), quaternion=np.array([1.0, 0.0, 0.0, 0.0])))
            i += 1
            if i >= 10:
                break


def test_base_provider_drops_oldest_on_queue_overflow():
    provider = _CountingProvider(queue_maxsize=3)
    provider.start()
    provider.stop(timeout=2.0)

    remaining = []
    while True:
        try:
            remaining.append(provider.output_queue.get_nowait())
        except queue.Empty:
            break

    assert len(remaining) == 3
    # oldest samples (timestamps 0..6) should have been dropped; last 3 (7,8,9) retained
    assert [s.timestamp for s in remaining] == [7.0, 8.0, 9.0]
