"""xIMU3 IMU provider (spec section 4, direct Python SDK integration).

Connects to an xIMU3 IMU device (USB or WiFi AP mode; Bluetooth excluded per
spec), auto-configures the hardware `axesAlignment` and AHRS axes convention based
on the configured reference frame,
and streams quaternions at up to 400 Hz.

COORDINATE FRAME & AXES ALIGNMENT LOGIC:
=========================================

Reference frame (right-handed):
  +X: Forward (direction of head gaze, out of nose)
  +Y: Right (ear-to-ear lateral axis, toward right ear)
  +Z: Down (vertical, toward neck/floor)
  This is the right-handed NED convention. The xIMU3 AHRS axes convention
  value for NED is 2.

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

The xIMU3 SDK returns quaternions already aligned per this configuration,
requiring no software transformation.

Reference: xIMU3 User Manual v1.11, axes alignment command documentation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import ximu3

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState


@dataclass
class DiscoveredDevice:
    """A discovered xIMU3 device available for connection."""
    name: str
    connection_type: str  # "usb" or "wifi_ap"
    port_name: str = ""  # for USB
    ip_address: str = ""  # for WiFi
    port: int = 7331  # for WiFi


def discover_ximu3_devices(timeout_seconds: float = 2.0) -> list[DiscoveredDevice]:
    """Scan for connected xIMU3 devices on USB and WiFi.

    Args:
        timeout_seconds: How long to wait for WiFi device announcements (default 2.0s)

    Returns:
        List of DiscoveredDevice objects representing available devices.
    """
    devices = []

    # Scan USB ports using PortScanner
    try:
        usb_devices = []

        def on_usb_announcement(announcement):
            # Collect all announcements
            usb_devices.append(announcement)

        port_scanner = ximu3.PortScanner(on_usb_announcement)
        port_scanner.scan()

        # Process announced USB devices
        for announcement in usb_devices:
            if hasattr(announcement, 'port_name') and announcement.port_name:
                port_name = announcement.port_name
                device_name = getattr(announcement, 'device_name', 'xIMU3')
                devices.append(DiscoveredDevice(
                    name=f"{device_name} (USB: {port_name})",
                    connection_type="usb",
                    port_name=port_name
                ))
    except Exception as e:
        print(f"Warning: PortScanner scan failed: {e}")

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

            if ip_address and ip_address not in seen_ips:  # Avoid duplicates
                seen_ips.add(ip_address)
                device_name = announcement.device_name if hasattr(announcement, 'device_name') else "xIMU3"

                # WiFi devices use UDP with send port 9000, receive port 8001
                devices.append(DiscoveredDevice(
                    name=f"{device_name} (WiFi: {ip_address})",
                    connection_type="wifi_ap",
                    ip_address=ip_address,
                    port=9000  # WiFi send port
                ))
    except Exception as e:
        print(f"Warning: WiFi scan failed: {e}")

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
        self.ahrs_axes_convention = ahrs_axes_convention
        self.ahrs_ignore_magnetometer = ahrs_ignore_magnetometer
        self.target_rate_hz = target_rate_hz
        self._connection = None

    def _run(self) -> None:
        """Acquire and stream quaternions from xIMU3 until stop is requested."""
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
        cmd = f"axesAlignment {self.axes_alignment}"
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
        cmd = f"ahrsAxesConvention {self.ahrs_axes_convention}"
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
        cmd = f"ahrsIgnoreMagnetometer {'true' if self.ahrs_ignore_magnetometer else 'false'}"
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
        if self._connection is None:
            raise RuntimeError("Cannot set ahrsIgnoreMagnetometer: xIMU3 is not connected")
        self.ahrs_ignore_magnetometer = enabled
        self._configure_ahrs_ignore_magnetometer(self._connection)

    def _on_quaternion(self, message: ximu3.QuaternionMessage) -> None:
        """Callback from the xIMU3 quaternion stream (runs in connection's callback thread)."""
        # QuaternionMessage has w, x, y, z fields
        q = qm.normalize(
            [
                float(message.w),
                float(message.x),
                float(message.y),
                float(message.z),
            ]
        )
        # Use the message's own timestamp if available; otherwise use current time
        timestamp = message.timestamp if hasattr(message, "timestamp") else time.monotonic()
        self._emit(ProviderSample(timestamp=float(timestamp), quaternion=q, raw_metadata={"device": "ximu3"}))
