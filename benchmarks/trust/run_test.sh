#!/usr/bin/env bash
# Runs the locked test split once, as PREREGISTRATION.md section 7 step 2 describes.
# Usage (from benchmarks/trust):  bash run_test.sh [workers]
set -u
WORKERS="${1:-6}"
PY="../../.venv/Scripts/python.exe"
OUT="results"
step() {
  local name="$1"; shift
  local started
  started=$(date +%s)
  echo "[$(date +%H:%M:%S)] $name: $*"
  "$@" > "$OUT/test_${name}.log" 2>&1
  local status=$?
  echo "[$(date +%H:%M:%S)] $name finished with status $status after $(( $(date +%s) - started ))s"
}
S="--split test --workers $WORKERS"
step run       $PY -m trust_bench run $S --families doc,data,plan --seeds 1000000-1000099 --arms sdk,langgraph --out "$OUT/test.jsonl"
step tamper    $PY -m trust_bench tamper $S --families doc,data --seeds 1000000-1000079 --arms sdk,sdk+anchor,sdk-default,langgraph --out "$OUT/test_tamper.jsonl"
step verify    $PY -m trust_bench verify $S --families doc,data,plan --seeds 1000000-1000099 --out "$OUT/test_verify.jsonl"
step attack    $PY -m trust_bench attack $S --seeds 1000000-1000099 --out "$OUT/test_attack.jsonl"
step authority $PY -m trust_bench authority $S --seeds 1000000-1000099 --arms sdk,langgraph,langgraph-plain,sdk-preapproved --allocations rule,all-human,none,random --out "$OUT/test_authority.jsonl"
step chain     $PY -m trust_bench chain $S --seeds 1000000-1000099 --out "$OUT/test_chain.jsonl"
echo "[$(date +%H:%M:%S)] all steps done"
