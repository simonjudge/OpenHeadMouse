"""Per-provider mouse movement profile migration and switching."""

import copy


class FakeMouse:
    position = (400.0, 400.0)

    def click(self, button):
        pass


def test_legacy_dsp_config_migrates_to_independent_provider_profiles():
    from main import ensure_mouse_profiles

    legacy = {
        "mode": "absolute",
        "absolute": {"kx": 12.0, "ky": 13.0},
        "velocity": {"kx": 2.0, "ky": 3.0, "gamma": 1.0, "deadband_deg_s": 0.0},
        "one_euro_filter": {
            "enabled": False,
            "min_cutoff": 1.0,
            "beta": 0.3,
            "d_cutoff": 1.0,
        },
        "auto_zero": {"enabled": False},
        "yaw_drift_correction": {"enabled": False},
    }
    config = {
        "active_provider": "uniface",
        "providers": {"uniface": {}, "ximu3": {}, "airpods": {}},
        "dsp": copy.deepcopy(legacy),
    }

    profiles = ensure_mouse_profiles(config)

    assert "dsp" not in config
    assert set(profiles) == {"uniface", "ximu3", "airpods"}
    assert all(profile == legacy for profile in profiles.values())
    profiles["uniface"]["absolute"]["kx"] = 99.0
    assert profiles["ximu3"]["absolute"]["kx"] == 12.0


def test_switching_provider_replaces_live_engine_and_gui_profile(qt_app, tmp_path):
    from main import App, load_config

    config = copy.deepcopy(load_config())
    config["active_provider"] = "uniface"
    config["mouse_profiles"]["uniface"]["mode"] = "absolute"
    config["mouse_profiles"]["uniface"]["absolute"] = {"kx": 11.0, "ky": 12.0}
    config["mouse_profiles"]["ximu3"]["mode"] = "velocity"
    config["mouse_profiles"]["ximu3"]["absolute"] = {"kx": 31.0, "ky": 32.0}
    config["mouse_profiles"]["ximu3"]["velocity"].update(
        {"kx": 41.0, "ky": 42.0, "gamma": 1.7, "deadband_deg_s": 4.0}
    )
    config_path = tmp_path / "config.json"
    app = App(
        config,
        auto_start_listeners=False,
        mouse_controller=FakeMouse(),
        config_path=config_path,
    )
    try:
        assert app.dsp.mode == "absolute"
        assert app.window._kx_spin.value() == 11

        combo = app.window.setup_panel._provider_combo
        combo.setCurrentIndex(combo.findData("ximu3"))
        assert app.dsp.mode == "velocity"
        assert app.dsp.kx == 31.0
        assert app.dsp.velocity_kx == 41.0
        assert app.window._mode_combo.currentData() == "velocity"
        assert app.window._kx_spin.value() == 31
        assert app.window._velocity_kx_spin.value() == 41
        assert app.window._gamma_spin.value() == 1.7

        app.window._kx_spin.setValue(55)
        combo.setCurrentIndex(combo.findData("uniface"))
        assert app.dsp.mode == "absolute"
        assert app.dsp.kx == 11.0
        assert app.window._kx_spin.value() == 11
        assert config["mouse_profiles"]["ximu3"]["absolute"]["kx"] == 55.0
        assert config["mouse_profiles"]["uniface"]["absolute"]["kx"] == 11.0
    finally:
        app.shutdown()


def test_app_persists_legacy_profile_migration(qt_app, tmp_path):
    import json

    from main import App, load_config

    config = copy.deepcopy(load_config())
    shared_profile = config.pop("mouse_profiles")["uniface"]
    config["dsp"] = shared_profile
    config_path = tmp_path / "legacy.json"
    config_path.write_text(json.dumps(config))

    app = App(
        config,
        auto_start_listeners=False,
        mouse_controller=FakeMouse(),
        config_path=config_path,
    )
    try:
        migrated = json.loads(config_path.read_text())
        assert "dsp" not in migrated
        assert set(migrated["mouse_profiles"]) == {"uniface", "ximu3", "airpods"}
    finally:
        app.shutdown()


def test_headmouse_core_remains_provider_and_config_independent():
    from core.headmouse import HeadMouse, ScreenBounds

    controller = FakeMouse()
    headmouse = HeadMouse(
        screen_bounds=ScreenBounds(width=800, height=600),
        mouse_controller=controller,
    )
    headmouse.set_tracking_state(True)
    headmouse.process_target(123.0, 234.0, timestamp=0.0)
    assert controller.position == (123.0, 234.0)
