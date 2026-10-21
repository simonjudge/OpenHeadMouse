"""Borderless screenshot magnifier for the second stage of a dwell click."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .engine import DwellSettings, Point


class DwellZoom(QWidget):
    def __init__(self):
        super().__init__(None)
        self._image = QPixmap()
        self._source = QRect()
        self._scale = 4.0
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

    def open_at(self, position: Point, settings: DwellSettings) -> None:
        point = QPoint(round(position[0]), round(position[1]))
        screen = QGuiApplication.screenAt(point)
        if screen is None:
            raise RuntimeError("Cannot zoom: the pointer is not on a screen.")
        bounds = screen.geometry()
        width = min(settings.zoom_width_px, bounds.width())
        height = min(settings.zoom_height_px, bounds.height())
        self._scale = settings.zoom_scale
        source_width = math.ceil(width / self._scale)
        source_height = math.ceil(height / self._scale)
        source_x = min(max(point.x() - source_width // 2, bounds.left()),
                       bounds.right() - source_width + 1)
        source_y = min(max(point.y() - source_height // 2, bounds.top()),
                       bounds.bottom() - source_height + 1)
        self._source = QRect(source_x, source_y, source_width, source_height)
        self._image = screen.grabWindow(
            0, source_x - bounds.x(), source_y - bounds.y(), source_width, source_height
        )
        if self._image.isNull():
            raise RuntimeError(
                "Cannot capture the screen for zoom clicking. Check screen-recording permissions."
            )
        # Qt cursor/window coordinates are logical pixels, including on scaled displays.
        # Over the taskbar, show the box clear of taskbar previews (the capture is unchanged).
        from .context_menu import DwellContextMenu

        area = DwellContextMenu._clear_of_taskbar_previews(position, screen)
        if area.width() < width or area.height() < height:
            area = bounds
        self.setFixedSize(width, height)
        self.move(
            min(max(point.x() - width // 2, area.left()), area.right() - width + 1),
            min(max(point.y() - height // 2, area.top()), area.bottom() - height + 1),
        )
        self.show()
        self.raise_()
        self.update()

    def target_at(self, position: Point) -> Point | None:
        x, y = position[0] - self.x(), position[1] - self.y()
        if not (0 <= x < self.width() and 0 <= y < self.height()):
            return None
        ratio = self._image.devicePixelRatio()
        return (
            self._source.x() + math.floor(x * ratio / self._scale) / ratio,
            self._source.y() + math.floor(y * ratio / self._scale) / ratio,
        )

    @property
    def source_origin(self) -> QPoint:
        return self._source.topLeft()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        ratio = self._image.devicePixelRatio()
        painter.drawPixmap(
            QRectF(0, 0, self._image.width() * self._scale / ratio,
                   self._image.height() * self._scale / ratio),
            self._image,
            QRectF(self._image.rect()),
        )
