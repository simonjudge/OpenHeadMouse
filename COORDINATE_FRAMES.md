# Coordinate Frame Conventions

Every provider converts its device's native orientation into **one canonical head frame**
before emitting a `ProviderSample`. Everything downstream (DSP/cursor mapping, recorder,
calibration, session statistics, session viewer) assumes this frame and never needs to know
which provider produced the data. Gains therefore mean the same thing for every provider.

## Canonical head frame (FLU)

```
+X: Forward (out of the nose)
+Y: Left    (toward the LEFT ear)
+Z: Up      (toward the top of the head)
```

Right-handed: X × Y = Z. Quaternions are scalar-first `[w, x, y, z]`.

## Euler angles (yaw, pitch, roll)

`core.quaternion_math.to_euler_zyx` extracts intrinsic Z-Y-X angles from the relative rotation
`q_neutral⁻¹ ⊗ q_current`. For every provider:

| Physical movement              | Angle | Sign         |
|--------------------------------|-------|--------------|
| Turn head LEFT                 | Yaw   | **positive** |
| Look UP (nod up)               | Pitch | **positive** |
| Tilt LEFT ear toward shoulder  | Roll  | **positive** |

(Yaw is the right-hand rotation about +Z; pitch and roll are the *negated* right-hand rotations
about +Y and +X so that "up" and "left ear down" come out positive. Equivalently
`R = Rz(yaw) · Ry(−pitch) · Rx(−roll)`.)

## Cursor mapping (DSP gains)

Screen x grows to the right and y grows downward. `core/dsp_engine.py` uses the **same signs in
absolute and velocity mode**:

```
absolute:  x = centre_x − kx · yaw        y = centre_y − ky · pitch
velocity:  x −= kx · yaw_rate · dt         y −= ky · pitch_rate · dt
```

So **positive gains make the cursor follow the head** (turn left → cursor left, look up →
cursor up) for every provider and both modes. Use a negative gain only to deliberately invert
an axis. If a provider needs a negative gain to move naturally, its frame conversion is wrong —
fix the provider, not the gain.

## Provider conversions

Each provider holds a fixed 3×3 matrix `M` whose **rows are the canonical head axes (forward,
left, up) expressed in device coordinates** (`v_head = M · v_device`). `M` must be a proper
rotation (det = +1); a reflection (e.g. just swapping two components) silently inverts rotation
directions. Apply it with `core.quaternion_math.change_basis(q_device, M)`, which is equivalent
to `R_head = M · R_device · Mᵀ`.

| Provider | Native device frame | `M` rows (forward; left; up) | Resulting quaternion |
|---|---|---|---|
| UniFace (MediaPipe face model) | X subject's left, Y up, Z out of face | `[0,0,1]; [1,0,0]; [0,1,0]` | via matrix `P·R_mp·Pᵀ` |
| AirPods (CMHeadphoneMotionManager) | X right, Y forward, Z up | `[0,1,0]; [−1,0,0]; [0,0,1]` | `[w, y, −x, z]` |
| xIMU3 (AHRS NED, axes aligned to FRD) | X forward, Y right, Z down | `[1,0,0]; [0,−1,0]; [0,0,−1]` | `[w, x, −y, −z]` |

Code: `providers/uniface_provider.py` (`_CAMERA_TO_HEAD_FRAME`),
`providers/airpods/airpods_provider.py` (`_AIRPODS_TO_HEAD_FRAME`),
`providers/ximu3_provider.py` (`_XIMU3_TO_HEAD_FRAME`). The session viewer's face mesh is in
MediaPipe coordinates, so it maps canonical rotations back with the UniFace matrix
(`session_viewer/mesh_visualizer.py`).

### xIMU3 hardware set-up

The xIMU3 is configured on the device with AHRS axes convention `2` (NED) and an
`axes_alignment` that makes its body axes Forward/Right/Down; the provider then converts FRD to
the canonical FLU frame in software with `_XIMU3_TO_HEAD_FRAME = diag(1, −1, −1)`
(`q_head = [w, x, −y, −z]`). This conversion is the same for every mount; only the
`axes_alignment` changes between mounts.

> **Do not compensate a sign error by choosing a different `axes_alignment`.** The alignment must
> describe the physical mount (FRD). An earlier version used an identity conversion and only
> appeared correct because the wrong alignment number was being sent (the mirror-side mount,
> which is a hidden 180° flip about X). Use `tests/test_provider_frames.py` and
> `test_orientation.py` to verify signs instead.

Hold the xIMU3 exactly how you will wear it and answer:

1. Which printed arrow points **Forward** (out of your nose)? → Logical X (negate if backward).
2. Which printed arrow points **Right** (toward your right ear)? → Logical Y (negate if left).
3. Which printed arrow points **Down** (toward the floor)? → Logical Z (negate if up).

Example — left temple mount, USB pointing backward: physical +X forward, physical +Z out of the
left ear (so Right = −Z), physical +Y down. Result: `axes_alignment = "+X-Z+Y"`.
(Reference: xIMU3 User Manual v1.11, axes alignment command — but see the note below on the
numeric values.)

The main GUI offers these presets beside the magnetometer option, visible only
when xIMU3 is selected:

| Mounting orientation | USB direction | `axes_alignment` | Value sent | `ahrs_axes_convention` |
|---|---|---|---|---|
| LHS-Horizontal | Backward | `+X-Z+Y` | `2` | `2` (NED) |
| LHS-Vertical | Down | `+Y-Z-X` | `6` | `2` (NED) |
| RHS-Horizontal | Backward | `+X+Z-Y` | `1` | `2` (NED) |
| RHS-Vertical | Down | `-Y+Z-X` | `17` | `2` (NED) |

**Note:** the numeric `axesAlignment` enum listed in the xIMU3 user manual / x-IMU3 App is
incorrect for the firmware. The values above come from `_AXES_ALIGNMENT_VALUES` in
`providers/ximu3_provider.py`, which was verified on hardware.

In all cases the printed arrows face out of the head. Horizontal is the
glasses-hanging mount; vertical is the temple/velcro mount. These definitions
match the comment header in `providers/ximu3_provider.py`.

## Adding a new provider

1. Work out the device's native axes as worn (which way each +axis points on the head) and
   write down `M` (rows = forward, left, up in device coordinates). Check `det(M) = +1`.
2. In the provider, convert every quaternion with `qm.change_basis(q, M)` (or, for a rotation
   matrix source, `M @ R @ M.T`) before calling `_emit`. Do not compensate in the DSP gains.
3. Add the device to `PHYSICAL_MOTIONS` in `tests/test_provider_frames.py`, describing turn-left,
   look-up and left-ear-down as rotations about the device's own axes; the test asserts each
   yields +yaw, +pitch and +roll respectively.
4. Verify on hardware (below), then confirm positive gains move the cursor naturally in both
   absolute and velocity mode.

## Testing on hardware

```bash
python test_orientation.py --provider uniface --duration 20
python test_orientation.py --provider airpods --duration 20
python test_orientation.py --provider ximu3   --duration 20
```

Expected: turn LEFT → yaw positive; look UP → pitch positive; tilt LEFT ear down → roll positive.
Unit tests covering the conventions: `tests/test_provider_frames.py`, `tests/test_dsp.py`,
`tests/test_quaternion_math.py`.
