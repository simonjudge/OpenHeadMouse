"""Qt service that polls the system pointer and performs dwell clicks."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication

from .context_menu import DwellContextMenu, DwellResumeButton
from .engine import DwellEngine, DwellSettings
from .overlay import DwellRing
from .zoom import DwellZoom

HOVER_BEFORE_PRESS_MS = 120


class DwellClickService(QObject):
    running_requested = Signal(bool)
    error_occurred = Signal(str)
    running_changed = Signal(bool)

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
        self._overlay = DwellRing(
            color=self.settings.ring_color,
            diameter_px=self.settings.ring_diameter_px,
            thickness_px=self.settings.ring_thickness_px,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll)
        self._zoom = DwellZoom()
        self._context_menu = DwellContextMenu()
        self._resume_button = DwellResumeButton()
        self._paused = False
        self._resume_blocked = False
        self._menu_target: tuple[float, float] | None = None
        self._menu_deadline: float | None = None
        self._menu_click_done = False
        self._zoom_pending = False
        self._menu_last_time = self._clock()
        self._hovered_choice: str | None = None
        self._blocked_choice: str | None = None
        self._last_button: str | None = None
        self._locked_button: str | None = None
        self._zoom_selected = False
        self._zoom_once_armed = False
        self._zoom_locked = self.settings.zoom_enabled
        self._button = "left"
        self._show_after_action = False
        self._click_origin: QPoint | None = None
        self._pending_position: tuple[float, float] | None = None
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(50)
        self._capture_timer.timeout.connect(self._open_zoom)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(50)
        self._click_timer.timeout.connect(self._complete_zoom_click)
        # Custom-drawn title bars (e.g. Chromium, WinUI close buttons) ignore a press that
        # arrives before they have seen the pointer hover, so pause after repositioning.
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.setInterval(HOVER_BEFORE_PRESS_MS)
        self._press_timer.timeout.connect(self._press_after_hover)
        self._press_target: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.running_requested.connect(self._set_running)

    @property
    def is_running(self) -> bool:
        """Enabled, including while paused from the click menu."""
        return self._timer.isActive()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_active(self) -> bool:
        """Running and able to click (not paused)."""
        return self.is_running and not self._paused

    def start(self) -> None:
        if self._paused and self.is_running:
            self.resume()
            return
        self._clear_pause()
        self._cancel_action()
        self._reset_choices()
        if self._mouse_controller is None:
            from pynput import mouse

            self._mouse_controller = mouse.Controller()
        self._engine.reset()
        self._timer.start()
        self.running_changed.emit(True)
        self._poll()

    def stop(self) -> None:
        self._timer.stop()
        self._clear_pause()
        self._cancel_action()
        self._reset_choices()
        self._engine.reset()
        self._overlay.hide()
        self.running_changed.emit(False)

    def request_running(self, running: bool) -> None:
        """Request a state change safely from any thread."""
        self.running_requested.emit(running)

    def pause(self) -> None:
        """Stop clicking but keep polling so the floating Resume button works."""
        if not self.is_running or self._paused:
            return
        self._cancel_action()
        self._hide_menu()
        self._overlay.hide()
        self._paused = True
        position = self._position_provider()
        try:
            self._resume_button.show_near(position, self.settings.context_menu_size_px)
        except RuntimeError as exc:
            logging.getLogger(__name__).error("%s", exc)
            self.error_occurred.emit(str(exc))
        self._resume_blocked = self._resume_button.contains(position)
        self._engine.reset()
        self.running_changed.emit(False)

    def resume(self) -> None:
        if not self._paused:
            return
        self._clear_pause()
        self._engine.inhibit_until_moved(self._position_provider())
        self.running_changed.emit(True)

    def _clear_pause(self) -> None:
        self._paused = False
        self._resume_blocked = False
        self._resume_button.hide()

    def _poll_paused(self) -> None:
        position = self._position_provider()
        if not self._resume_button.contains(position):
            self._resume_blocked = False
            self._engine.reset()
            self._overlay.hide()
            return
        if self._resume_blocked:
            self._engine.reset()
            self._overlay.hide()
            return
        update = self._engine.update(position, self._clock())
        if update.visible:
            self._overlay.display(update.position, update.progress)
        else:
            self._overlay.hide()
        if update.click:
            self._overlay.hide()
            self.resume()

    @Slot(bool)
    def _set_running(self, running: bool) -> None:
        if running and not self.is_running:
            self.start()
        elif not running and self.is_running:
            self.stop()

    def update_settings(self, settings: DwellSettings) -> None:
        if settings == self.settings:
            return
        self._cancel_action()
        self.settings = settings
        self._reset_choices()
        self._engine.update_settings(settings)
        self._overlay.set_color(settings.ring_color)
        self._overlay.set_size(settings.ring_diameter_px, settings.ring_thickness_px)
        if self._paused:
            self._refresh_resume_button()
        elif self.is_running:
            self._poll()

    def _refresh_resume_button(self) -> None:
        if not self._paused:
            return
        position = self._position_provider()
        try:
            self._resume_button.show_near(position, self.settings.context_menu_size_px)
        except RuntimeError as exc:
            logging.getLogger(__name__).error("%s", exc)
            self.error_occurred.emit(str(exc))

    @staticmethod
    def _cursor_position() -> tuple[float, float]:
        point = QCursor.pos()
        return float(point.x()), float(point.y())

    def _poll(self) -> None:
        if (
            self._capture_timer.isActive() or self._click_timer.isActive()
            or self._press_timer.isActive()
        ):
            return
        if self._paused:
            self._poll_paused()
            return
        if self._context_menu.is_visible() and self._poll_menu():
            return
        update = self._engine.update(self._position_provider(), self._clock())
        if update.visible:
            self._overlay.display(update.position, update.progress)
        else:
            self._overlay.hide()
        if not update.click:
            return
        if self._zoom.isVisible():
            target = self._zoom.target_at(update.position)
            self._zoom.hide()
            if target is not None:
                self._pending_position = target
                self._click_timer.start()
            else:
                self._show_after_action = False
        elif self.settings.context_menu_enabled:
            if self._locked_button is not None:
                self._begin_action(update.position, automatic=True)
            else:
                self._show_menu(update.position)
        else:
            self._button = "left"
            self._begin_action(update.position)

    def _begin_action(
        self, position: tuple[float, float], *, from_menu: bool = False,
        zoom_once: bool = False, automatic: bool = False,
    ) -> None:
        self._hide_menu()
        self._overlay.hide()
        self._show_after_action = automatic
        if automatic and self._locked_button is not None:
            self._button = self._locked_button
        use_zoom = self._zoom_locked or self._zoom_once_armed or zoom_once
        self._zoom_once_armed = False
        if use_zoom:
            self._pending_position = position
            # Let the compositor remove the menu/ring before capturing or clicking.
            self._capture_timer.start()
        elif from_menu:
            self._pending_position = position
            self._click_origin = QPoint(round(position[0]), round(position[1]))
            self._click_timer.start()
        else:
            self._click()
            self._action_completed(position)

    def _show_menu(self, position: tuple[float, float], *, timed: bool = False) -> None:
        try:
            self._context_menu.show_at(
                position, self.settings.context_menu_distance_px,
                self._locked_button, self._zoom_locked, self.settings.context_menu_size_px,
            )
        except RuntimeError as exc:
            self._report_error(str(exc))
            return
        self._menu_target = position
        self._menu_click_done = timed
        now = self._clock()
        self._menu_last_time = now
        self._menu_deadline = now + self.settings.context_menu_lock_time_s if timed else None
        self._hovered_choice = None
        self._blocked_choice = self._context_menu.choice_at(self._position_provider())
        self._engine.inhibit_until_moved(self._position_provider())

    def _hide_menu(self) -> None:
        self._context_menu.hide()
        self._menu_deadline = None
        self._menu_target = None
        self._menu_click_done = False
        self._zoom_pending = False
        self._hovered_choice = None
        self._blocked_choice = None

    def _reset_choices(self) -> None:
        self._hide_menu()
        self._last_button = None
        self._locked_button = None
        self._button = "left"
        self._zoom_selected = False
        self._zoom_once_armed = False
        self._zoom_locked = self.settings.zoom_enabled

    def _poll_menu(self) -> bool:
        position = self._position_provider()
        choice = self._context_menu.choice_at(position)
        now = self._clock()
        if self._menu_deadline is not None:
            # Give an icon dwell time to finish even near the dismissal deadline.
            if (
                (choice is not None and choice != self._blocked_choice)
                or self._hovered_choice is not None
            ):
                self._menu_deadline += max(0.0, now - self._menu_last_time)
            if now >= self._menu_deadline:
                self._hide_menu()
                return False
        self._menu_last_time = now
        if self._blocked_choice is not None:
            if choice == self._blocked_choice:
                self._engine.inhibit_until_moved(position)
                self._overlay.hide()
                return True
            self._blocked_choice = None
        if choice != self._hovered_choice:
            self._engine.reset()
            self._hovered_choice = choice
        update = self._engine.update(position, now)
        if update.visible:
            self._overlay.display(position, update.progress)
        else:
            self._overlay.hide()
        if not update.click:
            return True
        target = self._menu_target
        if choice is None:
            self._hide_menu()
            if self._locked_button is not None:
                self._begin_action(position, automatic=True)
            return True
        if target is None:
            raise RuntimeError("Visible click menu has no target")
        if choice == "pause":
            self.pause()
        elif choice == "zoom":
            if self._zoom_locked:
                self._zoom_locked = False
                self._zoom_selected = False
                self._zoom_once_armed = False
                self._dismiss_selection(position)
            else:
                self._zoom_locked = self._zoom_selected
                self._zoom_selected = True
                if self._menu_click_done:
                    self._zoom_once_armed = not self._zoom_locked
                    self._dismiss_selection(position)
                elif self._locked_button is None and self._last_button is None:
                    # No click button chosen yet: keep the menu open until one is.
                    self._zoom_pending = True
                    self._context_menu.highlight(None, True)
                    self._overlay.hide()
                    self._hovered_choice = None
                    self._blocked_choice = choice
                    self._engine.inhibit_until_moved(position)
                else:
                    self._button = self._locked_button or self._last_button
                    self._begin_action(target, from_menu=True, zoom_once=True)
        elif choice == self._locked_button:
            self._locked_button = None
            self._last_button = None
            self._dismiss_selection(position)
        else:
            self._locked_button = choice if choice == self._last_button else None
            self._last_button = choice
            self._button = choice
            self._begin_action(target, from_menu=True, zoom_once=self._zoom_pending)
        return True

    def _dismiss_selection(self, position: tuple[float, float]) -> None:
        self._hide_menu()
        self._overlay.hide()
        self._engine.inhibit_until_moved(position)

    def _cancel_action(self) -> None:
        self._capture_timer.stop()
        self._click_timer.stop()
        self._press_timer.stop()
        self._press_target = None
        self._pending_position = None
        self._click_origin = None
        self._show_after_action = False
        self._zoom.hide()

    def _open_zoom(self) -> None:
        position = self._pending_position
        self._pending_position = None
        if position is None:
            return
        try:
            self._zoom.open_at(position, self.settings)
        except RuntimeError as exc:
            self._report_error(str(exc))
        self._engine.inhibit_until_moved(self._position_provider())

    def _complete_zoom_click(self) -> None:
        position = self._pending_position
        self._pending_position = None
        if position is None:
            return
        anchor = self._click_origin if self._click_origin is not None else self._zoom.source_origin
        self._click_origin = None
        screen = QGuiApplication.screenAt(anchor)
        if screen is None:
            self._report_error("Cannot complete click: the captured screen is no longer available.")
            return
        # Use the capture origin so fractional-DPI rounding agrees with the image.
        # pynput uses native pixels except on macOS, which uses points.
        QCursor.setPos(anchor)
        if QCursor.pos() != anchor:
            self._report_error(
                "Cannot move the pointer for clicking. Check pointer-control permissions."
            )
            return
        ratio = screen.devicePixelRatio() if sys.platform != "darwin" else 1.0
        dx, dy = (position[0] - anchor.x()) * ratio, (position[1] - anchor.y()) * ratio
        if sys.platform != "darwin":
            dx, dy = round(dx), round(dy)
        native_x, native_y = self._mouse_controller.position
        expected = (native_x + dx, native_y + dy)
        self._mouse_controller.position = expected
        if self._mouse_controller.position != expected:
            self._report_error("Cannot position the pointer precisely; the click was cancelled.")
            return
        self._press_target = (position, expected)
        self._press_timer.start()

    def _press_after_hover(self) -> None:
        target = self._press_target
        self._press_target = None
        if target is None:
            return
        position, expected = target
        # Head movement during the hover pause must not shift the click.
        if self._mouse_controller.position != expected:
            self._mouse_controller.position = expected
        self._engine.inhibit_until_moved(self._position_provider())
        self._click()
        self._action_completed(position)

    def _action_completed(self, position: tuple[float, float]) -> None:
        show_menu = self._show_after_action
        self._show_after_action = False
        if show_menu and self.settings.context_menu_enabled:
            self._show_menu(position, timed=True)

    def _report_error(self, message: str) -> None:
        self._cancel_action()
        self._hide_menu()
        self._engine.inhibit_until_moved(self._position_provider())
        logging.getLogger(__name__).error("%s", message)
        self.error_occurred.emit(message)

    def _click(self) -> None:
        from pynput.mouse import Button

        if self._button == "double":
            self._mouse_controller.click(Button.left, 2)
        else:
            self._mouse_controller.click(Button.right if self._button == "right" else Button.left)
