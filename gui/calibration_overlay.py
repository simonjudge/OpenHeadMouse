"""Fullscreen overlay shown during initial (neutral-pose) calibration.

Displays a centred target with instructions. Timing runs in two phases:
a read period so the user can read the message, then a capture countdown
during which the App records the at-rest head pose. This widget only
handles presentation and timing; sample collection is done by the caller.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

CALIBRATION_MESSAGE = (
    "Please put your head in a neutral position and do not move your head for {seconds:g} seconds"
)


class CalibrationOverlay(QWidget):
    capture_started = Signal()
    finished = Signal()
    cancelled = Signal()

    PHASE_READ = "read"
    PHASE_CAPTURE = "capture"
    PHASE_DONE = "done"

    def __init__(
        self,
        read_duration_s: float = 5.0,
        capture_duration_s: float = 5.0,
        message: str | None = None,
        tick_ms: int = 50,
        clock=time.monotonic,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)
        self.setWindowTitle("HeadMouse Calibration")

        self.read_duration_s = float(read_duration_s)
        self.capture_duration_s = float(capture_duration_s)
        self.message = message if message is not None else CALIBRATION_MESSAGE.format(
            seconds=self.capture_duration_s
        )
        self._clock = clock
        self._phase = self.PHASE_READ
        self._phase_start = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(tick_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def phase(self) -> str:
        return self._phase

    def start(self, geometry: QRect | None = None) -> None:
        if geometry is not None:
            self.setGeometry(geometry)
        self._phase = self.PHASE_READ
        self._phase_start = self._clock()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._timer.start()
        self._tick()

    def stop(self) -> None:
        """Close silently (no signals), e.g. when the provider is stopped."""
        self._timer.stop()
        self._phase = self.PHASE_DONE
        self.close()

    def status_text(self) -> str:
        if self._phase == self.PHASE_READ:
            return "Get ready…"
        if self._phase == self.PHASE_CAPTURE:
            remaining = self.capture_duration_s - (self._clock() - self._phase_start)
            return f"Hold still: {max(1, math.ceil(remaining))}"
        return ""

    def _tick(self) -> None:
        elapsed = self._clock() - self._phase_start
        if self._phase == self.PHASE_READ and elapsed >= self.read_duration_s:
            self._phase = self.PHASE_CAPTURE
            self._phase_start = self._clock()
            self.capture_started.emit()
        elif self._phase == self.PHASE_CAPTURE and elapsed >= self.capture_duration_s:
            self._timer.stop()
            self._phase = self.PHASE_DONE
            self.close()
            self.finished.emit()
            return
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._phase != self.PHASE_DONE:
            self.stop()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(20, 20, 20))

        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = max(20.0, min(self.width(), self.height()) * 0.05)
        for i, color in enumerate((QColor("#c62828"), QColor("white"), QColor("#c62828"))):
            r = radius * (1.0 - i / 3.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(center, r, r)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawLine(QPointF(center.x() - radius * 1.4, center.y()), QPointF(center.x() + radius * 1.4, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - radius * 1.4), QPointF(center.x(), center.y() + radius * 1.4))

        text_width = self.width() * 0.8
        message_font = QFont()
        message_font.setPointSize(22)
        painter.setFont(message_font)
        painter.setPen(QColor("white"))
        message_rect = QRectF(center.x() - text_width / 2, center.y() - radius * 2 - 140, text_width, 120)
        painter.drawText(message_rect, Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, self.message)

        status_font = QFont()
        status_font.setPointSize(40 if self._phase == self.PHASE_CAPTURE else 24)
        status_font.setBold(True)
        painter.setFont(status_font)
        status_rect = QRectF(center.x() - text_width / 2, center.y() + radius * 2, text_width, 80)
        painter.drawText(status_rect, Qt.AlignHCenter | Qt.AlignTop, self.status_text())

        hint_font = QFont()
        hint_font.setPointSize(12)
        painter.setFont(hint_font)
        painter.setPen(QColor("#999999"))
        hint_rect = QRectF(0, self.height() - 60, self.width(), 40)
        painter.drawText(hint_rect, Qt.AlignHCenter | Qt.AlignVCenter, "Press Esc to cancel")
        painter.end()
