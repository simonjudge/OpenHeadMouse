"""Setup panel: provider selection and start/stop control (spec gui/setup_gui.py).

Reduced scope for the M6 vertical slice: only the UniFace provider is wired
up; xIMU3/AirPods are listed but disabled until their milestones land.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class SetupPanel(QWidget):
    start_requested = Signal(str)  # provider name, e.g. "uniface"
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._provider_combo = QComboBox()
        self._provider_combo.addItem("UniFace (webcam)", userData="uniface")
        self._provider_combo.addItem("xIMU3 (IMU)", userData="ximu3")
        self._provider_combo.addItem("AirPods (CoreMotion)", userData="airpods")

        self._start_button = QPushButton("Start")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)

        self._start_button.clicked.connect(self._on_start_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)

        box = QGroupBox("Input Provider")
        box_layout = QHBoxLayout(box)
        box_layout.addWidget(self._provider_combo)
        box_layout.addWidget(self._start_button)
        box_layout.addWidget(self._stop_button)

        layout = QVBoxLayout(self)
        layout.addWidget(box)

    def _on_start_clicked(self) -> None:
        provider_name = self._provider_combo.currentData()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._provider_combo.setEnabled(False)
        self.start_requested.emit(provider_name)

    def _on_stop_clicked(self) -> None:
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._provider_combo.setEnabled(True)
        self.stop_requested.emit()

    def reset_to_stopped(self) -> None:
        """Reset UI to stopped state after a failed start attempt."""
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._provider_combo.setEnabled(True)
