#!/usr/bin/env bash
# Finishes the locked test split after the 2026-10-26 01:13 interruption (see DEVIATIONS.md).
set -u
WORKERS="${1:-4}"
PY="../../.venv/Scripts/python.exe"
OUT="results"
REASON="the 2026-10-26 01:13 run was killed by a host resource failure (fork: Resource temporarily unavailable) after 172 of 1600 results; the partial output is kept in results/interrupted; no code changed between the two runs"
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
step authority $PY -m trust_bench authority $S --seeds 1000000-1000099 --arms sdk,langgraph,langgraph-plain,sdk-preapproved --allocations rule,all-human,none,random --rerun-reason "$REASON" --out "$OUT/test_authority.jsonl"
step chain     $PY -m trust_bench chain $S --seeds 1000000-1000099 --out "$OUT/test_chain.jsonl"
echo "[$(date +%H:%M:%S)] all steps done"
