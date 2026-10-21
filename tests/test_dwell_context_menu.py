from dataclasses import replace
from types import SimpleNamespace
import math

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QCursor, QPixmap
from pynput.mouse import Button

from dwell_click.engine import DwellSettings


def background_screen(bounds, color="white"):
    def grab(*args):
        image = QPixmap(54, 54)
        image.fill(QColor(color))
        return image

    return SimpleNamespace(availableGeometry=lambda: bounds, geometry=lambda: bounds,
                           grabWindow=grab)


@pytest.fixture
def menu_service(qt_app, monkeypatch):
    from dwell_click import context_menu, zoom
    from dwell_click.service import DwellClickService

    clock = [0.0]
    position = [(500.0, 400.0)]
    clicks = []
    mouse = SimpleNamespace(position=(500, 400), click=lambda button, count=1: clicks.append(
        (button, mouse.position) if count == 1 else (button, mouse.position, count)
    ))

    def capture(window, x, y, width, height):
        image = QPixmap(width, height)
        image.fill(QColor("white"))
        return image

    screen = SimpleNamespace(
        availableGeometry=lambda: QRect(0, 0, 1000, 800),
        geometry=lambda: QRect(0, 0, 1000, 800),
        devicePixelRatio=lambda: 1,
        grabWindow=capture,
    )
    monkeypatch.setattr(context_menu.QGuiApplication, "screenAt", lambda point: screen)
    monkeypatch.setattr(zoom.QGuiApplication, "screenAt", lambda point: screen)

    def move(point):
        mouse.position = (point.x(), point.y())
        position[0] = mouse.position

    monkeypatch.setattr(QCursor, "setPos", move)
    monkeypatch.setattr(QCursor, "pos", lambda: QPoint(*mouse.position))
    service = DwellClickService(
        DwellSettings(context_menu_enabled=True), mouse_controller=mouse,
        position_provider=lambda: position[0], clock=lambda: clock[0],
    )
    service.start()

    def poll(time, point=None):
        clock[0] = time
        if point is not None:
            position[0] = point
            mouse.position = point
        else:
            position[0] = mouse.position
        service._poll()

    def icon(choice):
        centre = service._context_menu.buttons[choice].geometry().center()
        return centre.x(), centre.y()

    def finish():
        service._click_timer.stop()
        service._complete_zoom_click()
        service._press_timer.stop()
        service._press_after_hover()
        position[0] = mouse.position

    yield service, poll, icon, finish, clicks
    service.stop()


@pytest.mark.parametrize("choice", ["left", "right"])
def test_first_selection_clicks_and_closes_second_selection_locks_and_clicks(menu_service, choice):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    assert service._context_menu.is_visible()
    assert clicks == []
    selected = icon(choice)
    poll(1.1, selected)
    poll(2.1)
    assert service._click_timer.isActive()
    assert not service._context_menu.is_visible()
    assert service._locked_button is None
    finish()
    assert clicks == [(Button.left if choice == "left" else Button.right, (500, 400))]
    assert not service._context_menu.is_visible()
    assert service._mouse_controller.position == (500, 400)
    poll(2.15)
    assert len(clicks) == 1
    assert not service._context_menu.is_visible()
    poll(2.2, (700, 600))
    poll(3.2)
    assert service._context_menu.is_visible()
    poll(3.3, icon(choice))
    poll(4.3)
    assert service._locked_button == choice
    finish()
    assert len(clicks) == 2
    assert clicks[-1] == (clicks[0][0], (700, 600))
    assert not service._context_menu.is_visible()
    poll(4.4, (800, 650))
    poll(5.4)
    assert len(clicks) == 3
    assert service._menu_deadline == pytest.approx(8.4)
    poll(8.4)
    assert not service._context_menu.is_visible()
    assert service._locked_button == choice


@pytest.mark.parametrize("choice", ["left", "right"])
def test_remaining_at_target_after_lock_cannot_immediately_unlock(menu_service, choice):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish, choice)
    poll(5.3)
    poll(6.3)
    assert service._locked_button == choice
    assert not service._context_menu.is_visible()
    assert len(clicks) == 2


def lock(service, poll, icon, finish, choice="left"):
    poll(1)
    poll(1.1, icon(choice))
    poll(2.1)
    finish()
    poll(2.2, (700, 600))
    poll(3.2)
    poll(3.3, icon(choice))
    poll(4.3)
    finish()
    assert service._locked_button == choice


@pytest.mark.parametrize("choice", ["left", "right"])
def test_locked_icon_unlocks_without_click_and_stays_unlocked(menu_service, choice):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish, choice)
    poll(4.4, (800, 650))
    poll(5.4)
    assert len(clicks) == 3
    assert service._locked_button == choice
    poll(5.5, icon(choice))
    poll(6.5)
    assert service._locked_button is None
    assert not service._context_menu.is_visible()
    poll(8)
    assert len(clicks) == 3
    poll(8.1, (400, 300))
    poll(9.1)
    assert service._context_menu.is_visible()
    assert len(clicks) == 3


@pytest.mark.parametrize("old,new", [("left", "right"), ("right", "left")])
def test_different_locked_choice_clicks_once_closes_then_can_lock(menu_service, old, new):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish, old)
    poll(4.4, (800, 650))
    poll(5.4)
    poll(5.5, icon(new))
    poll(6.5)
    assert service._locked_button is None
    finish()
    assert len(clicks) == 4
    assert clicks[-1] == (Button.right if new == "right" else Button.left, (800, 650))
    assert not service._context_menu.is_visible()
    poll(6.6, (400, 300))
    poll(7.6)
    poll(7.7, icon(new))
    poll(8.7)
    finish()
    assert service._locked_button == new
    assert clicks[-1] == (clicks[-2][0], (400, 300))


def test_menu_click_hovers_on_target_before_pressing(menu_service):
    from dwell_click.service import HOVER_BEFORE_PRESS_MS

    service, poll, icon, _, clicks = menu_service
    poll(1)
    poll(1.1, icon("right"))
    poll(2.1)
    service._click_timer.stop()
    service._complete_zoom_click()
    assert service._mouse_controller.position == (500, 400)
    assert clicks == []
    assert service._press_timer.isActive()
    assert service._press_timer.interval() == HOVER_BEFORE_PRESS_MS >= 100
    poll(2.12)
    assert clicks == []
    service._mouse_controller.position = (503, 404)
    service._press_timer.stop()
    service._press_after_hover()
    assert clicks == [(Button.right, (500, 400))]


def test_stop_during_hover_pause_cancels_click(menu_service):
    service, poll, icon, _, clicks = menu_service
    poll(1)
    poll(1.1, icon("left"))
    poll(2.1)
    service._click_timer.stop()
    service._complete_zoom_click()
    service.stop()
    assert not service._press_timer.isActive()
    service._press_after_hover()
    assert clicks == []


def test_outside_dwell_cancels_unlocked_menu(menu_service):
    service, poll, _, _, clicks = menu_service
    poll(1)
    poll(1.1, (700, 600))
    poll(2.1)
    assert not service._context_menu.is_visible()
    assert clicks == []


@pytest.mark.parametrize("choice", ["left", "right"])
def test_zoom_without_button_waits_for_button_then_zoom_clicks(menu_service, choice):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("zoom"))
    poll(2.1)
    assert not service.settings.zoom_enabled
    assert not service._zoom_locked
    assert service._zoom_selected
    assert service._context_menu.is_visible()
    assert not service._capture_timer.isActive()
    assert not service._click_timer.isActive()
    poll(3.1)
    poll(5.0)
    assert service._context_menu.is_visible()
    assert not service._zoom_locked
    assert clicks == []
    poll(5.1, icon(choice))
    poll(6.1)
    assert service._button == choice
    assert service._pending_position == (500, 400)
    assert service._capture_timer.isActive()
    assert not service._context_menu.is_visible()
    assert service._locked_button is None
    complete_magnifier(service, poll, finish, 6.2)
    assert clicks == [(Button.left if choice == "left" else Button.right, clicks[0][1])]
    assert not service._zoom_locked
    assert not service._context_menu.is_visible()


@pytest.mark.parametrize("choice", ["left", "right"])
def test_zoom_after_button_choice_zooms_immediately_with_that_button(menu_service, choice):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon(choice))
    poll(2.1)
    finish()
    poll(2.2, (700, 600))
    poll(3.2)
    poll(3.3, icon("zoom"))
    poll(4.3)
    assert service._capture_timer.isActive()
    assert service._pending_position == (700, 600)
    assert not service._context_menu.is_visible()
    assert not service._zoom_locked
    complete_magnifier(service, poll, finish, 4.4)
    assert [click[0] for click in clicks] == [Button.left if choice == "left" else Button.right] * 2
    assert service._locked_button is None


def test_zoom_choice_works_with_gui_unchecked_and_real_capture_timer(menu_service):
    from PySide6.QtTest import QTest
    from dwell_click.tray import DwellTray

    service, poll, icon, finish, clicks = menu_service
    config = service.settings.to_config()

    def apply(values):
        config.update(values)
        service.update_settings(DwellSettings.from_config(config))
        tray.set_settings(service.settings)

    tray = DwellTray(service, service.settings, on_settings_changed=apply)
    try:
        tray.show_settings()
        assert not tray._settings_dialog._zoom_options.values()["zoom_enabled"]
        poll(1)
        poll(1.1, icon("zoom"))
        poll(2.1)
        assert not config["zoom_enabled"]
        assert not tray._settings_dialog._zoom_options.values()["zoom_enabled"]
        poll(2.15, icon("left"))
        poll(3.15)
        assert service._capture_timer.isActive()
        for _ in range(50):
            QTest.qWait(20)
            if service._zoom.isVisible():
                break
        assert service._zoom.isVisible()
        assert not service._context_menu.is_visible()
        assert clicks == []
        poll(3.2)
        poll(4.2)
        assert service._zoom.isVisible()
        assert not service._click_timer.isActive()
        poll(4.3, (service._zoom.x() + 180, service._zoom.y() + 120))
        poll(5.3)
        finish()
        assert len(clicks) == 1
        assert not service._context_menu.is_visible()
    finally:
        tray.close()


@pytest.mark.parametrize("size", [36, 54, 100])
def test_menu_size_order_and_transparent_background(qt_app, monkeypatch, size):
    from dwell_click.context_menu import DwellContextMenu
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import Qt

    monkeypatch.setattr(QGuiApplication, "screenAt",
                        lambda point: background_screen(QRect(0, 0, 1000, 800)))
    menu = DwellContextMenu()
    try:
        menu.show_at((500, 400), 60, None, False, size)
        left, zoom, right, double, pause = (
            menu.buttons[key] for key in ("left", "zoom", "right", "double", "pause")
        )
        assert left.x() < zoom.x() < right.x()
        assert double.x() == left.x() and pause.x() == right.x()
        assert double.y() > 400 and pause.y() > 400 and zoom.y() < left.y() < 400
        for button in menu.buttons.values():
            assert button.width() == button.height() == size
            assert button.iconSize().width() == round(size * 28 / 36)
            assert button.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            image = button.grab().toImage()
            assert image.pixelColor(0, 0).alpha() == 0
            assert image.pixelColor(3, size // 2).alpha() == pytest.approx(51, abs=1)
        menu.highlight("left", True)
        assert left.grab().toImage().pixelColor(0, 0).alpha() == 0
    finally:
        menu.hide()


@pytest.mark.parametrize("background,dark", [("#101010", True), ("#fafafa", False)])
def test_menu_adapts_artwork_and_twenty_percent_tint(qt_app, monkeypatch, background, dark):
    from dwell_click.context_menu import DwellContextMenu
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "screenAt", lambda point:
                        background_screen(QRect(-1000, 0, 1000, 800), background))
    menu = DwellContextMenu()
    try:
        menu.show_at((-500, 400), 60, "right", True)
        assert all(value is dark for value in menu._dark_backgrounds.values())
        button = menu.buttons["left"]
        tint = button.grab().toImage().pixelColor(3, button.height() // 2)
        assert tint.alpha() == pytest.approx(51, abs=1)
        assert tint.red() == (255 if dark else 0)
        image = button.icon().pixmap(button.iconSize()).toImage()
        colours = [image.pixelColor(x, y)
                   for y in range(image.height()) for x in range(image.width())
                   if image.pixelColor(x, y).alpha() == 255]
        assert any(colour.red() == (255 if dark else 32) for colour in colours)
        assert menu.buttons["right"].grab().toImage().pixelColor(0, 0).alpha() == 0
    finally:
        menu.hide()


def test_background_sampling_failure_is_logged(qt_app, caplog):
    from dwell_click.context_menu import DwellContextMenu

    screen = SimpleNamespace(geometry=lambda: QRect(0, 0, 1000, 800),
                             grabWindow=lambda *args: QPixmap())
    assert isinstance(DwellContextMenu._is_dark_background(screen, QRect(0, 0, 54, 54)), bool)
    assert "Cannot sample the click-menu background" in caplog.text


def test_zoom_after_automatic_click_arms_next_target_without_repeating_click(menu_service):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish)
    poll(4.4, (800, 650))
    poll(5.4)
    poll(5.5, icon("zoom"))
    poll(6.5)
    assert service._locked_button == "left"
    assert not service._zoom_locked
    assert service._zoom_once_armed
    assert not service.settings.zoom_enabled
    assert not service._capture_timer.isActive()
    assert not service._context_menu.is_visible()
    assert len(clicks) == 3
    poll(6.6, (400, 300))
    poll(7.6)
    assert service._capture_timer.isActive()
    assert service._pending_position == (400, 300)


def complete_magnifier(service, poll, finish, start):
    service._capture_timer.stop()
    service._open_zoom()
    assert service._zoom.isVisible()
    poll(start)
    poll(start + 1)
    assert not service._click_timer.isActive()
    point = (service._zoom.x() + 180, service._zoom.y() + 120)
    poll(start + 1.1, point)
    poll(start + 2.1)
    assert not service._zoom.isVisible()
    assert service._click_timer.isActive()
    finish()


def test_zoom_first_use_second_lock_third_unlock_across_menus(menu_service):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("left"))
    poll(2.1)
    finish()
    assert len(clicks) == 1
    poll(2.2, (700, 600))
    poll(3.2)
    poll(3.3, icon("zoom"))
    poll(4.3)
    assert not service._zoom_locked
    complete_magnifier(service, poll, finish, 4.4)
    assert len(clicks) == 2
    assert not service._context_menu.is_visible()
    poll(6.6, (400, 300))
    poll(7.6)
    assert service._context_menu.is_visible()
    poll(7.7, icon("zoom"))
    poll(8.7)
    assert service._zoom_locked
    complete_magnifier(service, poll, finish, 8.8)
    assert len(clicks) == 3
    assert not service._context_menu.is_visible()
    poll(11.0, (700, 600))
    poll(12.0)
    assert service._context_menu.is_visible()
    assert not service._capture_timer.isActive()
    assert "solid #0078d4" in service._context_menu.buttons["zoom"].styleSheet() or (
        "solid #66c2ff" in service._context_menu.buttons["zoom"].styleSheet()
    )
    poll(12.1, icon("left"))
    poll(13.1)
    assert service._locked_button == "left"
    assert service._capture_timer.isActive()
    complete_magnifier(service, poll, finish, 13.2)
    assert len(clicks) == 4
    assert all(click[0] == Button.left for click in clicks)
    poll(15.4, (400, 300))
    poll(16.4)
    assert service._capture_timer.isActive()
    complete_magnifier(service, poll, finish, 16.5)
    assert len(clicks) == 5
    assert service._context_menu.is_visible()
    poll(18.7, icon("zoom"))
    poll(19.7)
    assert not service._zoom_locked
    assert service._locked_button == "left"
    assert not service._context_menu.is_visible()
    assert not service.settings.zoom_enabled
    assert len(clicks) == 5
    poll(19.8, (700, 600))
    poll(20.8)
    assert len(clicks) == 6
    assert service._context_menu.is_visible()
    assert not service._capture_timer.isActive()


def test_second_zoom_selection_after_autoclick_locks_without_repeating_click(menu_service):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish)
    poll(4.4, (800, 650))
    poll(5.4)
    poll(5.5, icon("zoom"))
    poll(6.5)
    poll(6.6, (400, 300))
    poll(7.6)
    complete_magnifier(service, poll, finish, 7.7)
    assert len(clicks) == 4
    poll(9.9, icon("zoom"))
    poll(10.9)
    assert service._zoom_locked
    assert service._locked_button == "left"
    assert not service._context_menu.is_visible()
    assert not service._capture_timer.isActive()
    assert len(clicks) == 4


def test_unlocking_button_does_not_unlock_zoom(menu_service):
    service, poll, icon, finish, clicks = menu_service
    service.update_settings(replace(service.settings, zoom_enabled=True))
    service._locked_button = "right"
    poll(1)
    complete_magnifier(service, poll, finish, 1.1)
    assert service._context_menu.is_visible()
    poll(3.3, icon("right"))
    poll(4.3)
    assert service._locked_button is None
    assert service._zoom_locked
    assert not service._context_menu.is_visible()
    assert len(clicks) == 1
    poll(4.4, (700, 600))
    poll(5.4)
    assert service._context_menu.is_visible()
    assert not service._capture_timer.isActive()
    poll(5.5, icon("right"))
    poll(6.5)
    assert service._capture_timer.isActive()
    assert service._button == "right"


def test_zoom_gui_default_is_reseeded_on_start_but_not_when_menu_changes(menu_service):
    service, poll, icon, finish, clicks = menu_service
    service.update_settings(replace(service.settings, zoom_enabled=True))
    poll(1)
    assert service._context_menu.is_visible()
    assert not service._capture_timer.isActive()
    poll(1.1, icon("left"))
    poll(2.1)
    complete_magnifier(service, poll, finish, 2.2)
    poll(4.4, (700, 600))
    poll(5.4)
    poll(5.5, icon("zoom"))
    poll(6.5)
    assert not service._zoom_locked
    assert service.settings.zoom_enabled
    service.stop()
    service.start()
    assert service._zoom_locked
    assert not service._zoom_selected
    assert service._locked_button is None
    assert len(clicks) == 1


def test_timeout_is_paused_while_completing_icon_dwell(menu_service):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish)
    poll(4.4, (800, 650))
    poll(5.4)
    assert service._menu_deadline == pytest.approx(8.4)
    poll(8)
    poll(8.1, icon("left"))
    poll(8.8)
    assert service._context_menu.is_visible()
    assert service._locked_button == "left"
    poll(9.1)
    assert service._locked_button is None
    assert not service._context_menu.is_visible()
    assert len(clicks) == 3


def test_menu_opened_under_pointer_requires_leaving_icon_before_unlocking(menu_service):
    service, poll, icon, _, clicks = menu_service
    service._locked_button = "left"
    service._show_menu((500, 400), timed=True)
    point = icon("left")
    poll(0.1, point)
    service._show_menu((500, 400), timed=True)
    assert service._blocked_choice == "left"
    poll(0.2)
    poll(1.2)
    assert service._locked_button == "left"
    assert not service._overlay.isVisible()
    poll(1.3, (500, 400))
    poll(1.4, icon("left"))
    poll(2.4)
    assert service._locked_button is None
    assert clicks == []


def test_right_click_choice_is_preserved_through_independent_zoom_lock(menu_service):
    service, poll, icon, finish, clicks = menu_service
    service.update_settings(replace(service.settings, zoom_enabled=True))
    assert service._zoom_locked
    service._show_menu((500, 400))
    poll(0.1, icon("right"))
    poll(1.1)
    service._capture_timer.stop()
    service._open_zoom()
    point = (service._zoom.x() + 180, service._zoom.y() + 120)
    poll(1.2, point)
    poll(2.2)
    finish()
    assert clicks[0][0] == Button.right
    assert not service._context_menu.is_visible()
    assert service._last_button == "right"
    service._show_menu((700, 600))
    poll(2.3, icon("right"))
    poll(3.3)
    assert service._locked_button == "right"
    assert service._zoom_locked
    assert service._capture_timer.isActive()


def test_locked_zoom_click_returns_unlock_menu_for_configured_time(menu_service):
    service, poll, icon, finish, clicks = menu_service
    service.update_settings(replace(service.settings, zoom_enabled=True))
    service._locked_button = "right"
    poll(1)
    assert service._capture_timer.isActive()
    assert not service._context_menu.is_visible()
    service._capture_timer.stop()
    service._open_zoom()
    point = (service._zoom.x() + 180, service._zoom.y() + 120)
    poll(1.1, point)
    poll(2.1)
    finish()
    assert clicks[-1][0] == Button.right
    assert service._context_menu.is_visible()
    assert service._menu_deadline == pytest.approx(5.1)
    poll(2.2, icon("zoom"))
    poll(3.2)
    assert service._locked_button == "right"
    assert not service._zoom_locked
    assert service.settings.zoom_enabled
    assert not service._context_menu.is_visible()
    assert len(clicks) == 1


def test_changing_unlocked_button_restarts_selection_count(menu_service):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("left"))
    poll(2.1)
    finish()
    poll(2.2, (700, 600))
    poll(3.2)
    poll(3.3, icon("right"))
    poll(4.3)
    finish()
    assert service._locked_button is None
    assert service._last_button == "right"
    assert len(clicks) == 2


def test_no_dwell_progress_carried_between_different_icons(menu_service):
    service, poll, icon, _, clicks = menu_service
    service.update_settings(replace(service.settings, movement_tolerance_px=100))
    service._show_menu((500, 400))
    poll(0.1, icon("left"))
    poll(0.9)
    poll(1, icon("right"))
    poll(1.5)
    assert not service._click_timer.isActive()
    assert clicks == []
    poll(2)
    assert service._click_timer.isActive()


@pytest.mark.parametrize("action", ["stop", "settings"])
def test_stop_and_settings_changes_clear_menu_and_lock(menu_service, action):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish)
    if action == "stop":
        service.stop()
    else:
        service.update_settings(DwellSettings())
    assert not service._context_menu.is_visible()
    assert service._locked_button is None
    assert service._menu_deadline is None
    assert len(clicks) == 2


@pytest.mark.parametrize("point", [(500, 400), (0, 0), (999, 799)])
def test_icon_layout_angles_distance_and_screen_edges(qt_app, monkeypatch, point):
    from dwell_click.context_menu import DwellContextMenu
    from PySide6.QtGui import QGuiApplication

    bounds = QRect(0, 0, 1000, 800)
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda point: background_screen(bounds))
    menu = DwellContextMenu()
    try:
        menu.show_at(point, 60, None, False)
        from dwell_click.context_menu import MENU_ANGLES

        assert list(menu.buttons) == ["left", "zoom", "right", "double", "pause"]
        rects = [button.geometry() for button in menu.buttons.values()]
        assert all(bounds.contains(rect) for rect in rects)
        for index, rect in enumerate(rects):
            assert not any(rect.intersects(other) for other in rects[index + 1:])
        if point == (500, 400):
            for rect, angle in zip(rects, (MENU_ANGLES[key] for key in menu.buttons)):
                assert rect.width() == rect.height() == 54
                assert rect.x() + 27 == round(500 + 77 * math.cos(math.radians(angle)))
                assert rect.y() + 27 == round(400 - 77 * math.sin(math.radians(angle)))
        for choice, button in menu.buttons.items():
            centre = button.geometry().center()
            assert menu.choice_at((centre.x(), centre.y())) == choice
            assert not button.icon().isNull()
    finally:
        menu.hide()


@pytest.mark.parametrize("config", [
    {"context_menu_distance_px": 47},
    {"context_menu_distance_px": 60.5},
    {"context_menu_distance_px": float("nan")},
    {"context_menu_lock_time_s": 0},
    {"context_menu_lock_time_s": float("inf")},
    {"context_menu_lock_time_s": float("nan")},
    {"context_menu_size_px": 0},
    {"context_menu_size_px": 201},
    {"context_menu_size_px": 54.5},
])
def test_invalid_menu_settings_rejected(config):
    with pytest.raises(ValueError):
        DwellSettings(**config)


def test_large_distance_is_reduced_to_fit_display(qt_app, monkeypatch):
    from dwell_click.context_menu import DwellContextMenu
    from PySide6.QtGui import QGuiApplication

    bounds = QRect(-200, 0, 200, 150)
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda point: background_screen(bounds))
    menu = DwellContextMenu()
    try:
        menu.show_at((-200, 0), 1000, None, False)
        rects = [button.geometry() for button in menu.buttons.values()]
        assert all(bounds.contains(rect) for rect in rects)
        for index, rect in enumerate(rects):
            assert not any(rect.intersects(other) for other in rects[index + 1:])
    finally:
        menu.hide()


@pytest.mark.parametrize("action", ["stop", "settings"])
def test_pending_menu_click_is_cancelled_by_stop_or_settings(menu_service, action):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("right"))
    poll(2.1)
    assert service._click_timer.isActive()
    if action == "stop":
        service.stop()
    else:
        service.update_settings(DwellSettings())
    finish()
    assert clicks == []
    assert not service._context_menu.is_visible()


@pytest.mark.parametrize("ui", ["dialog", "panel"])
def test_menu_settings_available_in_both_guis(qt_app, ui):
    from dwell_click.settings_dialog import DwellSettingsDialog
    from gui.control_panel import ControlPanel

    settings = DwellSettings(context_menu_enabled=True, context_menu_distance_px=90,
                             context_menu_lock_time_s=5)
    widget = (DwellSettingsDialog(settings) if ui == "dialog"
              else ControlPanel(dwell_config=settings.to_config()))
    seen = []
    signal = widget.settings_applied if ui == "dialog" else widget.dwell_settings_changed
    signal.connect(seen.append)
    try:
        assert widget._context_options.values()["context_menu_distance_px"] == 90
        widget._context_options._distance.setValue(120)
        widget._context_options._size.setValue(72)
        widget._context_options._lock_time.setValue(7)
        widget._context_options._enabled.setChecked(False)
        if ui == "dialog":
            widget._apply()
        assert seen[-1]["context_menu_distance_px"] == 120
        assert seen[-1]["context_menu_size_px"] == 72
        assert seen[-1]["context_menu_lock_time_s"] == 7
        assert seen[-1]["context_menu_enabled"] is False
        assert DwellSettings.from_config(settings.to_config()) == settings
    finally:
        widget.close()


def test_menu_settings_and_initial_zoom_lock_persist_through_host(qt_app, tmp_path):
    import copy
    import json
    from main import App, load_config

    path = tmp_path / "config.json"
    config = copy.deepcopy(load_config())
    mouse = SimpleNamespace(position=(0, 0), click=lambda button: None)
    app = App(config, auto_start_listeners=False, mouse_controller=mouse, config_path=path)
    try:
        options = app.window.controls._context_options
        options._enabled.setChecked(True)
        options._distance.setValue(100)
        options._size.setValue(72)
        options._lock_time.setValue(6)
        app._dwell_tray.show_settings()
        dialog = app._dwell_tray._settings_dialog
        dialog._zoom_options._enabled.setChecked(True)
        dialog._apply()
        assert app._dwell_click._zoom_locked
        assert app.window.controls.dwell_settings["zoom_enabled"]
        saved = json.loads(path.read_text())["ui"]["dwell_click"]
        assert saved["context_menu_enabled"]
        assert saved["context_menu_distance_px"] == 100
        assert saved["context_menu_size_px"] == 72
        assert saved["context_menu_lock_time_s"] == 6
        assert saved["zoom_enabled"]
    finally:
        app.shutdown()
    reloaded = App(load_config(path), auto_start_listeners=False,
                   mouse_controller=mouse, config_path=path)
    try:
        assert reloaded._dwell_click.settings.context_menu_enabled
        assert reloaded.window.controls._context_options.values()["context_menu_distance_px"] == 100
        assert reloaded._dwell_click.settings.context_menu_size_px == 72
        assert reloaded._dwell_click._locked_button is None
        assert reloaded._dwell_click._zoom_locked
    finally:
        reloaded.shutdown()


def test_double_click_selects_once_then_locks_and_unlocks(menu_service):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("double"))
    poll(2.1)
    finish()
    assert clicks == [(Button.left, (500, 400), 2)]
    assert service._locked_button is None
    assert not service._context_menu.is_visible()
    poll(2.2, (700, 600))
    poll(3.2)
    poll(3.3, icon("double"))
    poll(4.3)
    finish()
    assert service._locked_button == "double"
    assert clicks[-1] == (Button.left, (700, 600), 2)
    poll(4.4, (800, 650))
    poll(5.4)
    assert clicks[-1] == (Button.left, (800, 650), 2)
    assert service._context_menu.is_visible()
    poll(5.5, icon("double"))
    poll(6.5)
    assert service._locked_button is None
    assert len(clicks) == 3


def test_double_click_works_with_zoom(menu_service):
    service, poll, icon, finish, clicks = menu_service
    poll(1)
    poll(1.1, icon("zoom"))
    poll(2.1)
    assert service._context_menu.is_visible()
    poll(2.2, icon("double"))
    poll(3.2)
    assert service._capture_timer.isActive()
    complete_magnifier(service, poll, finish, 3.3)
    assert len(clicks) == 1 and clicks[0][0] == Button.left and clicks[0][2] == 2


def pause_from_menu(service, poll, icon):
    poll(1)
    poll(1.1, icon("pause"))
    poll(2.1)
    assert service.is_paused
    assert service.is_running and not service.is_active
    assert not service._context_menu.is_visible()
    assert service._resume_button.is_visible()


def test_pause_stops_clicking_until_resume_button_dwell(menu_service):
    service, poll, icon, finish, clicks = menu_service
    lock(service, poll, icon, finish, "right")
    poll(4.4, (800, 650))
    poll(5.4)
    assert len(clicks) == 3 and service._context_menu.is_visible()
    poll(5.5, icon("pause"))
    poll(6.5)
    assert service.is_paused and len(clicks) == 3
    resume = service._resume_button.button.geometry()
    assert resume.right() == 1000 - 1 - 8 and resume.bottom() == 800 - 1 - 8
    poll(6.6, (300, 300))
    poll(9)
    poll(9.1, (600, 200))
    poll(12)
    assert len(clicks) == 3 and not service._context_menu.is_visible()
    assert not service._overlay.isVisible()
    centre = resume.center()
    poll(12.1, (centre.x(), centre.y()))
    poll(12.6)
    assert service.is_paused and service._overlay.isVisible()
    poll(13.1)
    assert not service.is_paused and service.is_active
    assert not service._resume_button.is_visible()
    assert len(clicks) == 3
    assert service._locked_button == "right"
    poll(13.5)
    assert len(clicks) == 3
    poll(13.6, (400, 300))
    poll(14.6)
    assert clicks[-1] == (Button.right, (400, 300))


def test_pause_cancels_pending_target_without_clicking(menu_service):
    service, poll, icon, finish, clicks = menu_service
    pause_from_menu(service, poll, icon)
    finish()
    assert clicks == []


def test_resume_button_under_pointer_requires_leaving_first(menu_service):
    service, poll, icon, _, clicks = menu_service
    pause_from_menu(service, poll, icon)
    centre = service._resume_button.button.geometry().center()
    service.resume()
    poll(2.2, (centre.x(), centre.y()))
    service.pause()
    assert service._resume_blocked
    poll(3.2)
    poll(5)
    assert service.is_paused
    poll(5.1, (300, 300))
    poll(5.2, (centre.x(), centre.y()))
    poll(6.2)
    assert not service.is_paused
    assert clicks == []


def test_tray_start_resumes_and_host_sync_keeps_pause(menu_service):
    from dwell_click.tray import DwellTray

    service, poll, icon, _, _ = menu_service
    toggled = []
    tray = DwellTray(service, service.settings, on_toggled=toggled.append)
    try:
        tray.show_settings()
        pause_from_menu(service, poll, icon)
        assert tray.toggle_action.text() == "Start Dwell Clicking"
        assert tray._settings_dialog._toggle_button.text() == "Start Dwell Clicking"
        service._set_running(True)
        assert service.is_paused
        tray._toggle()
        assert service.is_active and not service._resume_button.is_visible()
        assert toggled == [True]
        assert tray.toggle_action.text() == "Stop Dwell Clicking"
        assert tray._settings_dialog._toggle_button.text() == "Stop Dwell Clicking"
        service.pause()
        service._set_running(False)
        assert not service.is_running and not service.is_paused
        assert not service._resume_button.is_visible()
        service.start()
        assert service.is_active
    finally:
        tray.close()



@pytest.mark.parametrize("available,target,clear", [
    (QRect(0, 0, 1000, 752), (500, 780), lambda rect: rect.bottom() < 752 - 260),
    (QRect(0, 48, 1000, 752), (500, 20), lambda rect: rect.top() >= 48 + 260),
    (QRect(60, 0, 940, 800), (30, 400), lambda rect: rect.left() >= 60 + 260),
    (QRect(0, 0, 1000, 752), (500, 600), None),
])
def test_menu_for_taskbar_target_avoids_preview_area(qt_app, monkeypatch, available, target, clear):
    from dwell_click.context_menu import DwellContextMenu
    from PySide6.QtGui import QGuiApplication

    screen = background_screen(QRect(0, 0, 1000, 800))
    screen.availableGeometry = lambda: QRect(available)
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda point: screen)
    menu = DwellContextMenu()
    try:
        menu.show_at(target, 60, None, False)
        rects = [button.geometry() for button in menu.buttons.values()]
        assert all(available.contains(rect) for rect in rects)
        for index, rect in enumerate(rects):
            assert not any(rect.intersects(other) for other in rects[index + 1:])
        if clear is None:
            zoom = menu.buttons["zoom"].geometry()
            assert (zoom.x() + 27, zoom.y() + 27) == (500, 600 - 77)
        else:
            assert all(clear(rect) for rect in rects)
            assert all(rect.width() == 54 for rect in rects)
    finally:
        menu.hide()
