#!/usr/bin/env bash
# Runs every v2 battery and the v1 batteries once, at the sizes PREREGISTRATION_V2.md fixes.
# Usage (from benchmarks/trust):  bash run_v2.sh [workers]
# Results go to results/v2_*; v1 results and reports are not touched.
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
  "$@" > "$OUT/v2_${name}.log" 2>&1
  local status=$?
  echo "[$(date +%H:%M:%S)] $name finished with status $status after $(( $(date +%s) - started ))s"
}
step boundary  $PY -m trust_bench boundary --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_boundary.jsonl"
step staterun  $PY -m trust_bench staterun --seeds 1-200 --workers "$WORKERS" --out "$OUT/v2_staterun.jsonl"
step lineage   $PY -m trust_bench lineage --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_lineage.jsonl"
step crash     $PY -m trust_bench crash --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_crash.jsonl"
step realruns  $PY -m trust_bench realruns --out "$OUT/v2_realruns.jsonl"
step tamper    $PY -m trust_bench tamper --split dev --families doc,data --seeds 1-30 --arms sdk,sdk+anchor,langgraph --workers "$WORKERS" --out "$OUT/v2_tamper.jsonl"
step gates     $PY -m trust_bench gates
step run       $PY -m trust_bench run --split dev --families doc,data --seeds 1-150 --arms sdk,langgraph --workers "$WORKERS" --out "$OUT/v2_dev_run.jsonl"
step verify    $PY -m trust_bench verify --split dev --families doc,data --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_dev_verify.jsonl"
step attack    $PY -m trust_bench attack --split dev --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_dev_attack.jsonl"
step authority $PY -m trust_bench authority --split dev --seeds 1-150 --arms sdk,langgraph,langgraph-plain,sdk-preapproved --workers "$WORKERS" --out "$OUT/v2_dev_authority.jsonl"
step chain     $PY -m trust_bench chain --split dev --seeds 1-150 --workers "$WORKERS" --out "$OUT/v2_dev_chain.jsonl"
step source    $PY -m trust_bench source-tests --out "$OUT/v2_source_tests.json"
echo "[$(date +%H:%M:%S)] all steps done"
