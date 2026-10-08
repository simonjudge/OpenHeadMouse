"""Qt service that polls the system pointer and performs dwell clicks."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QCursor

from .engine import DwellEngine, DwellSettings
from .overlay import DwellRing


class DwellClickService(QObject):
    running_requested = Signal(bool)

    def __init__(
        self,
        settings: DwellSettings | None = None,
        *,
        mouse_controller=None,
        position_provider: Callable[[], tuple[float, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval_ms: int = 16,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.settings = settings or DwellSettings()
        self._engine = DwellEngine(self.settings)
        self._mouse_controller = mouse_controller
        self._position_provider = position_provider or self._cursor_position
        self._clock = clock
        self._overlay = DwellRing(color=self.settings.ring_color)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll)
        self.running_requested.connect(self._set_running)

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self._mouse_controller is None:
            from pynput import mouse

            self._mouse_controller = mouse.Controller()
        self._engine.reset()
        self._timer.start()
        self._poll()

    def stop(self) -> None:
        self._timer.stop()
        self._engine.reset()
        self._overlay.hide()

    def request_running(self, running: bool) -> None:
        """Request a state change safely from any thread."""
        self.running_requested.emit(running)

    @Slot(bool)
    def _set_running(self, running: bool) -> None:
        if running and not self.is_running:
            self.start()
        elif not running and self.is_running:
            self.stop()

    def update_settings(self, settings: DwellSettings) -> None:
        was_running = self.is_running
        self.settings = settings
        self._engine.update_settings(settings)
        self._overlay.set_color(settings.ring_color)
        if was_running:
            self._poll()

    @staticmethod
    def _cursor_position() -> tuple[float, float]:
        point = QCursor.pos()
        return float(point.x()), float(point.y())

    def _poll(self) -> None:
        update = self._engine.update(self._position_provider(), self._clock())
        if update.visible:
            self._overlay.display(update.position, update.progress)
        else:
            self._overlay.hide()
        if update.click:
            from pynput.mouse import Button

            self._mouse_controller.click(Button.left)
