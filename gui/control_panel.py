"""Control panel for toggling subsystems independently."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    """Toggles for recording and mouse control."""

    recording_toggled = Signal(bool)
    mouse_control_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._record_check = QCheckBox("Recording")
        self._record_check.setChecked(True)
        self._record_check.toggled.connect(self.recording_toggled)

        self._mouse_check = QCheckBox("Mouse Control")
        self._mouse_check.setChecked(False)
        self._mouse_check.toggled.connect(self.mouse_control_toggled)

        box = QGroupBox("Subsystems")
        layout = QVBoxLayout(box)
        layout.addWidget(self._record_check)
        layout.addWidget(self._mouse_check)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(box)

    @property
    def recording_enabled(self) -> bool:
        return self._record_check.isChecked()

    @property
    def mouse_control_enabled(self) -> bool:
        return self._mouse_check.isChecked()
