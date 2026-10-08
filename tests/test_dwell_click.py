import pytest

from dwell_click.engine import DwellEngine, DwellSettings


def test_defaults_match_user_facing_defaults():
    settings = DwellSettings()
    assert settings.dwell_time_s == 1.0
    assert settings.movement_tolerance_px == 10.0
    assert settings.resume_time_s == 0.5
    assert settings.ring_color == "#ff0000"


def test_clicks_after_pointer_dwells_within_tolerance():
    engine = DwellEngine(DwellSettings(dwell_time_s=1.0, movement_tolerance_px=10.0))

    assert engine.update((100, 100), 0.0).progress == 0.0
    halfway = engine.update((106, 106), 0.5)
    assert halfway.progress == pytest.approx(0.5)
    completed = engine.update((103, 99), 1.0)
    assert completed.click is True
    assert completed.progress == 1.0


def test_moving_outside_tolerance_starts_a_new_dwell_area():
    engine = DwellEngine(DwellSettings(dwell_time_s=1.0, movement_tolerance_px=10.0))

    engine.update((0, 0), 0.0)
    engine.update((0, 0), 0.4)
    moved = engine.update((11, 0), 0.5)
    assert moved.progress == 0.0
    assert engine.update((11, 0), 1.0).progress == pytest.approx(0.5)


def test_returning_during_resume_window_continues_previous_progress():
    engine = DwellEngine(
        DwellSettings(dwell_time_s=1.0, movement_tolerance_px=10.0, resume_time_s=0.5)
    )

    engine.update((0, 0), 0.0)
    engine.update((0, 0), 0.4)
    engine.update((20, 0), 0.5)
    resumed = engine.update((2, 0), 0.8)
    assert resumed.progress == pytest.approx(0.4)
    assert engine.update((2, 0), 1.4).click is True


def test_returning_after_resume_window_does_not_restore_progress():
    engine = DwellEngine(
        DwellSettings(dwell_time_s=1.0, movement_tolerance_px=10.0, resume_time_s=0.2)
    )

    engine.update((0, 0), 0.0)
    engine.update((0, 0), 0.4)
    engine.update((20, 0), 0.5)
    returned = engine.update((0, 0), 0.8)
    assert returned.progress == 0.0


def test_stationary_pointer_only_clicks_once_until_it_moves_away():
    engine = DwellEngine(DwellSettings(dwell_time_s=1.0, movement_tolerance_px=10.0))

    engine.update((0, 0), 0.0)
    assert engine.update((0, 0), 1.0).click is True
    assert engine.update((0, 0), 2.0).click is False
    assert engine.update((20, 0), 2.1).visible is True
    assert engine.update((20, 0), 3.1).click is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dwell_time_s": 0},
        {"movement_tolerance_px": -1},
        {"resume_time_s": -1},
    ],
)
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        DwellSettings(**kwargs)


def test_qt_service_clicks_injected_mouse_controller(qt_app):
    from dwell_click.service import DwellClickService

    class Mouse:
        def __init__(self):
            self.clicks = []

        def click(self, button):
            self.clicks.append(button)

    now = [0.0]
    mouse = Mouse()
    service = DwellClickService(
        DwellSettings(dwell_time_s=1.0),
        mouse_controller=mouse,
        position_provider=lambda: (100.0, 100.0),
        clock=lambda: now[0],
    )
    try:
        service.start()
        now[0] = 1.0
        service._poll()
        assert len(mouse.clicks) == 1
        now[0] = 2.0
        service._poll()
        assert len(mouse.clicks) == 1
    finally:
        service.stop()
