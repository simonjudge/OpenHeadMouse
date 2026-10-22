"""Bounded startup checks for newly built, no-input-control release executables."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import shutil
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    executables = sorted(
        path for path in args.dist.iterdir()
        if path.is_file()
        and path.name.startswith(("OpenHeadMouse-", "DwellClick-", "SessionViewer-"))
        and path.suffix in ("", ".exe")
        and not path.name.endswith("-setup.exe")
    )
    if len(executables) != 4:
        raise RuntimeError(f"Expected four release executables, found {len(executables)}")
    with tempfile.TemporaryDirectory(prefix="openheadmouse-smoke-") as temporary:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        config = Path(temporary) / "config.json"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "config.json", config)
        for path in executables:
            command = [str(path.resolve()), "--smoke-test"]
            if path.name.startswith("OpenHeadMouse-"):
                command += ["--config", str(config)]
            subprocess.run(command, env=env, check=True, timeout=60)
            print(f"Startup passed: {path.name}")


if __name__ == "__main__":
    main()
