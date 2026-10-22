"""Run the session viewer independently of OpenHeadMouse."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from core.diagnostics import configure_frozen_diagnostics

    configure_frozen_diagnostics("SessionViewer")
    parser = argparse.ArgumentParser(
        description="View head-movement recordings and Fitts' Law playback."
    )
    parser.add_argument("--smoke-test", action="store_true", help="Create the viewer and exit without opening recordings")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from session_viewer.session_viewer import SessionVisualizerWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = SessionVisualizerWindow()
    if args.smoke_test:
        window.close()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
