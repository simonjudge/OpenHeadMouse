"""BLE provider for a Seeed XIAO nRF52840 Sense running the bundled firmware.

The board streams timestamped accelerometer and gyroscope readings. This adapter
estimates orientation with a six-axis complementary filter and emits quaternions
in OpenHeadMouse's canonical head frame.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import time

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

_LOGGER = logging.getLogger(__name__)
_PACKET = struct.Struct("<Ihhhhhh")
_TICK_MODULUS = 1 << 32
_TICK_HALF_RANGE = 1 << 31


class XiaoBleProvider(BaseProvider):
    """Receive sensor notifications from XIAO Sense firmware over BLE."""

    def __init__(
        self,
        device_name: str | None = None,
        service_uuid: str = "b28a0001-7f2d-4c9a-9b23-1d43c6e8a501",
        characteristic_uuid: str = "b28a0002-7f2d-4c9a-9b23-1d43c6e8a501",
        device_to_head: list[list[float]] | np.ndarray | None = None,
        sample_timeout_s: float = 1.0,
        scan_timeout_s: float = 2.0,
        queue_maxsize: int = 256,
    ):
        if device_name is not None and (not isinstance(device_name, str) or not device_name):
            raise ValueError("device_name must be a non-empty string or None")
        for label, value in (("service_uuid", service_uuid), ("characteristic_uuid", characteristic_uuid)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} must be a non-empty string")
        for label, value in (("sample_timeout_s", sample_timeout_s), ("scan_timeout_s", scan_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and positive")

        super().__init__(queue_maxsize=queue_maxsize)
        self.device_name = device_name
        self.service_uuid = service_uuid.lower()
        self.characteristic_uuid = characteristic_uuid.lower()
        self.device_to_head = np.asarray(
            np.eye(3) if device_to_head is None else device_to_head, dtype=np.float64
        )
        if (
            self.device_to_head.shape != (3, 3)
            or not np.allclose(self.device_to_head @ self.device_to_head.T, np.eye(3))
            or not np.isclose(np.linalg.det(self.device_to_head), 1.0)
        ):
            raise ValueError("device_to_head must be a proper 3x3 rotation matrix")
        self.sample_timeout_s = float(sample_timeout_s)
        self.scan_timeout_s = float(scan_timeout_s)
        self._connection_status = "stopped"
        self._connection_error = ""
        self._last_sample_received_at: float | None = None
        self._last_sensor_tick: int | None = None
        self._tick_wraps = 0
        self._clock_offset: float | None = None
        self._last_emitted_timestamp: float | None = None
        self._last_filter_timestamp: float | None = None
        self._orientation = np.array([1.0, 0.0, 0.0, 0.0])

    @property
    def has_received_sample(self) -> bool:
        return self._last_sample_received_at is not None

    @property
    def status_text(self) -> str:
        if self.state == ProviderState.ERROR:
            return f"error: {self._connection_error or 'see log'}"
        if self.state == ProviderState.STOPPED:
            return "stopped"
        if self.state == ProviderState.RUNNING:
            return "tracking"
        if self._connection_status == "connected":
            return "connected; waiting for sensor data"
        return self._connection_status

    @property
    def connection_error(self) -> str:
        return self._connection_error

    def _run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self._connection_error = str(exc)
            raise
        finally:
            self._connection_status = "stopped"

    def stop(self, timeout: float = 2.0) -> None:
        timeout = max(timeout, self.scan_timeout_s + 3.0)
        super().stop(timeout=timeout)
        if self._thread is not None and self._thread.is_alive():
            self._state = ProviderState.ERROR
            raise RuntimeError("Timed out stopping the XIAO BLE provider")

    async def _run_async(self) -> None:
        from bleak import BleakClient, BleakScanner

        retry_delay = 0.5
        self._state = ProviderState.TRACKING_LOST
        while not self._stop_event.is_set():
            self._connection_status = "scanning"
            self._state = ProviderState.TRACKING_LOST
            try:
                device = await BleakScanner.find_device_by_filter(
                    self._matches_device,
                    timeout=self.scan_timeout_s,
                )
                if device is None:
                    _LOGGER.info("No XIAO BLE sensor found; scanning again")
                    await self._wait_before_retry(retry_delay)
                    retry_delay = min(retry_delay * 2.0, 5.0)
                    continue

                self._connection_status = f"connecting to {device.name or device.address}"
                async with BleakClient(device, timeout=5.0) as client:
                    await client.start_notify(self.characteristic_uuid, self._notification_received)
                    self._connection_status = "connected"
                    self._state = ProviderState.TRACKING_LOST
                    retry_delay = 0.5
                    _LOGGER.info("Connected to XIAO BLE sensor %s", device.address)

                    while not self._stop_event.is_set() and client.is_connected:
                        await asyncio.sleep(0.1)
                        received_at = self._last_sample_received_at
                        if received_at is None or time.monotonic() - received_at > self.sample_timeout_s:
                            if self._state != ProviderState.TRACKING_LOST:
                                _LOGGER.warning("XIAO BLE sensor data timed out")
                            self._state = ProviderState.TRACKING_LOST

                if not self._stop_event.is_set():
                    _LOGGER.warning("XIAO BLE connection closed; reconnecting")
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                _LOGGER.warning("XIAO BLE connection attempt failed: %s", exc)
                self._connection_status = f"reconnecting ({exc})"
                self._state = ProviderState.TRACKING_LOST

            if not self._stop_event.is_set():
                await self._wait_before_retry(retry_delay)
                retry_delay = min(retry_delay * 2.0, 5.0)

    def _matches_device(self, device, advertisement) -> bool:
        service_matches = any(
            uuid.lower() == self.service_uuid for uuid in (advertisement.service_uuids or [])
        )
        name_matches = self.device_name is None or device.name == self.device_name
        return service_matches and name_matches

    async def _wait_before_retry(self, delay: float) -> None:
        end_time = time.monotonic() + delay
        while not self._stop_event.is_set() and time.monotonic() < end_time:
            await asyncio.sleep(min(0.1, end_time - time.monotonic()))

    def _notification_received(self, _sender, data: bytearray) -> None:
        try:
            self._process_packet(data)
        except ValueError as exc:
            _LOGGER.warning("Discarding invalid XIAO BLE sensor packet: %s", exc)

    def _process_packet(self, data: bytes, received_at: float | None = None) -> ProviderSample | None:
        """Parse one firmware packet, update orientation, and enqueue its sample."""
        if len(data) != _PACKET.size:
            raise ValueError(f"expected {_PACKET.size} bytes, got {len(data)}")

        tick, ax, ay, az, gx, gy, gz = _PACKET.unpack(data)
        received_at = time.monotonic() if received_at is None else float(received_at)
        if not math.isfinite(received_at):
            raise ValueError("receive time must be finite")

        if self._last_sensor_tick is None:
            self._tick_wraps = 0
        elif tick < self._last_sensor_tick:
            backwards = self._last_sensor_tick - tick
            if backwards > _TICK_HALF_RANGE:
                self._tick_wraps += 1
            else:
                self._tick_wraps = 0
                self._clock_offset = max(
                    received_at,
                    (self._last_emitted_timestamp or received_at) + 1e-6,
                ) - tick / 1_000_000.0
                self._last_filter_timestamp = None
                self._orientation = np.array([1.0, 0.0, 0.0, 0.0])
        elif tick == self._last_sensor_tick:
            return None

        unwrapped_tick = tick + self._tick_wraps * _TICK_MODULUS
        if self._clock_offset is None:
            self._clock_offset = received_at - unwrapped_tick / 1_000_000.0
        timestamp = self._clock_offset + unwrapped_tick / 1_000_000.0
        if self._last_emitted_timestamp is not None and timestamp <= self._last_emitted_timestamp:
            return None

        accel_g = np.array([ax, ay, az], dtype=np.float64) / 1000.0
        gyro_rad_s = np.radians(np.array([gx, gy, gz], dtype=np.float64) / 100.0)
        accel_norm = float(np.linalg.norm(accel_g))
        if not math.isfinite(accel_norm) or accel_norm < 1e-6:
            raise ValueError("accelerometer vector is invalid")

        if self._last_filter_timestamp is None:
            self._orientation = self._orientation_from_gravity(accel_g / accel_norm)
        else:
            dt = timestamp - self._last_filter_timestamp
            if dt > 0.25:
                self._orientation = self._orientation_from_gravity(accel_g / accel_norm)
            else:
                self._integrate_orientation(gyro_rad_s, accel_g / accel_norm, dt, accel_norm)

        self._last_sensor_tick = tick
        self._last_filter_timestamp = timestamp
        self._last_emitted_timestamp = timestamp
        self._last_sample_received_at = received_at
        self._connection_status = "connected"
        self._state = ProviderState.RUNNING

        quaternion = qm.change_basis(self._orientation, self.device_to_head)
        sample = ProviderSample(
            timestamp=timestamp,
            quaternion=quaternion,
            raw_metadata={
                "device": "xiao_ble",
                "timestamp_source": "sensor_micros",
                "accel_g": accel_g.tolist(),
                "gyro_rad_s": gyro_rad_s.tolist(),
            },
        )
        self._emit(sample)
        return sample

    @staticmethod
    def _orientation_from_gravity(accel_unit: np.ndarray) -> np.ndarray:
        """Initialize body-to-world orientation by aligning measured up with world up."""
        target = np.array([0.0, 0.0, 1.0])
        cross = np.cross(accel_unit, target)
        quaternion = np.concatenate(([1.0 + float(np.dot(accel_unit, target))], cross))
        if quaternion[0] < 1e-8:
            quaternion = np.array([0.0, 1.0, 0.0, 0.0])
        return qm.normalize(quaternion)

    def _integrate_orientation(
        self, gyro_rad_s: np.ndarray, accel_unit: np.ndarray, dt: float, accel_norm: float
    ) -> None:
        if dt <= 0:
            return

        w, x, y, z = self._orientation
        predicted_up = np.array([
            2.0 * (x * z - w * y),
            2.0 * (y * z + w * x),
            1.0 - 2.0 * (x * x + y * y),
        ])
        if 0.75 <= accel_norm <= 1.25:
            gyro_rad_s = gyro_rad_s + np.cross(accel_unit, predicted_up)

        gx, gy, gz = gyro_rad_s
        derivative = 0.5 * np.array([
            -x * gx - y * gy - z * gz,
            w * gx + y * gz - z * gy,
            w * gy - x * gz + z * gx,
            w * gz + x * gy - y * gx,
        ])
        self._orientation = qm.normalize(self._orientation + derivative * dt)
