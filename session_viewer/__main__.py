"""Run the session viewer independently of HeadMouse."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="View head-movement recordings and Fitts' Law playback."
    )
    parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from session_viewer.session_viewer import SessionVisualizerWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = SessionVisualizerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
