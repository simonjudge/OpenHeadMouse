# Provider guides

Start with the [application setup](../../README.md#setup-running-from-source),
then use the guide for your selected input. Each guide covers requirements,
setup, configuration, validation, and troubleshooting.

| Input | Guide | Acquisition |
| --- | --- | --- |
| UniFace | [uniface.md](./uniface.md) | Webcam and MediaPipe |
| OpenSeeFace | [openseeface.md](./openseeface.md) | Webcam and ONNX models, or external tracker UDP |
| xIMU3 | [ximu3.md](./ximu3.md) | USB or Wi-Fi AP |
| AirPods | [airpods.md](./airpods.md) | macOS CoreMotion helper |
| Sony headphones | [sony.md](./sony.md) | Managed native Sony Head Tracker bridge |
| XIAO nRF52840 Sense | [xiao_ble.md](./xiao_ble.md) | BLE and uploaded Arduino firmware |

For every hardware test, initially disable **Mouse Control** and **Dwell Click**.
Verify turn-left, look-up, and left-ear-down give positive yaw, pitch, and roll.
Then calibrate facing forward before enabling cursor control. Keep another way
to control the computer available. See [coordinate frames](../coordinate-frames.md)
for mounting conventions; successful unit tests are not hardware validation.

## Source layout

Each provider entry point is `providers/<name>_provider.py`. Provider-specific
helpers, firmware, and native helper sources live in `providers/<name>/`.
Setup documentation lives here, not alongside implementation files.

`base_provider.py` defines the shared lifecycle and `ProviderSample` contract.
`mock_provider.py` provides synthetic samples for development and tests; it needs
no hardware and is not offered in the GUI. Configuration keys and GUI selections
are unchanged by the source reorganization.
