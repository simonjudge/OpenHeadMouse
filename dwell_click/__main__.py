"""Run dwell-click independently of HeadMouse."""

from __future__ import annotations

import argparse
import sys

from .engine import DwellSettings
from .tray import DwellTray


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Click automatically when the pointer remains within a small area."
    )
    parser.add_argument("--dwell-time", type=float, default=1.0, metavar="SECONDS")
    parser.add_argument("--movement-tolerance", type=float, default=10.0, metavar="PIXELS")
    parser.add_argument("--resume-time", type=float, default=0.5, metavar="SECONDS")
    parser.add_argument("--color", default="#ff0000", help="Qt color name or #RRGGBB value")
    args = parser.parse_args(argv)

    try:
        settings = DwellSettings(
            dwell_time_s=args.dwell_time,
            movement_tolerance_px=args.movement_tolerance,
            resume_time_s=args.resume_time,
            ring_color=args.color,
        )
    except ValueError as exc:
        parser.error(str(exc))

    from PySide6.QtWidgets import QApplication

    from .service import DwellClickService

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    try:
        service = DwellClickService(settings)
    except ValueError as exc:
        parser.error(str(exc))
    service.start()
    tray = DwellTray(service, settings)
    app.aboutToQuit.connect(service.stop)
    app.aboutToQuit.connect(tray.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
