#!/bin/bash
set -euo pipefail
cd /workspace/pizda
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt
.venv/bin/python scripts/check_environment.py
