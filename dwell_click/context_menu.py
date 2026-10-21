"""Dwell-selectable, non-activating click choices and the paused-state resume button."""

import math
import logging

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QToolButton

from .engine import Point


def _icon(choice: str, size: int, dark: bool = False) -> QIcon:
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(size / 28, size / 28)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor("#ffffff" if dark else "#202020")
    accent = QColor("#66c2ff" if dark else "#0078d4")
    painter.setPen(QPen(ink, 2))
    if choice == "zoom":
        painter.drawEllipse(4, 3, 14, 14)
        painter.drawLine(17, 16, 24, 24)
        painter.drawLine(8, 10, 14, 10)
        painter.drawLine(11, 7, 11, 13)
    elif choice == "pause":
        painter.fillRect(8, 5, 4, 18, ink)
        painter.fillRect(16, 5, 4, 18, ink)
    elif choice == "resume":
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(9, 5), QPointF(23, 14), QPointF(9, 23)]))
    elif choice == "double":
        painter.drawRoundedRect(2, 2, 14, 24, 6, 6)
        painter.fillRect(4, 5, 5, 7, accent)
        painter.drawLine(9, 3, 9, 13)
        # Draw the "2" as a path so it does not depend on installed fonts.
        two = QPainterPath(QPointF(18.5, 9.5))
        two.cubicTo(18.5, 5.0, 26.0, 5.0, 26.0, 9.5)
        two.cubicTo(26.0, 13.0, 21.0, 16.0, 18.5, 21.0)
        two.lineTo(26.5, 21.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(two)
    else:
        painter.drawRoundedRect(6, 2, 16, 24, 7, 7)
        painter.fillRect(8 if choice == "left" else 15, 5, 5, 7, accent)
        painter.drawLine(14, 3, 14, 13)
    painter.end()
    return QIcon(image)


def _make_button(name: str, help_text: str) -> QToolButton:
    button = QToolButton()
    button.setWindowFlags(
        Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
    )
    button.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    button.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    button.setToolTip(help_text)
    button.setAccessibleName(name)
    return button


def _style(button: QToolButton, dark: bool, active: bool) -> None:
    tint = "255, 255, 255" if dark else "0, 0, 0"
    button.setStyleSheet(
        "QToolButton { background: rgba(" + tint + ", 51); padding: 0px; border: 2px solid "
        + (("#66c2ff" if dark else "#0078d4") if active else "transparent")
        + "; border-radius: 6px; }"
    )


def _paint(button: QToolButton, choice: str, size: int, dark: bool) -> None:
    button.setFixedSize(size, size)
    artwork_size = max(1, round(size * 28 / 36))
    button.setIcon(_icon(choice, artwork_size, dark))
    button.setIconSize(QSize(artwork_size, artwork_size))


# Angles (degrees anticlockwise from the pointer's right) for each menu choice.
MENU_ANGLES = {"left": 135, "zoom": 90, "right": 45, "double": 225, "pause": 315}
# Room left for taskbar window previews (thumbnail plus title) next to the taskbar.
TASKBAR_PREVIEW_CLEARANCE_PX = 260


class DwellContextMenu:
    def __init__(self):
        self.buttons = {}
        self._dark_backgrounds: dict[str, bool] = {}
        for choice, help_text in (
            ("left", "Left click. Choose again at the next target to lock; choose while locked to unlock."),
            ("zoom", "Magnify this click using the chosen click button (choose one if prompted). "
                     "Choose again later to lock zoom; choose while locked to unlock zoom."),
            ("right", "Right click. Choose again at the next target to lock; choose while locked to unlock."),
            ("double", "Double click. Choose again at the next target to lock; choose while locked to unlock."),
            ("pause", "Pause dwell clicking without clicking. Dwell on the Resume button in the "
                      "screen's bottom-right corner, or use Start in the tray menu, to resume."),
        ):
            self.buttons[choice] = _make_button(choice, help_text)

    @staticmethod
    def _clear_of_taskbar_previews(position: Point, screen) -> QRect:
        """Usable area, excluding the strip where taskbar previews pop up for a taskbar target.

        Windows draws taskbar previews above every normal window (even topmost ones), so the
        menu is moved clear of them instead of trying to stay on top.
        """
        bounds = screen.availableGeometry()
        x, y = round(position[0]), round(position[1])
        if bounds.contains(QPoint(x, y)):
            return bounds
        vertical = min(TASKBAR_PREVIEW_CLEARANCE_PX, bounds.height() // 2)
        horizontal = min(TASKBAR_PREVIEW_CLEARANCE_PX, bounds.width() // 2)
        if y > bounds.bottom():
            bounds.setBottom(bounds.bottom() - vertical)
        elif y < bounds.top():
            bounds.setTop(bounds.top() + vertical)
        elif x > bounds.right():
            bounds.setRight(bounds.right() - horizontal)
        elif x < bounds.left():
            bounds.setLeft(bounds.left() + horizontal)
        return bounds

    def show_at(
        self, position: Point, distance: int, locked: str | None, zoom: bool, size: int = 54,
    ) -> None:
        screen = QGuiApplication.screenAt(QPoint(round(position[0]), round(position[1])))
        if screen is None:
            raise RuntimeError("Cannot show click menu: the pointer is not on a screen.")
        bounds = self._clear_of_taskbar_previews(position, screen)
        # Icons span (1 + sin 45°) * distance + size vertically and 2 * cos 45° * distance + size
        # horizontally, with distance >= size * sqrt(2); keep a pixel spare for rounding.
        size = max(1, min(
            size,
            (bounds.width() - 2) // 3,
            math.floor((bounds.height() - 2) / (2 + math.sqrt(2))),
        ))
        # Adjacent square targets must not overlap, even with larger icons.
        distance = min(
            max(distance, math.ceil(size * math.sqrt(2))),
            math.floor((bounds.width() - size) / math.sqrt(2)),
            math.floor((bounds.height() - size) / (1 + math.sqrt(0.5))),
        )
        rects = {}
        for choice in self.buttons:
            angle = math.radians(MENU_ANGLES[choice])
            rects[choice] = QRect(
                round(position[0] + distance * math.cos(angle)) - size // 2,
                round(position[1] - distance * math.sin(angle)) - size // 2,
                size, size,
            )
        extent = QRect()
        for rect in rects.values():
            extent = extent.united(rect)
        dx = max(bounds.left() - extent.left(), min(0, bounds.right() - extent.right()))
        dy = max(bounds.top() - extent.top(), min(0, bounds.bottom() - extent.bottom()))
        self.hide()
        for choice, button in self.buttons.items():
            geometry = rects[choice].translated(dx, dy)
            dark = self._is_dark_background(screen, geometry)
            self._dark_backgrounds[choice] = dark
            _paint(button, choice, size, dark)
            button.move(geometry.topLeft())
            button.show()
            button.raise_()
        self.highlight(locked, zoom)

    @staticmethod
    def _is_dark_background(screen, geometry: QRect) -> bool:
        origin = screen.geometry().topLeft()
        image = screen.grabWindow(
            0, geometry.x() - origin.x(), geometry.y() - origin.y(),
            geometry.width(), geometry.height(),
        ).toImage()
        if image.isNull():
            logging.getLogger(__name__).warning(
                "Cannot sample the click-menu background; using the application colour scheme."
            )
            return QGuiApplication.palette().window().color().lightnessF() < 0.5
        image = image.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        brightness = 0.0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                brightness += 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
        return brightness / (image.width() * image.height()) < 0.5

    def highlight(self, locked: str | None, zoom: bool) -> None:
        for choice, button in self.buttons.items():
            active = choice == locked or (choice == "zoom" and zoom)
            _style(button, self._dark_backgrounds[choice], active)

    def choice_at(self, position: Point) -> str | None:
        point = QPoint(round(position[0]), round(position[1]))
        return next((choice for choice, button in self.buttons.items()
                     if button.isVisible() and button.geometry().contains(point)), None)

    def is_visible(self) -> bool:
        return any(button.isVisible() for button in self.buttons.values())

    def hide(self) -> None:
        for button in self.buttons.values():
            button.hide()


class DwellResumeButton:
    """Floating, dwell-selectable button shown while dwell clicking is paused."""

    MARGIN_PX = 8

    def __init__(self):
        self.button = _make_button(
            "resume", "Dwell clicking is paused. Dwell here, or use Start in the tray menu, to resume."
        )

    def show_near(self, position: Point, size: int) -> None:
        screen = QGuiApplication.screenAt(QPoint(round(position[0]), round(position[1])))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("Cannot show the resume button: no screen is available.")
        bounds = screen.availableGeometry()
        size = max(1, min(size, bounds.width(), bounds.height()))
        geometry = QRect(
            bounds.right() - size - self.MARGIN_PX + 1,
            bounds.bottom() - size - self.MARGIN_PX + 1,
            size, size,
        ).intersected(bounds)
        dark = DwellContextMenu._is_dark_background(screen, geometry)
        _paint(self.button, "resume", geometry.width(), dark)
        _style(self.button, dark, True)
        self.button.move(geometry.topLeft())
        self.button.show()
        self.button.raise_()

    def contains(self, position: Point) -> bool:
        point = QPoint(round(position[0]), round(position[1]))
        return self.button.isVisible() and self.button.geometry().contains(point)

    def is_visible(self) -> bool:
        return self.button.isVisible()

    def hide(self) -> None:
        self.button.hide()
