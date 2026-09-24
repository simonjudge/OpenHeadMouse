# Coordinate Frame Conventions

## Reference Frame

```
+X: Forward (direction of gaze)
+Y: Left (ear-to-ear axis, viewer's left)
+Z: Up (vertical, against gravity)
```

Right-handed coordinate system: X × Y = Z

## Euler Angle Sign Conventions

**Intrinsic Z-Y-X rotation sequence** (yaw → pitch → roll):
- **Yaw** (rotation about Z): Head turning left is **POSITIVE**
  - Physical: Turn head left → +X axis rotates toward +Y
  - Right-hand rule: positive rotation about +Z axis

- **Pitch** (rotation about Y): Head tilting up is **POSITIVE**
  - Physical: Nod head up → +X axis rotates toward +Z
  - Right-hand rule: positive rotation about +Y axis

- **Roll** (rotation about X): Left ear down is **POSITIVE**
  - Physical: Tilt head left (left ear toward shoulder) → +Y rotates toward -Z
  - Right-hand rule: positive rotation about +X axis

## Provider-Specific Frames

### UniFace (Webcam)

**Camera frame** (from MediaPipe):
- Z: Forward (into camera)
- X: Right (from camera's perspective)
- Y: Down (below camera)

**Transformation to head frame:**
```
P = [[0, 0, 1],    # Head X ← Camera Z
     [1, 0, 0],    # Head Y ← Camera X (but camera X is right, we need left)
     [0, 1, 0]]    # Head Z ← Camera Y (but camera Y is down, we need up)
```

Hmm, this suggests: Head Y (left) ← Camera X (right), which is wrong in sign.

Actually, looking at the code more carefully:
```python
r_head = P @ r_mp @ P.T
```

This is conjugating by P. The rotation matrix r_mp is in camera frame. 
- r_mp's column 0 is the camera X axis direction in camera frame
- r_mp's column 1 is the camera Y axis direction in camera frame
- r_mp's column 2 is the camera Z axis direction in camera frame

The conjugation changes coordinate systems. If we want to express r_mp in the head frame:
- r_head = P @ r_mp @ P^T

But the comment says camera frame has X right, Y down, Z forward (into camera).

### xIMU3 (IMU)

**Reference Frame:**
```
+X: Forward (out of nose)
+Y: Right (toward right ear)
+Z: Down (toward neck/floor)
```

This is the right-handed NED convention (North/forward, East/right,
Down). The xIMU3 alignment below maps the physical device axes into this
logical frame. Configure the xIMU3 **AHRS axes convention** to `2` (NED) in
addition to the axes alignment.

**Physical Device:**
The xIMU3 has three axes printed with arrows on its case: X, Y, Z.

**How to Determine Your axes_alignment:**

Hold the xIMU3 exactly how you will wear it on your head. Ask three questions:

1. **Which printed arrow points Forward (out of your nose)?**
   - Set that as your **Logical X**
   - If it points backward, use the negative (e.g., `-X`)

2. **Which printed arrow points to the Right (toward your right ear)?**
   - Set that as your **Logical Y**
   - If it points left, use the negative (e.g., `-Y`)

3. **Which printed arrow points Down (toward your neck/floor)?**
   - Set that as your **Logical Z**
   - If it points up, use the negative (e.g., `-Z`)

**Example: Left Temple Mount (USB Pointing Backward)**

Holding the sensor flat against the left side of the head:
```
1. Physical +X arrow → points Forward ✓
   Set Logical X = +X

2. Physical +Z arrow → points out of the head (from the left ear) ✓
   Set Logical Y = -Z

3. Physical +Y arrow → points Down ✓
   Set Logical Z = +Y
```

**Result: `axes_alignment = "+X-Z+Y"`**

This tells the xIMU3 device:
- Output X ← Physical X
- Output Y ← Physical -Z (negated)
- Output Z ← Physical Y

Device quaternions output in the correct reference frame with no software transformation needed.

**Reference:** xIMU3 User Manual v1.11, axes alignment command format.

## Testing

Use `test_orientation.py` to verify each provider:

```bash
# Test UniFace (webcam)
python test_orientation.py --provider uniface --duration 20

# Test xIMU3
python test_orientation.py --provider ximu3 --duration 20
```

**Expected values when performing movements:**
1. Turn head LEFT → Yaw = POSITIVE (e.g., +30°)
2. Nod head UP → Pitch = POSITIVE (e.g., +20°)
3. Tilt head LEFT (left ear down) → Roll = POSITIVE (e.g., +15°)

If signs don't match, the axes_alignment or quaternion extraction needs correction.
