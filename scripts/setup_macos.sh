#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Этот скрипт предназначен для macOS." >&2
  exit 1
fi
if [[ -x /opt/homebrew/bin/brew ]]; then
  export PATH="/opt/homebrew/bin:$PATH"
elif [[ -x /usr/local/bin/brew ]]; then
  export PATH="/usr/local/bin:$PATH"
fi
if ! command -v brew >/dev/null; then
  echo "Установите Homebrew по инструкции https://brew.sh и повторите запуск." >&2
  exit 1
fi
brew install python@3.12 ffmpeg deno
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
if [[ "$(uname -m)" == arm64 ]] && [[ "$("$PYTHON" -c 'import platform; print(platform.machine())')" != arm64 ]]; then
  echo "Для Apple Silicon нужен Python arm64. Запустите Terminal без Rosetta и используйте /opt/homebrew." >&2
  exit 1
fi
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/check_environment.py
echo "Готово. Запуск: bash scripts/run_macos.sh"
