#!/usr/bin/env bash
# Runs every v2 battery and the v1 batteries once, at the sizes PREREGISTRATION_V3.md (validation of 1064119) fixes.
# Usage (from benchmarks/trust):  bash run_v2.sh [workers]
# Results go to results/val_*; v1 results and reports are not touched.
set -u
WORKERS="${1:-6}"
PY="../../.venv/Scripts/python.exe"
OUT="results"
mkdir -p "$OUT"
step() {
  local name="$1"; shift
  local started
  started=$(date +%s)
  echo "[$(date +%H:%M:%S)] $name: $*"
  "$@" > "$OUT/val_${name}.log" 2>&1
  local status=$?
  echo "[$(date +%H:%M:%S)] $name finished with status $status after $(( $(date +%s) - started ))s"
}
step boundary  $PY -m trust_bench boundary --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_boundary.jsonl"
step staterun  $PY -m trust_bench staterun --seeds 1-200 --workers "$WORKERS" --out "$OUT/val_staterun.jsonl"
step lineage   $PY -m trust_bench lineage --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_lineage.jsonl"
step crash     $PY -m trust_bench crash --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_crash.jsonl"
step realruns  $PY -m trust_bench realruns --out "$OUT/val_realruns.jsonl"
step tamper    $PY -m trust_bench tamper --split dev --families doc,data --seeds 1-100 --arms sdk,sdk+anchor,sdk-default,langgraph --workers "$WORKERS" --out "$OUT/val_tamper.jsonl"
step gates     $PY -m trust_bench gates
step run       $PY -m trust_bench run --split dev --families doc,data --seeds 1-150 --arms sdk,langgraph --workers "$WORKERS" --out "$OUT/val_dev_run.jsonl"
step verify    $PY -m trust_bench verify --split dev --families doc,data --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_dev_verify.jsonl"
step attack    $PY -m trust_bench attack --split dev --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_dev_attack.jsonl"
step authority $PY -m trust_bench authority --split dev --seeds 1-150 --arms sdk,langgraph,langgraph-plain,sdk-preapproved --workers "$WORKERS" --out "$OUT/val_dev_authority.jsonl"
step chain     $PY -m trust_bench chain --split dev --seeds 1-150 --workers "$WORKERS" --out "$OUT/val_dev_chain.jsonl"
step source    $PY -m trust_bench source-tests --out "$OUT/val_source_tests.json"
echo "[$(date +%H:%M:%S)] all steps done"
