#!/usr/bin/env bash
set -u
PY="../../.venv/Scripts/python.exe"
S="--split test --seeds 1000000-1000099 --workers 4"
echo "[$(date +%H:%M:%S)] acceptance"
$PY -m trust_bench judge-prepare --task acceptance $S --families doc,data,plan --arms sdk,langgraph --out results/test_judge_acceptance_in.jsonl > results/test_judge_acceptance_prepare.log 2>&1
echo "[$(date +%H:%M:%S)] acceptance status $?"
$PY -m trust_bench judge-prepare --task allocation $S --families ops --arms sdk,langgraph --out results/test_judge_allocation_in.jsonl > results/test_judge_allocation_prepare.log 2>&1
echo "[$(date +%H:%M:%S)] allocation status $?"
echo "all prepared"
