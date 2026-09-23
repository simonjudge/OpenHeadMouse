"""Pluggable target rendering. Hit-testing is always a circle of diameter
`width_px` regardless of visual skin, so switching stimuli never changes the
ISO A/W math computed elsewhere (fitts_law/conditions.py, results.py).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

ACCENT_COLOR = QColor("#e53935")
MUTED_COLOR = QColor("#9e9e9e")

EMOJI_CHOICES = ["🎯", "🔵", "⭐️", "🎈", "😀", "🍎"]


class Stimulus:
    """Base class: draws itself inside `rect`, a square QRectF of side width_px."""

    def paint(self, painter: QPainter, rect: QRectF, is_current: bool) -> None:
        raise NotImplementedError


class CircleStimulus(Stimulus):
    """The ISO-standard look: a filled circle, accented when active."""

    def paint(self, painter: QPainter, rect: QRectF, is_current: bool) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(ACCENT_COLOR if is_current else MUTED_COLOR)
        painter.drawEllipse(rect)


class IconStimulus(Stimulus):
    """Draws a centered icon/emoji glyph, sized to fill the target circle."""

    def __init__(self, glyph: str = "🎯"):
        self.glyph = glyph

    def paint(self, painter: QPainter, rect: QRectF, is_current: bool) -> None:
        if not is_current:
            painter.setPen(Qt.NoPen)
            painter.setBrush(MUTED_COLOR)
            painter.drawEllipse(rect)
            return
        font = QFont()
        font.setPixelSize(int(rect.height() * 0.8))
        painter.setFont(font)
        painter.setPen(Qt.black)
        painter.drawText(rect, Qt.AlignCenter, self.glyph)


# Backward compatibility alias
EmojiStimulus = IconStimulus


class ImageStimulus(Stimulus):
    """Draws a user-chosen image scaled into the target circle."""

    def __init__(self, pixmap: QPixmap):
        self.pixmap = pixmap

    def paint(self, painter: QPainter, rect: QRectF, is_current: bool) -> None:
        if not is_current:
            painter.setPen(Qt.NoPen)
            painter.setBrush(MUTED_COLOR)
            painter.drawEllipse(rect)
            return
        scaled = self.pixmap.scaled(
            rect.size().toSize(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        target = QRectF(scaled.rect())
        target.moveCenter(rect.center())
        painter.drawPixmap(target.topLeft(), scaled)


STIMULUS_CHOICES = {
    "Circle (ISO standard)": lambda: CircleStimulus(),
    "Icon": lambda: IconStimulus(),
    "Custom image...": None,  # handled specially by the setup screen (QFileDialog)
}
