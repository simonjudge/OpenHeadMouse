"""Quaternion math for the HeadMouse reference frame.

Convention: quaternions are represented as numpy arrays ``[w, x, y, z]``
(scalar-first). This is the OPPOSITE of ``scipy.spatial.transform.Rotation``,
which uses scalar-last ``[x, y, z, w]`` — any interop with scipy must
explicitly reorder components.

Reference frame (right-handed, matches the system spec):
    +X — forward (direction of view, out of nose)
    +Y — right (ear-to-ear lateral axis, toward right ear)
    +Z — down (vertical axis, toward floor/neck)

This frame is consistent across all providers:
  - UniFace (webcam): Transforms camera frame → reference frame via permutation
  - xIMU3 (IMU): Configured via axes_alignment command to output in this frame
  - AirPods (CoreMotion): Assumes device frame matches this frame

Sign convention for the derived Euler angles, given the above frame and a
right-hand rotation rule (positive rotation = counter-clockwise when viewed
from the positive end of the rotation axis looking toward the origin):
    - Yaw   (rotation about +Z): turning the head to the LEFT is POSITIVE
      (rotating the +X forward axis toward -Y, which is left, is a positive
      right-hand rotation about +Z).
    - Pitch (rotation about +Y): looking DOWN is POSITIVE (rotating +X toward
      -Z down is a positive right-hand rotation about +Y).
    - Roll  (rotation about +X): tilting the head so the LEFT ear drops
      toward the ground is POSITIVE (rotating +Y right toward +Z down is a
      positive right-hand rotation about +X).

Euler angles are extracted using an intrinsic Z-Y-X sequence
(yaw, then pitch, then roll), i.e. ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``,
per the spec's requirement to avoid gimbal lock/discontinuities within
normal cervical range of motion (pitch away from +/-90 degrees).
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def normalize(q: np.ndarray) -> np.ndarray:
    """Normalize one quaternion ``[w, x, y, z]`` or a batch ``(N, 4)`` to unit length."""
    q = np.asarray(q, dtype=np.float64)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    norm = np.where(norm < _EPS, 1.0, norm)
    return q / norm


def conjugate(q: np.ndarray) -> np.ndarray:
    """Return the conjugate ``[w, -x, -y, -z]`` of a quaternion or batch."""
    q = np.asarray(q, dtype=np.float64)
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def inverse(q: np.ndarray) -> np.ndarray:
    """Return the inverse of a unit quaternion (equal to the conjugate for unit q)."""
    q = np.asarray(q, dtype=np.float64)
    norm_sq = np.sum(q * q, axis=-1, keepdims=True)
    norm_sq = np.where(norm_sq < _EPS, 1.0, norm_sq)
    return conjugate(q) / norm_sq


def multiply(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """Hamilton product ``q1 ⊗ q2``. Supports single quaternions or broadcastable batches."""
    q1 = np.asarray(q1, dtype=np.float64)
    q2 = np.asarray(q2, dtype=np.float64)
    w1, x1, y1, z1 = np.moveaxis(q1, -1, 0)
    w2, x2, y2, z2 = np.moveaxis(q2, -1, 0)
    w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2
    return np.stack([w, x, y, z], axis=-1)


def relative_rotation(q_neutral: np.ndarray, q_current: np.ndarray) -> np.ndarray:
    """Compute ``q_relative = q_neutral^-1 ⊗ q_current``."""
    return multiply(inverse(q_neutral), q_current)


IDENTITY_QUATERNION = np.array([1.0, 0.0, 0.0, 0.0])


def to_euler_zyx(q: np.ndarray) -> np.ndarray:
    """Convert quaternion(s) ``[w, x, y, z]`` to intrinsic Z-Y-X Euler angles.

    Returns an array (or batch of arrays) ``[yaw, pitch, roll]`` in radians,
    matching the sign convention documented at module level. ``R = Rz(yaw) @
    Ry(pitch) @ Rx(roll)`` for a quaternion-derived rotation matrix R applied
    to column vectors in the [+X forward, +Y left, +Z up] frame.
    """
    q = normalize(np.asarray(q, dtype=np.float64))
    w, x, y, z = np.moveaxis(q, -1, 0)

    # Standard quaternion-to-rotation-matrix elements (Wikipedia convention,
    # v' = q v q*): R20 = 2(xz-wy), R10 = 2(xy+wz), R21 = 2(yz+wx).
    r20 = 2 * (x * z - w * y)
    r20 = np.clip(r20, -1.0, 1.0)  # guard against float error pushing |r20| > 1 near +/-90 deg pitch
    r10 = 2 * (x * y + w * z)
    r00 = 1 - 2 * (y * y + z * z)
    r21 = 2 * (y * z + w * x)
    r22 = 1 - 2 * (x * x + y * y)

    pitch = np.arcsin(r20)
    yaw = np.arctan2(r10, r00)
    roll = np.arctan2(-r21, r22)

    return np.stack([yaw, pitch, roll], axis=-1)


def from_euler_zyx(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Build a quaternion ``[w, x, y, z]`` from intrinsic Z-Y-X Euler angles (radians).

    Inverse of :func:`to_euler_zyx`, used mainly for tests/synthetic data generation.
    """
    hy, hp, hr = yaw / 2.0, pitch / 2.0, roll / 2.0
    cy, sy = np.cos(hy), np.sin(hy)
    cp, sp = np.cos(hp), np.sin(hp)
    cr, sr = np.cos(hr), np.sin(hr)

    # q = qz(yaw) ⊗ qy(pitch) ⊗ qx(roll). The y/x components are negated
    # relative to the textbook axis-rotation quaternions: a bare rotation
    # about +Y drives +X toward -Z, and a bare rotation about +X drives +Y
    # toward +Z — both opposite the module's documented sign convention
    # (pitch up = positive, roll left-ear-down = positive). Negating here
    # is equivalent to rotating about -Y / -X instead of +Y / +X.
    qz = np.array([cy, 0.0, 0.0, sy])
    qy = np.array([cp, 0.0, -sp, 0.0])
    qx = np.array([cr, -sr, 0.0, 0.0])
    return normalize(multiply(multiply(qz, qy), qx))
