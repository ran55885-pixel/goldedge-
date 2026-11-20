"""Aurum Nexus desktop app (Windows): "Aurum Nexus.exe".

Launching starts the backend (hub + engine + MT5 workers) and opens the dashboard in its OWN window
(pywebview; fallback: Edge/Chrome --app window; last resort: default browser). A system-tray icon offers:
Open Dashboard, Phone/LAN QR, Pause All, Resume All, Kill Switch, Settings folder, Autostart, Quit.
Live trading still requires the typed confirmation: the exe asks in a console prompt when live is configured.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from aurum_nexus import APP_NAME
from aurum_nexus.config import PROJECT_ROOT, load_config

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    for cand in (base / "assets" / name, PROJECT_ROOT / "assets" / name):
        if cand.exists():
            return cand
    return PROJECT_ROOT / "assets" / name


def app_window_command(url: str) -> list[str] | None:
    """Edge/Chrome in --app mode (a chrome-less window) when pywebview is unavailable."""
    cands = [shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium")]
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
    for root in pf:
        if root:
            cands += [os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if c and os.path.exists(c):
            return [c, f"--app={url}", "--window-size=1440,900"]
    return None


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg  # type: ignore
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if os.name != "nt":
        return
    import winreg  # type: ignore
    exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" -m aurum_nexus.desktop.app'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{exe}"' if getattr(sys, "frozen", False) else exe)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_qr(hub) -> None:
    from aurum_nexus.hub.server import qr_png
    p = hub.cfg.data_dir / "lan_qr.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(qr_png(hub.url_lan))
    open_folder(p)


def tray(hub, on_open, on_quit):  # pragma: no cover - needs a desktop session
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None
    eng = hub.engine

    def item(text, fn, **kw):
        return pystray.MenuItem(text, lambda icon, it: fn(), **kw)

    menu = pystray.Menu(
        item("Open Dashboard", on_open, default=True),
        item("Phone / LAN QR", lambda: show_qr(hub)),
        pystray.Menu.SEPARATOR,
        item("Pause All", lambda: eng.pause("*", True)),
        item("Resume All", lambda: eng.pause("*", False)),
        item("KILL SWITCH", lambda: eng.kill(not eng.guardian.kill_switch, by="tray"),
             checked=lambda it: eng.guardian.kill_switch),
        pystray.Menu.SEPARATOR,
        item("Settings folder", lambda: open_folder(hub.cfg.config_dir)),
        pystray.MenuItem("Autostart with Windows", lambda icon, it: set_autostart(not autostart_enabled()),
                         checked=lambda it: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        item("Quit", on_quit),
    )
    icon = pystray.Icon("aurum_nexus", Image.open(asset("icon.png")), APP_NAME, menu)
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    return icon


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - desktop runtime
    import multiprocessing
    multiprocessing.freeze_support()
    from aurum_nexus.cli import confirm_live
    from aurum_nexus.hub.server import Hub
    args = argv if argv is not None else sys.argv[1:]
    cfg = load_config(PROJECT_ROOT / "config")
    cfg.live_confirmed = confirm_live(cfg, "--yes-live" in args)
    simulate = "--simulate" in args
    hub = Hub(cfg, simulate=simulate).start()
    hub.banner(simulate)
    quit_evt = threading.Event()

    def open_dashboard():
        url = hub.launch_url()
        cmd = app_window_command(url)
        if cmd:
            subprocess.Popen(cmd)
        else:
            webbrowser.open(url)

    def do_quit():
        quit_evt.set()
        try:
            import webview  # type: ignore
            for w in list(webview.windows):
                w.destroy()
        except Exception:
            pass

    icon = tray(hub, open_dashboard, do_quit)
    try:
        import webview  # type: ignore
        webview.create_window(APP_NAME, hub.launch_url(), width=1440, height=900, min_size=(390, 600),
                              background_color="#02050d")
        # closing the window hides to tray when the tray exists; otherwise it quits
        webview.start(icon=str(asset("icon.png")))
        if icon is None:
            quit_evt.set()
    except Exception:
        open_dashboard()
    quit_evt.wait() if icon is not None else None
    if icon is not None:
        icon.stop()
    hub.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
