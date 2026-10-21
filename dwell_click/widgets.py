"""Widgets and help text shared by the dwell-click settings UIs."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDoubleSpinBox, QGridLayout, QGroupBox,
    QLabel, QPushButton, QSpinBox,
)

from .engine import DwellSettings

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
    "zoom_enabled": (
        "Start with zoom locked on. Move, then dwell inside the magnified screenshot "
        "to click the precise point. The menu's zoom lock is independent of left/right."
    ),
    "zoom_width_px": "Width of the displayed borderless zoom box in pixels.",
    "zoom_height_px": "Height of the displayed borderless zoom box in pixels.",
    "zoom_scale": "Magnification of the captured area. Pixels are enlarged without smoothing.",
}


class ZoomOptions(QGroupBox):
    """Shared zoom controls for the main panel and tray dialog."""

    changed = Signal()

    def __init__(self, config: dict):
        super().__init__("Zoom Clicking")
        self._enabled = QCheckBox("Use zoom (start locked)")
        self._width = QSpinBox()
        self._height = QSpinBox()
        for control in (self._width, self._height):
            control.setRange(1, 16384)
            control.setSuffix(" px")
        self._scale = QDoubleSpinBox()
        self._scale.setRange(1.01, 100.0)
        self._scale.setDecimals(2)
        self._scale.setSingleStep(0.5)
        self._scale.setSuffix(" x")
        self._fields = (
            (self._enabled, "zoom_enabled"),
            (self._width, "zoom_width_px"),
            (self._height, "zoom_height_px"),
            (self._scale, "zoom_scale"),
        )
        self.set_values({**DwellSettings().to_config(), **config})
        grid = QGridLayout(self)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self._enabled.setToolTip(DWELL_HELP["zoom_enabled"])
        grid.addWidget(self._enabled, 0, 0, 1, 2)
        for row, column, text, control, key in (
            (0, 2, "Scale", self._scale, "zoom_scale"),
            (1, 0, "Width", self._width, "zoom_width_px"),
            (1, 2, "Height", self._height, "zoom_height_px"),
        ):
            label = QLabel(text)
            label.setToolTip(DWELL_HELP[key])
            control.setToolTip(DWELL_HELP[key])
            grid.addWidget(label, row, column)
            grid.addWidget(control, row, column + 1)
            control.valueChanged.connect(self.changed)
        self._enabled.toggled.connect(self.changed)

    def values(self) -> dict:
        return {
            "zoom_enabled": self._enabled.isChecked(),
            "zoom_width_px": self._width.value(),
            "zoom_height_px": self._height.value(),
            "zoom_scale": self._scale.value(),
        }

    def set_values(self, values: dict) -> None:
        for control, key in self._fields:
            if key not in values:
                continue
            blocked = control.blockSignals(True)
            if isinstance(control, QCheckBox):
                control.setChecked(values[key])
            else:
                control.setValue(values[key])
            control.blockSignals(blocked)


class ContextMenuOptions(QGroupBox):
    changed = Signal()

    def __init__(self, config: dict):
        super().__init__("Click Context Menu")
        self._enabled = QCheckBox("Enable click menu")
        self._enabled.setToolTip(
            "Choose left, right, double click, zoom or pause after dwelling. "
            "Choices are remembered across menus: "
            "choose the same option again to lock it; choose a locked option to unlock it. "
            "Zoom clicks with the chosen left/right button, waiting for one if none is chosen."
        )
        self._distance = QSpinBox()
        self._distance.setRange(48, 1000)
        self._distance.setSuffix(" px")
        self._distance.setToolTip("P: distance from the click point to each icon's centre.")
        self._size = QSpinBox()
        self._size.setRange(24, 200)
        self._size.setSuffix(" px")
        self._size.setToolTip(
            "Size of each menu icon target. Spacing grows if needed to prevent overlap."
        )
        self._lock_time = QDoubleSpinBox()
        self._lock_time.setRange(0.1, 60)
        self._lock_time.setSuffix(" s")
        self._lock_time.setToolTip(
            "S: how long the menu remains after an automatic click. Dwelling on an icon "
            "pauses dismissal. Choose a locked option to unlock without clicking."
        )
        self.set_values({**DwellSettings().to_config(), **config})
        grid = QGridLayout(self)
        grid.addWidget(self._enabled, 0, 0, 1, 4)
        for column, text, control in ((0, "Distance", self._distance), (2, "Stay", self._lock_time)):
            label = QLabel(text)
            label.setToolTip(control.toolTip())
            grid.addWidget(label, 1, column)
            grid.addWidget(control, 1, column + 1)
            control.valueChanged.connect(self.changed)
        self._enabled.toggled.connect(self.changed)
        label = QLabel("Icon size")
        label.setToolTip(self._size.toolTip())
        grid.addWidget(label, 2, 0)
        grid.addWidget(self._size, 2, 1)
        self._size.valueChanged.connect(self.changed)

    def values(self) -> dict:
        return {
            "context_menu_enabled": self._enabled.isChecked(),
            "context_menu_distance_px": self._distance.value(),
            "context_menu_size_px": self._size.value(),
            "context_menu_lock_time_s": self._lock_time.value(),
        }

    def set_values(self, values: dict) -> None:
        for control, key in (
            (self._enabled, "context_menu_enabled"),
            (self._distance, "context_menu_distance_px"),
            (self._size, "context_menu_size_px"),
            (self._lock_time, "context_menu_lock_time_s"),
        ):
            if key in values:
                blocked = control.blockSignals(True)
                if isinstance(control, QCheckBox):
                    control.setChecked(values[key])
                else:
                    control.setValue(values[key])
                control.blockSignals(blocked)


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
