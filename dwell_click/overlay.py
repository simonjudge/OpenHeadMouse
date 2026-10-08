"""Transparent dwell-progress ring."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DwellRing(QWidget):
    def __init__(
        self,
        color: str = "#ff0000",
        diameter_px: int = 40,
        thickness_px: int = 4,
    ):
        super().__init__(None)
        self._color = QColor(color)
        if not self._color.isValid():
            raise ValueError(f"Invalid dwell ring color: {color}")
        self._diameter_px = diameter_px
        self._thickness_px = thickness_px
        self._progress = 0.0
        extent = diameter_px + thickness_px * 2
        self.setFixedSize(extent, extent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def set_color(self, color: str) -> None:
        parsed = QColor(color)
        if not parsed.isValid():
            raise ValueError(f"Invalid dwell ring color: {color}")
        self._color = parsed
        self.update()

    def display(self, cursor_position: tuple[float, float], progress: float) -> None:
        self._progress = max(0.0, min(1.0, progress))
        center = QPoint(round(cursor_position[0]), round(cursor_position[1]))
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = self._thickness_px / 2 + 1
        ring = QRectF(
            inset,
            inset,
            self.width() - inset * 2,
            self.height() - inset * 2,
        )

        background = QColor(self._color)
        background.setAlpha(45)
        painter.setPen(
            QPen(
                background,
                self._thickness_px,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawEllipse(ring)

        foreground = QColor(self._color)
        foreground.setAlpha(180)
        painter.setPen(
            QPen(
                foreground,
                self._thickness_px,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawArc(ring, 90 * 16, round(-360 * 16 * self._progress))
