@echo off
REM Builds a standalone Windows .exe with PyInstaller, bundling Playwright Chromium.
REM Run this ON WINDOWS (PyInstaller cannot cross-compile from macOS/Linux):
REM
REM   build_app.bat
REM
REM Output: dist\TSRSims4ModDownloader\TSRSims4ModDownloader.exe
setlocal
cd /d "%~dp0"

set APP_NAME=TSRSims4ModDownloader
set PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright

echo >> Dependencies (pyinstaller + playwright + chromium)...
python -m pip install -r requirements.txt pyinstaller
python -m playwright install chromium

echo >> Building with PyInstaller...
python -m PyInstaller ^
  --noconfirm --clean --windowed ^
  --name "%APP_NAME%" ^
  --collect-all playwright ^
  --add-data "ms-playwright;ms-playwright" ^
  app.py

echo >> Done. Output in: dist\%APP_NAME%
endlocal
