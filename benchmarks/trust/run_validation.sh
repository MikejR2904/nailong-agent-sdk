#!/usr/bin/env bash
# Validates commit 1064119: lint, the Windows suite, the Ubuntu suite, then the whole development Trust Profile suite.
# Usage (from benchmarks/trust): bash run_validation.sh [workers]
set -u
WORKERS="${1:-4}"
ROOT="$(cd ../.. && pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
OUT="results"
stamp() { echo "[$(date +%H:%M:%S)] $*"; }
stamp "git: $(git -C "$ROOT" rev-parse HEAD) (code identical to 1064119: $(git -C "$ROOT" diff --stat 1064119 HEAD -- src tests benchmarks/trust/trust_bench | wc -l) differing lines)"
stamp "ruff"
( cd "$ROOT" && "$PY" -m ruff check src tests && "$PY" -m ruff format --check src tests ) > "$OUT/val_lint.log" 2>&1; stamp "ruff status $?"
stamp "windows pytest (python 3.13)"
( cd "$ROOT" && "$PY" -m pytest -m "not docker" -p no:cacheprovider -W ignore -rfEs --durations=15 ) > "$OUT/val_windows_pytest.log" 2>&1; stamp "windows pytest status $?"
stamp "ubuntu pytest (python 3.12, WSL)"
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/nailong-linux/repo_head && PYTHONPATH=src ~/nailong-linux/venv/bin/python -m pytest -m "not docker" -p no:cacheprovider -W ignore -rfEs --durations=15' > "$OUT/val_ubuntu_pytest.log" 2>&1; stamp "ubuntu pytest status $?"
stamp "development suite"
bash run_validation_dev.sh "$WORKERS" > "$OUT/val_driver_dev.log" 2>&1; stamp "development suite done"
stamp "all validation done"
