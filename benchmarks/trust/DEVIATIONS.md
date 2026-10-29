# Deviations from the pre-registration

Nothing yet. After the lock, every change to the plan is recorded here with its date, what
changed, why, and which results it affects, and is written before the affected result is read
(`PREREGISTRATION.md`, section 9). A job that has to be run again is recorded here too, with the
`--rerun-reason` given to the harness.

## 2026-10-26 / 2026-10-27: interrupted test-split runs

* `authority --split test` (registered 2026-10-25 17:12 UTC, 1,600 jobs) was killed at 01:13 local
  time by a host resource failure (`fork: Resource temporarily unavailable`) after 172 results. The
  partial file is kept as `results/interrupted/test_authority_partial_172.jsonl`. The step was run
  again in full with `--rerun-reason` (`run_test_resume.sh`) and finished with status 0 on
  2026-10-27 at 08:47; `chain` ran after it. No file under the lock changed between the two runs
  (`verify-lock` held), and the jobs are deterministic in their seeds.
* `judge-prepare --task acceptance --split test` was stopped by hand before it finished; its partial
  output is kept as `results/interrupted/test_judge_acceptance_in_partial.jsonl` and nothing was sent
  to a model. The `allocation` questions were not prepared. No judge call has been made on the test
  split. The diagnosis questions (6,600 for 3,800 jobs) were prepared offline.

## 2026-10-30: code added under the lock after the test split was run

`src/nailong_agent_sdk/developer_tools/attribute_root_cause.py` and the `attribute-root-cause`
command (and their exports in `developer_tools/__init__.py` and `cli.py`) were added after the
locked test split and the judge arm had run. The lock (commit `2ce3ab6`, `LOCK.json`) therefore no
longer matches the tree: `verify-lock` reports the system hash as changed. The results in
`reports/test.md` stay bound to commit `2ce3ab6` and are not affected, because no job ran against the
new code and the new module is not called by any Trust Profile battery. Any further test-split
claim needs a new lock; the test split is spent for the jobs already run.
