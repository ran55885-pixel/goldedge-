# PyInstaller spec for "Aurum Nexus.exe" (Windows, onedir). Build with build_windows.bat.
# config/, .env and data/ are NOT bundled: they live next to the exe (installer copies the config templates).
from pathlib import Path

ROOT = Path(SPECPATH).parent
a = Analysis(
    [str(ROOT / "aurum_nexus" / "desktop" / "app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "aurum_nexus" / "web"), "aurum_nexus/web"), (str(ROOT / "assets"), "assets")],
    hiddenimports=["MetaTrader5", "webview", "pystray._win32", "uvicorn.logging", "uvicorn.loops.auto",
                   "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
                   "websockets", "tzdata"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Aurum Nexus", icon=str(ROOT / "assets" / "icon.ico"),
          console=True)  # console kept: shows the LAN URL/QR, setup code and the live-trading confirmation prompt
coll = COLLECT(exe, a.binaries, a.datas, name="Aurum Nexus")
