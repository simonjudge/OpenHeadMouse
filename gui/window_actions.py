"""Consistent close controls for application and playback windows."""

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenu


def add_close_action(window: QMainWindow, file_menu: QMenu) -> QAction:
    action = QAction("Close", window)
    action.setShortcut(QKeySequence.Close)
    action.setToolTip("Close this window and stop its current task (Ctrl+W)")
    action.triggered.connect(window.close)
    file_menu.addSeparator()
    file_menu.addAction(action)
    return action
