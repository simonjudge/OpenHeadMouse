import os

# Must be set before PySide6/Qt is imported anywhere, so GUI tests can build
# real widgets without a real display (no windowing system in this sandbox/CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app

