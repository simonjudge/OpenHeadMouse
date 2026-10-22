"""Fetch pinned assets or export build-environment licences and versions."""

from __future__ import annotations

import argparse
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.model_assets import FACE_SHA256, FACE_URL, MESH_SHA256, MESH_URL, download_verified


def export_notices(destination: Path) -> None:
    destination = destination.resolve()
    if destination == ROOT or destination in ROOT.parents or destination == Path.home():
        raise ValueError("Choose a dedicated generated notices directory")
    if destination.exists() and any(destination.iterdir()):
        manifest_path = destination / "build-environment.json"
        if not manifest_path.is_file() or json.loads(
            manifest_path.read_text(encoding="utf-8")
        ).get("generated_by") != "OpenHeadMouse prepare_release":
            raise ValueError(f"Refusing to overwrite an unmanaged directory: {destination}")
        licenses = destination / "licenses"
        if licenses.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {licenses}")
        if licenses.is_dir():
            shutil.rmtree(licenses)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, destination / name)
    packages = []
    for dist in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        copied = []
        for entry in dist.files or []:
            if any(word in entry.name.lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    target = destination / "licenses" / name / str(entry).replace("..", "_parent_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    copied.append(str(target.relative_to(destination)))
        packages.append({
            "name": name, "version": dist.version,
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            "license_files": copied,
        })
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")
    else:
        (destination / "PYTHON-LICENSE.txt").unlink(missing_ok=True)
    manifest = {
        "generated_by": "OpenHeadMouse prepare_release",
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": packages,
        "assets": {"face_landmarker.task": FACE_SHA256, "canonical_face_model.obj": MESH_SHA256},
    }
    (destination / "build-environment.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", action="store_true")
    parser.add_argument("--notices", type=Path)
    args = parser.parse_args()
    if not args.assets and args.notices is None:
        parser.error("Select --assets and/or --notices")
    if args.assets:
        download_verified(FACE_URL, ROOT / "models" / "face_landmarker.task", FACE_SHA256)
        download_verified(MESH_URL, ROOT / "models" / "canonical_face_model.obj", MESH_SHA256)
        print("Pinned model assets verified.")
    if args.notices is not None:
        export_notices(args.notices)
        print(f"Licences and build versions exported to {args.notices}. Manual review is still required.")


if __name__ == "__main__":
    main()
