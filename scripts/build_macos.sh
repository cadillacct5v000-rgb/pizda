#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Сборка .app выполняется на macOS." >&2
  exit 1
fi
.venv/bin/python scripts/check_environment.py
.venv/bin/python -m pytest -q
.venv/bin/python -m PyInstaller --noconfirm KadroGen-macOS.spec
echo "Приложение: dist/KadroGen.app. Для видео нужен установленный FFmpeg (brew install ffmpeg)."
