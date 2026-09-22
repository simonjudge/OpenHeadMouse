"""Live telemetry readout for the main GUI (spec gui/vis_gui.py).

Displays current yaw/pitch/roll from the active provider in real-time.
Session playback, statistics, and 3D mesh visualization launch in a separate browser window.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class TelemetryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._yaw_label = QLabel("Yaw (Left/Right):   --.-°")
        self._pitch_label = QLabel("Pitch (Up/Down): --.-°")
        self._roll_label = QLabel("Roll (Tilt):  --.-°")
        for label in (self._yaw_label, self._pitch_label, self._roll_label):
            label.setStyleSheet("font-family: monospace; font-size: 14pt;")

        box = QGroupBox("Telemetry")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(self._yaw_label)
        box_layout.addWidget(self._pitch_label)
        box_layout.addWidget(self._roll_label)

        layout = QVBoxLayout(self)
        layout.addWidget(box)

    def update_angles(self, yaw_deg: float, pitch_deg: float, roll_deg: float) -> None:
        self._yaw_label.setText(f"Yaw (Left/Right):   {yaw_deg:+6.1f}°")
        self._pitch_label.setText(f"Pitch (Up/Down): {pitch_deg:+6.1f}°")
        self._roll_label.setText(f"Roll (Tilt):  {roll_deg:+6.1f}°")
