#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Сначала выполните: bash scripts/setup_macos.sh" >&2
  exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python scripts/check_environment.py
exec .venv/bin/python app.py "$@"
