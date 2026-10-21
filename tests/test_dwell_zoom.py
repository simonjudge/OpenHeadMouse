from types import SimpleNamespace
import math

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QPixmap

from dwell_click.engine import DwellSettings


def test_zoom_settings_defaults_and_round_trip():
    defaults = DwellSettings.from_config({})
    assert not defaults.zoom_enabled
    assert (defaults.zoom_width_px, defaults.zoom_height_px, defaults.zoom_scale) == (300, 200, 4)
    settings = DwellSettings(
        zoom_enabled=True, zoom_width_px=400, zoom_height_px=250, zoom_scale=5.5
    )
    assert DwellSettings.from_config(settings.to_config()) == settings


@pytest.mark.parametrize("ui", ["panel", "dialog"])
def test_zoom_options_load_edit_and_emit(qt_app, ui):
    from dwell_click.settings_dialog import DwellSettingsDialog
    from gui.control_panel import ControlPanel

    settings = DwellSettings(zoom_enabled=True, zoom_width_px=400, zoom_height_px=250, zoom_scale=5)
    seen = []
    if ui == "panel":
        widget = ControlPanel(dwell_config=settings.to_config())
        widget.dwell_settings_changed.connect(seen.append)
    else:
        widget = DwellSettingsDialog(settings)
        widget.settings_applied.connect(seen.append)
    try:
        options = widget._zoom_options
        assert options.values() == {key: value for key, value in settings.to_config().items()
                                    if key.startswith("zoom_")}
        options._width.setValue(320)
        options._height.setValue(240)
        options._scale.setValue(3)
        options._enabled.setChecked(False)
        if ui == "dialog":
            widget._apply()
        assert seen[-1]["zoom_width_px"] == 320
        assert seen[-1]["zoom_height_px"] == 240
        assert seen[-1]["zoom_scale"] == 3
        assert seen[-1]["zoom_enabled"] is False
        if ui == "panel":
            count = len(seen)
            widget.set_dwell_settings(settings.to_config())
            assert len(seen) == count
            assert widget.dwell_settings == settings.to_config()
    finally:
        widget.close()


def fake_screen(monkeypatch, bounds, dpr=1, available=None):
    from dwell_click import zoom

    captures = []

    def grab(window, x, y, width, height):
        captures.append((x, y, width, height))
        image = QPixmap(round(width * dpr), round(height * dpr))
        image.setDevicePixelRatio(dpr)
        image.fill(QColor("#1256ab"))
        return image

    screen = SimpleNamespace(geometry=lambda: bounds, grabWindow=grab,
                             availableGeometry=lambda: QRect(available or bounds),
                             devicePixelRatio=lambda: dpr)
    monkeypatch.setattr(zoom.QGuiApplication, "screenAt", lambda point: screen)
    return captures


@pytest.mark.parametrize("dpr", [1, 1.25, 2])
@pytest.mark.parametrize("point", [(-700, 400), (-1000, 0), (-1, 799)])
def test_zoom_dimensions_scaling_and_screen_edges(qt_app, monkeypatch, point, dpr):
    from dwell_click.zoom import DwellZoom

    bounds = QRect(-1000, 0, 1000, 800)
    captures = fake_screen(monkeypatch, bounds, dpr)
    zoom = DwellZoom()
    try:
        zoom.open_at(point, DwellSettings())
        assert zoom.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert (zoom.width(), zoom.height()) == (300, 200)
        assert bounds.contains(zoom.geometry())
        x, y, width, height = captures[-1]
        assert (width, height) == (75, 50)
        assert 0 <= x <= 925 and 0 <= y <= 750
        assert zoom.target_at((zoom.x(), zoom.y())) == (x - 1000, y)
        assert zoom.target_at((zoom.x() + 7, zoom.y() + 11)) == pytest.approx(
            (x - 1000 + math.floor(7 * dpr / 4) / dpr, y + math.floor(11 * dpr / 4) / dpr)
        )
        assert zoom.target_at((zoom.x() + 299, zoom.y() + 199)) == pytest.approx(
            (x - 1000 + math.floor(299 * dpr / 4) / dpr, y + math.floor(199 * dpr / 4) / dpr)
        )
        assert zoom.target_at((zoom.x() - 1, zoom.y())) is None
        assert zoom.target_at((zoom.x() + 300, zoom.y())) is None
        assert zoom.target_at((zoom.x(), zoom.y() + 200)) is None
        assert zoom.grab().toImage().pixelColor(100, 100) == QColor("#1256ab")
    finally:
        zoom.close()


@pytest.mark.parametrize("available,target,clear", [
    (QRect(0, 0, 1000, 752), (500, 780), lambda rect: rect.bottom() < 752 - 260),
    (QRect(60, 0, 940, 800), (30, 400), lambda rect: rect.left() >= 60 + 260),
])
def test_zoom_over_taskbar_opens_clear_of_previews(qt_app, monkeypatch, available, target, clear):
    from dwell_click.zoom import DwellZoom

    captures = fake_screen(monkeypatch, QRect(0, 0, 1000, 800), available=available)
    zoom = DwellZoom()
    try:
        zoom.open_at(target, DwellSettings())
        assert clear(zoom.geometry())
        x, y, width, height = captures[-1]
        source = QRect(x, y, width, height)
        assert source.contains(QPoint(*target))
        mapped = zoom.target_at((zoom.x() + 150, zoom.y() + 100))
        assert source.contains(QPoint(round(mapped[0]), round(mapped[1])))
    finally:
        zoom.close()


def test_zoom_away_from_taskbar_is_centred_on_target(qt_app, monkeypatch):
    from dwell_click.zoom import DwellZoom

    fake_screen(monkeypatch, QRect(0, 0, 1000, 800), available=QRect(0, 0, 1000, 752))
    zoom = DwellZoom()
    try:
        zoom.open_at((500, 400), DwellSettings())
        assert (zoom.x(), zoom.y()) == (350, 300)
    finally:
        zoom.close()


def test_zoom_fits_small_screen_and_non_integer_scale(qt_app, monkeypatch):
    from dwell_click.zoom import DwellZoom

    captures = fake_screen(monkeypatch, QRect(0, 0, 100, 80))
    zoom = DwellZoom()
    try:
        zoom.open_at((50, 40), DwellSettings(zoom_scale=2.5))
        assert (zoom.width(), zoom.height()) == (100, 80)
        assert captures[-1][2:] == (40, 32)
        x, y, _, _ = captures[-1]
        assert zoom.target_at((7, 9)) == (x + 2, y + 3)
    finally:
        zoom.close()


@pytest.fixture
def zoom_service(qt_app, monkeypatch):
    from dwell_click.service import DwellClickService

    now = [0.0]
    position = [(500.0, 400.0)]
    clicks = []
    moves = []
    mouse = SimpleNamespace(click=lambda button: clicks.append(button), position=(0, 0))
    service = DwellClickService(
        DwellSettings(zoom_enabled=True), mouse_controller=mouse,
        position_provider=lambda: position[0], clock=lambda: now[0],
    )
    fake_screen(monkeypatch, QRect(0, 0, 1000, 800))
    monkeypatch.setattr(QCursor, "setPos", lambda point: moves.append(point))
    monkeypatch.setattr(QCursor, "pos", lambda: moves[-1] if moves else QPoint(500, 400))
    service.start()

    def poll(time, point=None):
        now[0] = time
        if point is not None:
            position[0] = point
        service._poll()

    def open_zoom():
        poll(1)
        assert service._capture_timer.isActive()
        assert clicks == []
        service._capture_timer.stop()
        service._open_zoom()
        assert service._zoom.isVisible()

    yield service, poll, open_zoom, clicks, moves
    service.stop()
    service._zoom.close()
    service._overlay.close()


def test_two_stage_click_maps_to_original_pixel_and_only_clicks_once(zoom_service):
    service, poll, open_zoom, clicks, moves = zoom_service
    open_zoom()
    poll(3)
    assert clicks == [] and not service._overlay.isVisible()
    point = (service._zoom.x() + 180, service._zoom.y() + 120)
    expected = service._zoom.target_at(point)
    poll(3.1, point)
    poll(4.1)
    assert not service._zoom.isVisible()
    assert clicks == [] and service._click_timer.isActive()
    service._click_timer.stop()
    poll(4.2, expected)
    service._complete_zoom_click()
    assert clicks == [] and service._press_timer.isActive()
    service._press_timer.stop()
    service._press_after_hover()
    assert len(clicks) == 1
    assert moves == [service._zoom.source_origin]
    poll(5.1, expected)
    poll(6.1)
    assert len(clicks) == 1
    assert not service._capture_timer.isActive()


def test_dwelling_outside_cancels_without_click_or_pointer_warp(zoom_service):
    service, poll, open_zoom, clicks, moves = zoom_service
    open_zoom()
    poll(1.1, (service._zoom.x() - 20, 400))
    assert service._zoom.isVisible()
    poll(2.1)
    assert not service._zoom.isVisible()
    assert not service._click_timer.isActive()
    poll(4)
    assert clicks == [] and moves == []
    assert not service._capture_timer.isActive()


@pytest.mark.parametrize("phase", ["capture", "zoom", "click"])
@pytest.mark.parametrize("action", ["stop", "settings"])
def test_stop_or_settings_change_cancels_all_zoom_stages(zoom_service, phase, action):
    service, poll, open_zoom, clicks, moves = zoom_service
    if phase == "capture":
        poll(1)
    else:
        open_zoom()
        if phase == "click":
            poll(1.1, (service._zoom.x() + 180, service._zoom.y() + 120))
            poll(2.1)
            assert service._click_timer.isActive()
    if action == "stop":
        service.stop()
    else:
        service.update_settings(DwellSettings())
    assert not service._zoom.isVisible()
    assert not service._capture_timer.isActive()
    assert not service._click_timer.isActive()
    service._open_zoom()
    service._complete_zoom_click()
    service._press_after_hover()
    assert clicks == [] and moves == []


def test_capture_failure_reports_error_without_clicking(zoom_service, monkeypatch, caplog):
    from dwell_click import zoom

    service, poll, _, clicks, moves = zoom_service
    monkeypatch.setattr(zoom.QGuiApplication, "screenAt", lambda point: SimpleNamespace(
        geometry=lambda: QRect(0, 0, 1000, 800), grabWindow=lambda *args: QPixmap()
    ))
    errors = []
    service.error_occurred.connect(errors.append)
    poll(1)
    service._capture_timer.stop()
    service._open_zoom()
    assert errors and "Cannot capture" in errors[0]
    assert "Cannot capture" in caplog.text
    assert not service._zoom.isVisible()
    poll(3)
    assert clicks == [] and moves == []


def test_high_dpi_click_refines_cursor_to_native_pixel(zoom_service, monkeypatch):
    from dwell_click import service as service_module

    service, _, _, clicks, moves = zoom_service
    monkeypatch.setattr(service_module.sys, "platform", "win32")
    monkeypatch.setattr(service_module.QGuiApplication, "screenAt",
                        lambda point: SimpleNamespace(devicePixelRatio=lambda: 2))
    service._mouse_controller.position = (1000, 800)
    service._zoom._source = QRect(500, 400, 75, 50)
    service._pending_position = (500.5, 400.5)
    service._complete_zoom_click()
    service._press_after_hover()
    assert moves == [QPoint(500, 400)]
    assert service._mouse_controller.position == (1001, 801)
    assert len(clicks) == 1


def test_pointer_permission_failure_cancels_instead_of_clicking_wrong_point(
    zoom_service, monkeypatch, caplog
):
    service, _, open_zoom, clicks, _ = zoom_service
    open_zoom()
    errors = []
    service.error_occurred.connect(errors.append)
    service._pending_position = service._zoom.target_at(
        (service._zoom.x() + 180, service._zoom.y() + 120)
    )
    service._zoom.hide()
    monkeypatch.setattr(QCursor, "pos", lambda: QPoint(0, 0))
    service._complete_zoom_click()
    assert clicks == []
    assert "pointer-control permissions" in errors[-1]
    assert "pointer-control permissions" in caplog.text


def test_zoom_settings_persist_and_sync_between_main_window_and_tray(qt_app, tmp_path):
    import copy
    import json
    from main import App, load_config

    config = copy.deepcopy(load_config())
    path = tmp_path / "config.json"
    mouse = SimpleNamespace(position=(0, 0), click=lambda button: None)
    app = App(config, auto_start_listeners=False, mouse_controller=mouse, config_path=path)
    expected = DwellSettings(
        zoom_enabled=True, zoom_width_px=450, zoom_height_px=280, zoom_scale=5.5
    )
    try:
        app._dwell_tray.show_settings()
        dialog = app._dwell_tray._settings_dialog
        dialog._zoom_options.set_values(expected.to_config())
        dialog._apply()
        assert app._dwell_click.settings.zoom_enabled
        assert app.window.controls.dwell_settings["zoom_width_px"] == 450
        app.window.controls._zoom_options._height.setValue(290)
        assert app._dwell_tray._settings.zoom_height_px == 290
        saved = json.loads(path.read_text())["ui"]["dwell_click"]
        assert saved["zoom_enabled"] is True
        assert saved["zoom_width_px"] == 450
        assert saved["zoom_height_px"] == 290
        assert saved["zoom_scale"] == 5.5
    finally:
        app.shutdown()
    reloaded = App(
        load_config(path), auto_start_listeners=False, mouse_controller=mouse, config_path=path
    )
    try:
        assert reloaded._dwell_click.settings.zoom_width_px == 450
        assert reloaded.window.controls.dwell_settings["zoom_height_px"] == 290
        assert reloaded._dwell_tray._settings.zoom_scale == 5.5
    finally:
        reloaded.shutdown()
