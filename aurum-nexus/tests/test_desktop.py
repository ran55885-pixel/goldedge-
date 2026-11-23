"""Desktop helpers that can be verified off-Windows (the window/tray themselves need a Windows desktop)."""
from aurum_nexus.desktop import app


def test_assets_exist():
    assert app.asset("icon.png").exists() and app.asset("icon.ico").exists()


def test_app_window_command_shape(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n if n == "chromium" else None)
    monkeypatch.setattr(app.os.path, "exists", lambda p: p == "/usr/bin/chromium")
    cmd = app.app_window_command("http://127.0.0.1:8787/x")
    assert cmd[0] == "/usr/bin/chromium" and cmd[1] == "--app=http://127.0.0.1:8787/x"


def test_autostart_noop_off_windows():
    if app.os.name != "nt":
        assert app.autostart_enabled() is False
        app.set_autostart(True)   # must not raise


def test_cli_passthrough():
    assert app.cli_passthrough(["download-history", "--source", "mt5"])
    assert app.cli_passthrough(["backtest"])
    assert not app.cli_passthrough([]) and not app.cli_passthrough(["--simulate"])
