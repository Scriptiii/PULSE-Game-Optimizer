@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, PySide6.QtSvg, psutil" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto :failed
exit /b 0
:failed
echo.
echo PULSE could not start. Check the error above and README.md.
echo Python 3.10 or newer with pip is required.
pause
exit /b 1
