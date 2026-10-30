#!/usr/bin/env bash
# Reruns the Ubuntu suite after the token_bench collection fix, once the development suite has finished.
set -u
OUT="results"
while ! grep -q "all validation done" "$OUT/val_driver.log"; do sleep 60; done
echo "[$(date +%H:%M:%S)] ubuntu pytest rerun"
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/nailong-linux/repo_head && PYTHONPATH=src ~/nailong-linux/venv/bin/python -m pytest -m "not docker" -p no:cacheprovider -W ignore -rfEs --durations=15' > "$OUT/val_ubuntu_pytest_rerun.log" 2>&1
echo "[$(date +%H:%M:%S)] ubuntu pytest rerun status $?"
echo "ubuntu rerun done"
