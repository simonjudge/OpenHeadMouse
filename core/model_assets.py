"""Pinned model assets used by source setup and release builds."""

from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request


FACE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
FACE_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MESH_URL = (
    "https://raw.githubusercontent.com/google-ai-edge/mediapipe/"
    "a908d668c730da128dfa8d9f6bd25d519d006692/"
    "mediapipe/modules/face_geometry/data/canonical_face_model.obj"
)
MESH_SHA256 = "8bac80443397e113f41a8b565ea72c59390bc031d9defab289dba7bc0c54e618"


def download_verified(url: str, path: Path, expected: str) -> Path:
    """Use verified TLS and only persist a complete, checksum-matched response."""
    if path.is_file():
        data = path.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum mismatch for {path.name}: expected {expected}, got {actual}")
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path
