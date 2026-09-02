"""Synthetic provider for tests and CI without hardware.

Generates a deterministic sinusoidal head-motion pattern (yaw and pitch
oscillation) at a configurable rate, using the same ProviderSample contract
as real hardware providers. Not part of the top-level spec's provider table
(which only enumerates hardware sources); this exists purely as test/CI
scaffolding so core/dsp_engine.py, core/headmouse.py, and the GUI can be
exercised end-to-end without any physical device.
"""

from __future__ import annotations

import time

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample


class MockProvider(BaseProvider):
    def __init__(
        self,
        rate_hz: float = 100.0,
        yaw_amplitude_deg: float = 20.0,
        pitch_amplitude_deg: float = 10.0,
        frequency_hz: float = 0.25,
        queue_maxsize: int = 256,
    ):
        super().__init__(queue_maxsize=queue_maxsize)
        self.rate_hz = rate_hz
        self.yaw_amplitude = qm.np.radians(yaw_amplitude_deg)
        self.pitch_amplitude = qm.np.radians(pitch_amplitude_deg)
        self.frequency_hz = frequency_hz

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        t0 = time.monotonic()
        while not self._stop_event.is_set():
            t = time.monotonic() - t0
            omega = 2 * qm.np.pi * self.frequency_hz
            yaw = self.yaw_amplitude * qm.np.sin(omega * t)
            pitch = self.pitch_amplitude * qm.np.sin(omega * t * 0.5)
            q = qm.from_euler_zyx(yaw, pitch, 0.0)
            self._emit(ProviderSample(timestamp=time.monotonic(), quaternion=q, raw_metadata={"synthetic": True}))
            time.sleep(period)
