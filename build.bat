@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt "pyinstaller>=6.16,<7"
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name PULSE --icon "pulse\assets\pulse.ico" --add-data "pulse\assets:pulse\assets" --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets main.py
if errorlevel 1 goto :failed
echo.
echo Ready: dist\PULSE\PULSE.exe
echo Copy the WHOLE dist\PULSE folder when moving the app.
pause
exit /b 0
:failed
echo Build failed. Check the error above.
pause
exit /b 1
