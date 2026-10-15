"""Widgets and help text shared by the dwell-click settings UIs."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton

DWELL_HELP = {
    "dwell_time_s": "Dwell time: how long the pointer must stay still before a left click is made.",
    "movement_tolerance_px": (
        "Movement tolerance: how far (in pixels) the pointer can wander and still count as "
        "staying still. Larger values make dwelling easier with an unsteady pointer."
    ),
    "resume_time_s": (
        "Resume time: if the pointer briefly leaves the tolerance area and returns within this "
        "time, the dwell timer carries on instead of starting again."
    ),
    "ring_diameter_px": "Diameter: size of the dwell timer circle drawn around the pointer, in pixels.",
    "ring_thickness_px": "Thickness: width of the timer segment that fills the circle as the dwell progresses.",
    "ring_color": "Colour: colour of the dwell timer circle. Click to choose a new colour.",
}


class ColorButton(QPushButton):
    """Shows a colour swatch and opens a colour picker when clicked."""

    color_changed = Signal(str)

    def __init__(self, color: str = "#ff0000", parent=None):
        super().__init__(parent)
        self._color = "#ff0000"
        self.set_color(color)
        self.clicked.connect(self._choose)

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        parsed = QColor(color)
        if not parsed.isValid():
            parsed = QColor("#ff0000")
        self._color = parsed.name()
        text_color = "#000000" if parsed.lightness() > 140 else "#ffffff"
        self.setText(self._color)
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; color: {text_color}; }}"
        )

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Dwell circle colour")
        if chosen.isValid() and chosen.name() != self._color:
            self.set_color(chosen.name())
            self.color_changed.emit(self._color)
