"""xIMU3 IMU provider (spec section 4, direct Python SDK integration).

Connects to an xIMU3 IMU device (USB or WiFi AP mode; Bluetooth excluded per
spec), auto-configures the hardware `axesAlignment` and AHRS axes convention based
on the configured reference frame,
and streams quaternions at up to 400 Hz.

COORDINATE FRAME & AXES ALIGNMENT LOGIC:
=========================================

Device body frame (configured on the hardware):
  +X: Forward (direction of head gaze, out of nose)
  +Y: Right (toward right ear)
  +Z: Down (toward neck/floor)
  This is the right-handed NED/FRD convention (AHRS axes convention 2).
  Quaternions are converted in software to the project's canonical head
  frame (+X forward, +Y left, +Z up; see COORDINATE_FRAMES.md) by a 180°
  rotation about X: q_head = [w, x, -y, -z].

xIMU3 Physical Axes:
  The device has three axes printed with arrows: X, Y, Z

Determining axes_alignment:
  Hold the xIMU3 exactly how it will be worn on the head. Then ask three questions:

  1. **Which printed arrow points Forward (out of the nose)?**
     - Set that as your **Logical X**
     - If the arrow points backward, use the negative version (e.g., -X)

  2. **Which printed arrow points to the Right (toward the right ear)?**
     - Set that as your **Logical Y**
     - If the arrow points toward the left ear, use the negative version (e.g., -Y)

  3. **Which printed arrow points Down (toward the neck/floor)?**
     - Set that as your **Logical Z**
     - If the arrow points up toward the sky, use the negative version (e.g., -Z)

Example: Left Temple Mounting (USB Pointing Backward)
  Hold the sensor flat against the left side of the head with USB pointing back:

  1. Physical +X arrow points Forward ✓ → Logical X = +X
  2. Physical +Z arrow points out of the head (from the left ear) ✓
     → Logical Y = -Z
  3. Physical +Y arrow points Down ✓ → Logical Z = +Y

  Result: axes_alignment = "+X-Z+Y"

The xIMU3 SDK returns quaternions in this FRD body frame; ``_on_quaternion``
converts them to the canonical head frame.

Reference: xIMU3 User Manual v1.11, axes alignment command documentation.
"""

from __future__ import annotations

import json
import queue
import time
from dataclasses import dataclass
from threading import Lock

import numpy as np
import ximu3

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

# Rows are the canonical head axes (forward, left, up) in the device's FRD body frame.
_XIMU3_TO_HEAD_FRAME = np.diag([1.0, 1.0, 1.0])

# FusionAxesAlignment enum values used by the "axesAlignment" device setting
# (x-IMU3 App DeviceSettingsEnums.xml).
_AXES_ALIGNMENT_VALUES = {
    name: value
    for value, name in enumerate((
        "+X+Y+Z", "+X-Z+Y", "+X-Y-Z", "+X+Z-Y", "-X+Y-Z", "-X+Z+Y", "-X-Y+Z", "-X-Z-Y",
        "+Y-X+Z", "+Y-Z-X", "+Y+X-Z", "+Y+Z+X", "-Y+X+Z", "-Y-Z+X", "-Y-X-Z", "-Y+Z-X",
        "+Z+Y-X", "+Z+X+Y", "+Z-Y+X", "+Z-X-Y", "-Z+Y+X", "-Z-X+Y", "-Z-Y-X", "-Z+X-Y",
    ))
}


def _command(key: str, value) -> str:
    """Encode a device command; the x-IMU3 protocol only accepts single-key JSON objects."""
    return json.dumps({key: value})


def _axes_alignment_value(alignment: str) -> int:
    try:
        return _AXES_ALIGNMENT_VALUES[alignment.replace(" ", "").upper()]
    except KeyError:
        raise ValueError(f"Unsupported xIMU3 axes alignment '{alignment}'") from None


@dataclass
class DiscoveredDevice:
    """A discovered xIMU3 device available for connection."""
    name: str
    connection_type: str  # "usb" or "wifi_ap"
    port_name: str = ""  # for USB
    ip_address: str = ""  # for WiFi
    port: int = 7331  # for WiFi
    receive_port: int = 8001


def discover_ximu3_devices(
    timeout_seconds: float = 2.0, errors: list[str] | None = None
) -> list[DiscoveredDevice]:
    """Scan for connected xIMU3 devices on USB and WiFi.

    Args:
        timeout_seconds: How long to wait for WiFi device announcements (default 2.0s)

    Returns:
        List of DiscoveredDevice objects representing available devices.
    """
    devices = []

    # Scan USB ports using PortScanner
    try:
        for device in ximu3.PortScanner.scan():
            config = device.connection_config
            if isinstance(config, ximu3.UsbConnectionConfig) and config.port_name:
                devices.append(DiscoveredDevice(
                    name=f"{device.device_name} (USB: {config.port_name})",
                    connection_type="usb",
                    port_name=config.port_name,
                ))
    except Exception as e:
        print(f"Warning: PortScanner scan failed: {e}")
        if errors is not None:
            errors.append(f"USB scan: {e}")

    # Manual USB port scanning for macOS (in case PortScanner misses them)
    # On macOS, USB serial devices appear as /dev/tty.usbmodem*, /dev/cu.usbmodem*, etc.
    try:
        import glob
        import os

        # Check common macOS USB patterns
        for pattern in ['/dev/tty.usbmodem*', '/dev/cu.usbmodem*', '/dev/tty.usbserial*', '/dev/cu.usbserial*']:
            for port in glob.glob(pattern):
                # Check if this port is already in our list
                if not any(d.port_name == port for d in devices):
                    # Try to identify as xIMU3
                    device_name = 'x-IMU3' if 'usbmodem' in port else 'USB Device'
                    devices.append(DiscoveredDevice(
                        name=f"{device_name} ({port})",
                        connection_type="usb",
                        port_name=port
                    ))
    except Exception as e:
        print(f"Warning: macOS USB port scan failed: {e}")
        if errors is not None:
            errors.append(f"USB port scan: {e}")

    # Scan WiFi using NetworkAnnouncement with improved timing
    try:
        import time
        announcer = ximu3.NetworkAnnouncement()

        # Wait for announcements with longer timeout for reliability
        time.sleep(timeout_seconds)

        # Get any received announcements
        announcements = announcer.get_messages()

        seen_ips = set()
        for announcement in announcements:
            # Each announcement should have device info
            ip_address = announcement.ip_address if hasattr(announcement, 'ip_address') else None

            if ip_address and ip_address not in seen_ips and announcement.udp_send and announcement.udp_receive:
                seen_ips.add(ip_address)
                device_name = announcement.device_name if hasattr(announcement, 'device_name') else "xIMU3"

                udp_config = announcement.to_udp_connection_config()
                if udp_config is None:
                    continue
                devices.append(DiscoveredDevice(
                    name=f"{device_name} (WiFi: {ip_address})",
                    connection_type="wifi_ap",
                    ip_address=ip_address,
                    port=udp_config.send_port,
                    receive_port=udp_config.receive_port,
                ))
    except Exception as e:
        print(f"Warning: WiFi scan failed: {e}")
        if errors is not None:
            errors.append(f"Wi-Fi announcement scan: {e}")

    return devices


class Ximu3Provider(BaseProvider):
    """xIMU3 IMU provider with auto-configuration of axes alignment."""

    def __init__(
        self,
        connection_type: str = "usb",  # "usb" or "wifi_ap"
        port_name: str = "",  # USB port name (e.g., "/dev/ttyUSB0", "COM3")
        wifi_ip_address: str = "192.168.1.1",  # WiFi AP IP address
        wifi_port_send: int = 9000,  # WiFi AP send port
        wifi_port_receive: int = 8001,  # WiFi AP receive port
        axes_alignment: str = "+X-Z+Y",
        ahrs_axes_convention: int = 2,
        ahrs_ignore_magnetometer: bool = False,
        target_rate_hz: int = 200,
        queue_maxsize: int = 256,
    ):
        super().__init__(queue_maxsize=queue_maxsize)
        self.connection_type = connection_type
        self.port_name = port_name
        self.wifi_ip_address = wifi_ip_address
        self.wifi_port_send = wifi_port_send
        self.wifi_port_receive = wifi_port_receive
        self.axes_alignment = axes_alignment
        _axes_alignment_value(axes_alignment)
        self.ahrs_axes_convention = ahrs_axes_convention
        self.ahrs_ignore_magnetometer = ahrs_ignore_magnetometer
        self.target_rate_hz = target_rate_hz
        self._connection = None
        self._keep_open = None
        self._link_lock = Lock()
        self._link_status = "connecting"
        self._connected_generation = 0
        self._configured_generation = -1
        self._last_data_time: float | None = None
        self._connection_error: str | None = None

    @property
    def link_status(self) -> str:
        with self._link_lock:
            return self._link_status

    @property
    def connection_error(self) -> str | None:
        with self._link_lock:
            return self._connection_error

    def _set_link_status(self, status: str, error: str | None = None) -> None:
        with self._link_lock:
            self._link_status = status
            self._connection_error = error

    def _on_connection_status(self, status: int) -> None:
        with self._link_lock:
            if status == ximu3.CONNECTION_STATUS_CONNECTED:
                self._connected_generation += 1
                self._link_status = "configuring"
            else:
                self._link_status = "reconnecting"
            self._last_data_time = None
        self._state = ProviderState.TRACKING_LOST
        self._clear_samples()

    def _clear_samples(self) -> None:
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def _send_verified_command(self, command: str) -> None:
        response = self._connection.send_command(command)
        if response is None:
            raise RuntimeError(f"No acknowledgement for {command}")
        if response.error:
            raise ValueError(f"xIMU3 rejected {command}: {response.error}")

    def _setting_commands(self) -> list[str]:
        return [
            _command("axesAlignment", _axes_alignment_value(self.axes_alignment)),
            _command("ahrsAxesConvention", int(self.ahrs_axes_convention)),
            _command("ahrsIgnoreMagnetometer", bool(self.ahrs_ignore_magnetometer)),
        ]

    def _configure_wifi(self) -> None:
        # A UDP ping also tells the device where to send its data stream.
        identity = self._connection.ping()
        if identity is None:
            raise RuntimeError("xIMU3 did not respond to ping")
        for command in (*self._setting_commands(), _command("apply", None)):
            self._send_verified_command(command)

    def _run_wifi(self) -> None:
        config = ximu3.UdpConnectionConfig(
            self.wifi_ip_address, self.wifi_port_send, self.wifi_port_receive
        )
        connection = ximu3.Connection(config)
        self._connection = connection
        callback_ids = [
            connection.add_quaternion_callback(self._on_quaternion),
            connection.add_status_callback(self._on_connection_status),
        ]
        try:
            self._state = ProviderState.TRACKING_LOST
            # KeepOpen opens the connection itself and reopens it whenever the link goes silent.
            self._keep_open = ximu3.KeepOpen(connection)
            next_attempt = 0.0
            while not self._stop_event.wait(0.1):
                sdk_connected = connection.get_status() == ximu3.CONNECTION_STATUS_CONNECTED
                with self._link_lock:
                    # Status callbacks are the primary signal; polling covers a missed transition.
                    if sdk_connected and (self._connected_generation == 0
                                          or self._link_status == "reconnecting"):
                        self._connected_generation += 1
                        self._link_status = "configuring"
                    elif not sdk_connected and self._link_status not in ("reconnecting", "connecting"):
                        self._link_status = "reconnecting"
                        self._last_data_time = None
                    generation = self._connected_generation
                    status = self._link_status
                    last_data = self._last_data_time
                if status in ("reconnecting", "connecting") or generation == 0:
                    self._state = ProviderState.TRACKING_LOST
                    continue
                if generation != self._configured_generation and time.monotonic() >= next_attempt:
                    try:
                        self._configure_wifi()
                    except (RuntimeError, OSError) as exc:
                        self._set_link_status("configuring", str(exc))
                        print(f"xIMU3 Wi-Fi setup failed: {exc}")
                        next_attempt = time.monotonic() + 2.0
                    else:
                        with self._link_lock:
                            if generation == self._connected_generation and self._link_status != "reconnecting":
                                self._configured_generation = generation
                                self._link_status = "waiting for data"
                elif generation == self._configured_generation and last_data is not None:
                    if time.monotonic() - last_data > 3.0:
                        with self._link_lock:
                            if self._last_data_time == last_data and self._link_status == "streaming":
                                self._link_status = "waiting for data"
                                self._connection_error = "No quaternion data received for 3 seconds"
                                self._state = ProviderState.TRACKING_LOST
                                self._clear_samples()
        except Exception as exc:
            self._set_link_status("error", str(exc))
            raise
        finally:
            self._keep_open = None
            for callback_id in callback_ids:
                connection.remove_callback(callback_id)
            connection.close()
            self._connection = None
            if self.link_status != "error":
                self._set_link_status("stopped")

    def stop(self, timeout: float = 2.0) -> None:
        super().stop(timeout=5.0 if self.connection_type == "wifi_ap" else timeout)
        if self.connection_type == "wifi_ap" and self._thread is not None and self._thread.is_alive():
            self._set_link_status("error", "Timed out stopping the xIMU3 Wi-Fi connection")
            self._state = ProviderState.ERROR
            raise RuntimeError("Timed out stopping the xIMU3 Wi-Fi connection")

    def _run(self) -> None:
        """Acquire and stream quaternions from xIMU3 until stop is requested."""
        if self.connection_type == "wifi_ap":
            self._run_wifi()
            return
        try:
            # Create connection config based on type
            if self.connection_type == "usb":
                if not self.port_name:
                    raise ValueError("port_name required for USB connection (e.g., '/dev/ttyUSB0', '/dev/tty.usbmodem142201')")
                config = ximu3.UsbConnectionConfig(self.port_name)
            elif self.connection_type == "wifi_ap":
                config = ximu3.UdpConnectionConfig(self.wifi_ip_address, self.wifi_port_send, self.wifi_port_receive)
            else:
                raise ValueError(f"Unknown connection_type: {self.connection_type}")

            # Open connection
            print(f"Opening {self.connection_type} connection...")
            self._connection = ximu3.Connection(config)
            self._connection.open()
            print(f"✓ Connection opened successfully")

            # Wait for device to be ready before sending commands
            time.sleep(0.5)

            # Get device statistics to confirm connection
            stats = self._connection.get_statistics()
            if stats is None:
                raise RuntimeError("No xIMU3 device found on connection")

            print(f"✓ Device ready, configuring...")

            # Configure axes alignment via command
            self._configure_axes_alignment(self._connection)
            self._configure_ahrs_axes_convention(self._connection)
            self._configure_ahrs_ignore_magnetometer(self._connection)

            # Register quaternion callback
            self._connection.add_quaternion_callback(self._on_quaternion)
            print(f"✓ Quaternion stream started")

            # Keep the connection alive until stop_event is set
            while not self._stop_event.is_set():
                time.sleep(0.01)
        except Exception as e:
            self._state = ProviderState.ERROR
            print(f"✗ Provider error: {e}")
            raise
        finally:
            if self._connection is not None:
                self._connection.close()
                print(f"Connection closed")

    def _configure_axes_alignment(self, connection) -> None:
        """Send axes alignment config command to the device with retry logic."""
        cmd = _command("axesAlignment", _axes_alignment_value(self.axes_alignment))
        max_retries = 3

        for attempt in range(max_retries):
            try:
                print(f"Configuring axes alignment '{self.axes_alignment}' (attempt {attempt + 1}/{max_retries})...")
                connection.send_command(cmd)
                time.sleep(0.5)  # Increased wait for device to acknowledge and process

                # Verify device is responding by getting statistics
                stats = connection.get_statistics()
                if stats is not None:
                    print(f"✓ Axes alignment configured successfully")
                    return
                else:
                    print(f"  Device not responding to statistics request, retrying...")

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  Attempt {attempt + 1} failed: {e}. Retrying...")
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"Failed to configure axes alignment after {max_retries} attempts: {e}")

    def _configure_ahrs_axes_convention(self, connection) -> None:
        """Set the xIMU3 AHRS axes convention (2 is NED)."""
        cmd = _command("ahrsAxesConvention", int(self.ahrs_axes_convention))
        try:
            print(
                f"Configuring AHRS axes convention "
                f"'{self.ahrs_axes_convention}' (NED=2)..."
            )
            connection.send_command(cmd)
            time.sleep(0.5)
        except Exception as e:
            raise RuntimeError(
                f"Failed to configure AHRS axes convention "
                f"'{self.ahrs_axes_convention}': {e}"
            ) from e

    def _configure_ahrs_ignore_magnetometer(self, connection) -> None:
        """Set whether the xIMU3 AHRS algorithm ignores the magnetometer."""
        cmd = _command("ahrsIgnoreMagnetometer", bool(self.ahrs_ignore_magnetometer))
        try:
            print(f"Configuring AHRS ignore magnetometer '{self.ahrs_ignore_magnetometer}'...")
            connection.send_command(cmd)
            time.sleep(0.5)
        except Exception as e:
            raise RuntimeError(
                f"Failed to configure AHRS ignore magnetometer "
                f"'{self.ahrs_ignore_magnetometer}': {e}"
            ) from e

    def set_ahrs_ignore_magnetometer(self, enabled: bool) -> None:
        """Toggle the AHRS ignore-magnetometer setting on a live connection.

        Unlike the startup-only `_configure_*` helpers, this can be called
        after the device has already connected (e.g. from the GUI thread).
        """
        if self.connection_type == "wifi_ap":
            if self.link_status != "streaming":
                self.ahrs_ignore_magnetometer = enabled
                return
            self._send_verified_command(_command("ahrsIgnoreMagnetometer", bool(enabled)))
            self._send_verified_command(_command("apply", None))
        else:
            if self._connection is None:
                raise RuntimeError("Cannot set ahrsIgnoreMagnetometer: xIMU3 is not connected")
            self.ahrs_ignore_magnetometer = enabled
            self._configure_ahrs_ignore_magnetometer(self._connection)
        self.ahrs_ignore_magnetometer = enabled

    def _on_quaternion(self, message: ximu3.QuaternionMessage) -> None:
        """Callback from the xIMU3 quaternion stream (runs in connection's callback thread)."""
        if self.connection_type == "wifi_ap":
            with self._link_lock:
                if (self._connected_generation != self._configured_generation
                        or self._link_status not in ("waiting for data", "streaming")):
                    return
        # QuaternionMessage has w, x, y, z fields
        q = qm.change_basis(
            [
                float(message.w),
                float(message.x),
                float(message.y),
                float(message.z),
            ],
            _XIMU3_TO_HEAD_FRAME,
        )
        # The SDK reports device time in microseconds; ProviderSample requires seconds.
        timestamp = float(message.timestamp) / 1_000_000 if hasattr(message, "timestamp") else time.monotonic()
        sample = ProviderSample(timestamp=timestamp, quaternion=q, raw_metadata={"device": "ximu3"})
        if self.connection_type == "wifi_ap":
            with self._link_lock:
                if (self._connected_generation != self._configured_generation
                        or self._link_status not in ("waiting for data", "streaming")):
                    return
                self._last_data_time = time.monotonic()
                self._link_status = "streaming"
                self._connection_error = None
                self._state = ProviderState.RUNNING
                self._emit(sample)
        else:
            self._emit(sample)
