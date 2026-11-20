@echo off
REM Build "Aurum Nexus.exe" (PyInstaller) and, if Inno Setup is installed, the installer.
REM Requires: Python 3.11+ (64-bit) on PATH. Run from the project folder.
setlocal
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv || goto :err )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
python tools\make_icon.py || goto :err
python -m pytest -q || echo WARNING: tests failed - review before distributing
pyinstaller --noconfirm --distpath build\out\dist --workpath build\out\work build\aurum_nexus.spec || goto :err
xcopy /e /i /y config "build\out\dist\Aurum Nexus\config" >nul
copy /y .env.example "build\out\dist\Aurum Nexus\.env.example" >nul
copy /y README_he.md "build\out\dist\Aurum Nexus\" >nul
echo.
echo Built: build\out\dist\Aurum Nexus\Aurum Nexus.exe
where iscc >nul 2>nul && iscc build\installer.iss && echo Installer: build\out\AurumNexusSetup.exe
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" build\installer.iss
goto :eof
:err
echo BUILD FAILED
exit /b 1
