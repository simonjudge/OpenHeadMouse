# xIMU3 provider

The [xIMU3 adapter](../../providers/ximu3_provider.py) receives quaternions
through the xIMU3 Python SDK.

## Requirements

- An xIMU3 and a USB connection or access to its Wi-Fi AP.
- The application's dependencies, including `ximu3>=1.9.1`.
- A known, fixed mounting orientation.

## Setup

### USB connection

1. Connect the xIMU3 by USB.
2. Select **xIMU3 (IMU)** and press **Start** with mouse control and dwell
   clicking off.
3. Choose the USB device in the device picker. Refresh if it is not listed.
4. Verify movement directions and calibrate facing forward before enabling
   cursor control.

### Wi-Fi AP connection

Join the device's Wi-Fi AP before starting xIMU3. The device picker scans for USB and Wi-Fi
announcements in the background; **Refresh devices** retries the scan. If announcements are
missed, **Connect directly** uses `providers.ximu3.wifi_ip_address`,
`wifi_port_send`, and `wifi_port_receive` from `config.json` (defaults:
`192.168.1.1`, `9000`, `8001`). Announced devices instead use their advertised
UDP ports. Confirm the computer is actually joined to the device's AP when direct
connection does not produce data.

The status under Input Provider distinguishes connection, configuration, waiting for
samples, streaming, and reconnection. Tracking and initial calibration wait for a
configured connection and a real quaternion. During a Wi-Fi outage cursor updates stop;
the SDK retries until Stop is pressed. On recovery tracking resumes using the previous
neutral pose, without counting the outage as a new recording session. Calibration that
was interrupted before completion restarts after samples resume. Configuration-command
rejections are errors, not successful connections. Both USB and Wi-Fi startup
verify and apply the saved device settings.

The xIMU3 SDK timestamps quaternion messages in microseconds of device time. The provider
converts these to seconds before velocity-mode processing and recording, matching the
other providers' timestamp unit; the measured sample interval, not the configured target
rate, determines angular speed. Existing recordings made before this conversion retain
their original microsecond timestamps and are not migrated.

Requires the `ximu3` Python package 1.9.1 or newer. Settings are sent as JSON commands
(e.g. `{"axesAlignment": 2}`) followed by `{"apply": null}`, as the x-IMU3 protocol requires.

## Configuration

Settings are under `providers.ximu3` in `config.json`. `connection_type` is
`"usb"` or `"wifi_ap"`; `port_name` identifies a USB port. Wi-Fi direct defaults
are `wifi_ip_address="192.168.1.1"`, `wifi_port_send=9000`, and
`wifi_port_receive=8001`. `target_rate_hz` defaults to `200`.
`axes_alignment`, `ahrs_axes_convention`, and `ahrs_ignore_magnetometer` configure
the device's frame and AHRS. Saved configurations can override these defaults.
Mouse gains and filtering live in `mouse_profiles.ximu3`.

### Mounting orientation

When xIMU3 is selected, the **Mounting** dropdown appears beside the magnetometer
option. Choose **LHS-Horizontal**, **LHS-Vertical**, **RHS-Horizontal**, or
**RHS-Vertical**. Horizontal means hanging from glasses with USB pointing backward;
vertical means mounted on the temple with USB pointing down. Printed arrows face
out of the head in all four cases.

The selection saves `axes_alignment` and `ahrs_axes_convention` and sends both
settings to a connected device (USB or Wi-Fi). During a Wi-Fi outage it is applied
on reconnection. Changing a live mount resets cursor mapping and restarts calibration
if enabled. Existing non-preset settings appear as **Custom (from config)** and
are preserved until a preset is selected. See [Coordinate Frame Conventions](../coordinate-frames.md)
for the preset alignments.

## Validation

From the repository root:

```powershell
python test_orientation.py --provider ximu3 --duration 20
```

The diagnostic discovers a device. Wear it in the configured orientation, hold
a neutral pose, then turn left, look up, and tilt left-ear-down. Yaw, pitch, and
roll should each increase. Stop the GUI's provider before running the diagnostic.

## Troubleshooting and limitations

- Device missing: check the USB cable/port or that the computer has joined the
  xIMU3 AP, then refresh discovery or use direct connection.
- Connected but no data: read the connection/configuration status and logs;
  rejected device settings are not a successful setup.
- Wrong motion signs: verify the mounting preset and canonical frame, not DSP
  gain signs.
- Magnetic disturbance: enable **AHRS: Ignore Magnetometer** near metal or
  electronics; yaw may drift without a reliable magnetic reference.
- Historical recordings with microsecond timestamps are not automatically
  converted to seconds.

