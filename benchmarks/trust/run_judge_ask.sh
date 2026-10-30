#!/usr/bin/env bash
# Asks Jev the prepared test-split questions once, in the order of PREREGISTRATION.md section 7 step 3.
set -u
PY="../../.venv/Scripts/python.exe"
ask() {
  local task="$1" calls="$2" tokens="$3"
  echo "[$(date +%H:%M:%S)] ask $task"
  $PY -m trust_bench judge-ask --inputs results/test_judge_${task}_in.jsonl --out results/test_judge_${task}_ans.jsonl --split test --max-calls "$calls" --max-input-tokens "$tokens" --workers 8 > results/test_judge_${task}_ask.log 2>&1
  echo "[$(date +%H:%M:%S)] ask $task status $?"
  $PY -m trust_bench judge-score --inputs results/test_judge_${task}_in.jsonl --answers results/test_judge_${task}_ans.jsonl --out results/test_judge_${task}_rows.jsonl > results/test_judge_${task}_score.log 2>&1
  echo "[$(date +%H:%M:%S)] score $task status $?"
}
ask allocation 1000 1000000
ask acceptance 3000 5000000
ask diagnosis 8000 25000000
echo "all asked"
