#!/usr/bin/env bash
# Builds a standalone app with PyInstaller, bundling the Playwright Chromium.
#
#   macOS / Linux:  ./build_app.sh
#
# On macOS this produces dist/TSRSims4ModDownloader.app (and a CLI binary);
# on Linux a single executable. A Windows .exe must be built ON WINDOWS with
# build_app.bat (PyInstaller cannot cross-compile).
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="TSRSims4ModDownloader"
# Where Playwright will store the browser so we can bundle it next to the app.
export PLAYWRIGHT_BROWSERS_PATH="$PWD/ms-playwright"

echo ">> Dependencies (pyinstaller + playwright + chromium)…"
python3 -m pip install -r requirements.txt pyinstaller
python3 -m playwright install chromium

echo ">> Building with PyInstaller…"
python3 -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --collect-all playwright \
  --add-data "ms-playwright:ms-playwright" \
  app.py

echo ">> Done. Output in: dist/$APP_NAME"
