# XIAO nRF52840 Sense provider

This guide covers flashing the board firmware and testing the XIAO Sense with
OpenHeadMouse on Windows. The simplest firmware workflow is the standalone
Arduino IDE; Visual Studio or VS Code is not required. The firmware sketch is
[`firmware/OpenHeadMouseXiao/OpenHeadMouseXiao.ino`](./firmware/OpenHeadMouseXiao/OpenHeadMouseXiao.ino).

This is specifically for **XIAO nRF52840 Sense** (or Sense Plus), with the
onboard LSM6DS3TR-C IMU. The non-Sense XIAO nRF52840 does not have that IMU.
Nothing needs to be soldered or connected to the board for this test.

## 1. Install Arduino IDE and the XIAO board support

1. Install [Arduino IDE 2](https://www.arduino.cc/en/software) and open it.
2. Open **File > Preferences**. In **Additional boards manager URLs**, add:
   `https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json`
   (if there are already URLs in the field, separate entries with commas).
3. Open **Tools > Board > Boards Manager**, search for `Seeed nRF52`, and
   install **Seeed nRF52 mbed-enabled Boards**. This BLE sketch uses
   `ArduinoBLE`; choose the Mbed-enabled Seeed core rather than the other
   `Seeed nRF52 Boards` core.
4. Connect the XIAO using a known data-capable USB-C cable. In
   **Tools > Board**, select **Seeed XIAO nRF52840 Sense** (not the non-Sense
   board). In **Tools > Port**, select the port for the XIAO. On Windows it
   will normally appear as a COM port.

Seeed's board setup instructions are also in the
[XIAO nRF52840 guide](https://wiki.seeedstudio.com/XIAO_BLE/).

## 2. Install the two Arduino libraries

In Arduino IDE, open **Sketch > Include Library > Manage Libraries**. Search
for and install:

- **ArduinoBLE** (by Arduino)
- **Seeed Arduino LSM6DS3** (by Seeed Studio)

The sketch reads the Sense's onboard IMU using Seeed's LSM6DS3 library and
advertises its samples through ArduinoBLE. No microphone library is needed.

## 3. Compile and upload the firmware

1. Open the sketch file linked at the top of this page. The folder and `.ino`
   file have matching names, as required by Arduino sketches.
2. Confirm **Seeed XIAO nRF52840 Sense** and the XIAO COM port are selected.
3. Click **Verify** (the checkmark) and wait for compilation to finish.
4. Click **Upload** (the right arrow). Wait for the upload-complete message
   before unplugging the board.

The board starts advertising as `OpenHeadMouse-XIAO` after boot. If wanted,
open **Tools > Serial Monitor** at **115200 baud**; the startup message is
`OpenHeadMouse XIAO sensor advertising`. The serial monitor is optional for
normal BLE tracking.

If the board or port is missing, try another USB-C cable known to carry data,
connect directly to the computer rather than through a hub, and reconnect the
board. If compilation says `ArduinoBLE.h` or `LSM6DS3.h` is missing, install the
corresponding library above and make sure the Mbed-enabled board core is
selected.

## 4. Run OpenHeadMouse

From a PowerShell terminal in the OpenHeadMouse repository root, install the
Python dependencies if they are not already installed, then launch the app:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

In the setup window, select **XIAO nRF52840 Sense (BLE)** and click **Start**.
The connection status should progress from scanning/connecting to **tracking**
after the first valid sensor notification. Initial calibration also waits for
that first sample. For the first test, keep mouse control off and hold the
board in the same orientation in which it will be worn.

Alternatively, test from the repository root without opening the GUI:

```powershell
.\.venv\Scripts\python.exe test_orientation.py --provider xiao_ble --duration 20
```

Do not run the GUI and this command at the same time: the BLE board accepts one
connection, and the two programs would compete for it.

Move the board as if it were fixed to your head: turn left, look up, then tilt
the left edge down. The orientation test should show positive yaw, pitch and
roll respectively. Six-axis tracking has no compass/magnetometer, so pitch and
roll are gravity-referenced while yaw is relative and drifts with time. This
provider is useful for trying the integration, but should not be treated as an
absolute-heading tracker.

## Mounting and sensor axes

The configuration entry `providers.xiao_ble.device_to_head` is a 3x3 rotation
matrix mapping the IMU's sensor axes to the canonical head axes. Its default
identity matrix assumes sensor +X points forward, +Y points left, and +Z points
up. If your mounting differs, change that matrix in `config.json` and repeat
the direction test. It must be a proper rotation (`det(M) = +1`); do not fix
direction signs by changing mouse gains. See
[`COORDINATE_FRAMES.md`](../../COORDINATE_FRAMES.md) for the frame convention.

## What the board sends

The sketch notifies a 16-byte little-endian packet approximately every 10 ms:

| Offset | Type | Meaning |
|---:|---|---|
| 0 | `uint32` | Sensor `micros()` timestamp |
| 4 | `int16[3]` | Acceleration X/Y/Z in milli-g |
| 10 | `int16[3]` | Angular velocity X/Y/Z in centi-degrees/second |

The service UUID is `b28a0001-7f2d-4c9a-9b23-1d43c6e8a501`; the notification
characteristic UUID is `b28a0002-7f2d-4c9a-9b23-1d43c6e8a501`. These and the
packet layout must match between the sketch and
[`providers/xiao_ble_provider.py`](../xiao_ble_provider.py).
