"""Timed initial-rest and range-of-movement tasks shown before Fitts trials."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.clock import host_time


def build_pretest_stages(
    include_initial_rest: bool,
    include_range_of_movement: bool,
    move_duration_s: float,
    hold_duration_s: float,
) -> list[dict]:
    stages: list[dict] = []
    if include_initial_rest:
        stages.extend(
            [
                {
                    "phase": "initial_rest.move",
                    "task": "initial_rest",
                    "segment": "move",
                    "direction": None,
                    "duration_s": move_duration_s,
                },
                {
                    "phase": "initial_rest.hold",
                    "task": "initial_rest",
                    "segment": "hold",
                    "direction": None,
                    "duration_s": hold_duration_s,
                },
            ]
        )
    if include_range_of_movement:
        for direction in ("left", "right", "up", "down"):
            stages.extend(
                [
                    {
                        "phase": f"range_of_movement.{direction}.move",
                        "task": "range_of_movement",
                        "segment": "move",
                        "direction": direction,
                        "duration_s": move_duration_s,
                    },
                    {
                        "phase": f"range_of_movement.{direction}.hold",
                        "task": "range_of_movement",
                        "segment": "hold",
                        "direction": direction,
                        "duration_s": hold_duration_s,
                    },
                ]
            )
    return stages


class PreTestTaskOverlay(QWidget):
    """Displays pre-test instructions and records cursor movement by phase."""

    phase_changed = Signal(str)
    completed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        stages: list[dict],
        tick_ms: int = 50,
        clock=time.monotonic,
        host_clock=host_time,
        parent=None,
        cursor_position=None,
    ):
        super().__init__(parent)
        if not stages:
            raise ValueError("at least one pre-test stage is required")
        self._stages = [dict(stage) for stage in stages]
        self._clock = clock
        self._host_clock = host_clock
        # Polled every tick: mouse-move events are not reliably delivered while
        # OpenHeadMouse (or another access method) moves the hidden cursor.
        self._cursor_position = cursor_position or (lambda: self.mapFromGlobal(QCursor.pos()))
        self._index = -1
        self._phase_start = 0.0
        self.records: list[dict] = []

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.BlankCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(tick_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def current_stage(self) -> dict | None:
        if 0 <= self._index < len(self._stages):
            return self._stages[self._index]
        return None

    def start(self) -> None:
        self._start_stage(0)
        self._timer.start()
        self.setFocus()

    def stop(self, *, cancelled: bool = False) -> None:
        self._timer.stop()
        if cancelled and self.records and self.records[-1]["end_host_time"] is None:
            self.records[-1]["end_host_time"] = self._host_clock()
            self.records[-1]["cancelled"] = True

    def _start_stage(self, index: int) -> None:
        self._index = index
        self._phase_start = self._clock()
        stage = self._stages[index]
        self.records.append(
            {
                **stage,
                "start_host_time": self._host_clock(),
                "end_host_time": None,
                "cursor_trajectory": [],
            }
        )
        self.phase_changed.emit(stage["phase"])
        self.update()

    def _tick(self) -> None:
        stage = self.current_stage
        if stage is None:
            return
        position = self._cursor_position()
        if position is not None:
            self._record_cursor(position.x(), position.y())
        if self._clock() - self._phase_start < stage["duration_s"]:
            self.update()
            return

        self.records[-1]["end_host_time"] = self._host_clock()
        next_index = self._index + 1
        if next_index >= len(self._stages):
            self._timer.stop()
            self.phase_changed.emit("")
            self.completed.emit()
            return
        self._start_stage(next_index)

    def mouseMoveEvent(self, event) -> None:
        if self.current_stage is None:
            return
        position = event.position()
        self._record_cursor(position.x(), position.y())

    def _record_cursor(self, x: float, y: float) -> None:
        self.records[-1]["cursor_trajectory"].append(
            {"host_time": self._host_clock(), "x_px": float(x), "y_px": float(y)}
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.stop(cancelled=True)
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def _instruction(self) -> str:
        stage = self.current_stage
        if stage is None:
            return ""
        if stage["task"] == "initial_rest":
            if stage["segment"] == "move":
                return "Move your head into a comfortable neutral rest position"
            return "Keep your head still in the neutral rest position"
        direction = stage["direction"]
        if stage["segment"] == "move":
            return f"Move your head as far {direction.upper()} as you comfortably can"
        return f"Keep your head at its furthest {direction.upper()} position"

    def _status(self) -> str:
        stage = self.current_stage
        if stage is None:
            return ""
        remaining = stage["duration_s"] - (self._clock() - self._phase_start)
        action = "Ready" if stage["segment"] == "move" else "Hold"
        return f"{action}: {max(1, math.ceil(remaining))}"

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        stage = self.current_stage
        if stage is None:
            painter.end()
            return

        if stage["task"] == "initial_rest":
            self._paint_rest_target(painter)
        else:
            self._paint_direction_band(painter, stage["direction"])

        message_font = QFont()
        message_font.setPointSize(22)
        painter.setFont(message_font)
        painter.setPen(QColor("white"))
        message_rect = QRectF(
            self.width() * 0.1,
            self.height() * 0.2,
            self.width() * 0.8,
            120,
        )
        painter.drawText(
            message_rect,
            Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
            self._instruction(),
        )

        status_font = QFont()
        status_font.setPointSize(40)
        status_font.setBold(True)
        painter.setFont(status_font)
        status_rect = QRectF(
            self.width() * 0.1,
            self.height() * 0.68,
            self.width() * 0.8,
            80,
        )
        painter.drawText(
            status_rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            self._status(),
        )

        hint_font = QFont()
        hint_font.setPointSize(12)
        painter.setFont(hint_font)
        painter.setPen(QColor("#999999"))
        painter.drawText(
            QRectF(0, self.height() - 60, self.width(), 40),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "Press Esc to cancel",
        )
        painter.end()

    def _paint_rest_target(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = max(20.0, min(self.width(), self.height()) * 0.05)
        for index, color in enumerate(
            (QColor("#c62828"), QColor("white"), QColor("#c62828"))
        ):
            size = radius * (1.0 - index / 3.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(center, size, size)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawLine(
            QPointF(center.x() - radius * 1.4, center.y()),
            QPointF(center.x() + radius * 1.4, center.y()),
        )
        painter.drawLine(
            QPointF(center.x(), center.y() - radius * 1.4),
            QPointF(center.x(), center.y() + radius * 1.4),
        )

    def _paint_direction_band(self, painter: QPainter, direction: str) -> None:
        thickness = max(30.0, min(self.width(), self.height()) * 0.1)
        if direction == "left":
            band = QRectF(0, 0, thickness, self.height())
        elif direction == "right":
            band = QRectF(self.width() - thickness, 0, thickness, self.height())
        elif direction == "up":
            band = QRectF(0, 0, self.width(), thickness)
        else:
            band = QRectF(0, self.height() - thickness, self.width(), thickness)
        painter.fillRect(band, QColor("#c62828"))
