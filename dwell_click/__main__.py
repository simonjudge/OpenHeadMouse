"""Run dwell-click independently of OpenHeadMouse."""

from __future__ import annotations

import argparse
import sys

from dwell_click.engine import DwellSettings
from dwell_click.tray import DwellTray


def main(argv: list[str] | None = None) -> int:
    from core.diagnostics import configure_frozen_diagnostics

    configure_frozen_diagnostics("DwellClick")
    parser = argparse.ArgumentParser(
        description="Click automatically when the pointer remains within a small area."
    )
    parser.add_argument("--dwell-time", type=float, default=1.0, metavar="SECONDS")
    parser.add_argument("--movement-tolerance", type=float, default=10.0, metavar="PIXELS")
    parser.add_argument("--resume-time", type=float, default=0.5, metavar="SECONDS")
    parser.add_argument("--color", default="#ff0000", help="Qt color name or #RRGGBB value")
    parser.add_argument("--diameter", type=int, default=40, metavar="PIXELS",
                        help="Diameter of the dwell timer circle")
    parser.add_argument("--thickness", type=int, default=4, metavar="PIXELS",
                        help="Thickness of the dwell timer segment")
    parser.add_argument("--zoom", action="store_true", help="Use two-stage zoom dwell clicking")
    parser.add_argument("--zoom-width", type=int, default=300, metavar="PIXELS")
    parser.add_argument("--zoom-height", type=int, default=200, metavar="PIXELS")
    parser.add_argument("--zoom-scale", type=float, default=4.0, metavar="FACTOR")
    parser.add_argument("--context-menu", action="store_true", help="Choose left, right or zoom after dwelling")
    parser.add_argument("--menu-distance", type=int, default=60, metavar="PIXELS")
    parser.add_argument("--menu-size", type=int, default=54, metavar="PIXELS")
    parser.add_argument("--menu-lock-time", type=float, default=3.0, metavar="SECONDS")
    parser.add_argument("--smoke-test", action="store_true", help="Validate settings and Qt startup without clicking")
    args = parser.parse_args(argv)

    try:
        settings = DwellSettings(
            dwell_time_s=args.dwell_time,
            movement_tolerance_px=args.movement_tolerance,
            resume_time_s=args.resume_time,
            ring_color=args.color,
            ring_diameter_px=args.diameter,
            ring_thickness_px=args.thickness,
            zoom_enabled=args.zoom,
            zoom_width_px=args.zoom_width,
            zoom_height_px=args.zoom_height,
            zoom_scale=args.zoom_scale,
            context_menu_enabled=args.context_menu,
            context_menu_distance_px=args.menu_distance,
            context_menu_size_px=args.menu_size,
            context_menu_lock_time_s=args.menu_lock_time,
        )
    except ValueError as exc:
        parser.error(str(exc))

    from PySide6.QtWidgets import QApplication

    from dwell_click.service import DwellClickService

    app = QApplication(sys.argv[:1])
    if args.smoke_test:
        return 0
    app.setQuitOnLastWindowClosed(False)
    try:
        service = DwellClickService(settings)
    except ValueError as exc:
        parser.error(str(exc))
    service.start()
    tray = None

    def apply_settings(values: dict) -> None:
        updated = DwellSettings.from_config({**service.settings.to_config(), **values})
        service.update_settings(updated)
        tray.set_settings(updated)

    tray = DwellTray(service, settings, on_settings_changed=apply_settings)
    app.aboutToQuit.connect(service.stop)
    app.aboutToQuit.connect(tray.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
