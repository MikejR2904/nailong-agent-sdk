# Self-evolution, step 1: retention-only, offline

This step lets a host improve its episodic-memory retention model from its own runs. It makes no model calls. Only the retention utility model evolves; the PCKP solver, the verifier and the trust boundary do not.

## The loop

1. **Record.** Attach `RetentionOutcomeLog(path, task_group=...).observe` as the store's `retention_observer`. Each compaction that selects episodes appends one `RetentionDecision` (task digest, budget, predictor identity, per-episode features, token costs, prerequisite graph, utilities used, selection, solver status). The task text is never stored.
2. **Attach outcomes.** The host calls `record_outcome(decision_id, score, source=..., trial=...)` with a score in [0, 1]. `source` must be `synthetic` or `measured`.
3. **Retrain.** `python -m dfl_bench.evolve retrain` builds preference pairs from decisions of the same task that differ in selection and outcome, then trains a linear or MLP ranking model. A log with no such contrast fails with an explicit error: the host must run exploratory (shadow) arms, because a single deterministic policy never produces contrasting decisions.
4. **Gate.** The candidate is compared with the incumbent (the static utilities logged with each decision, or the promoted model) on a hash-split gate set (60/15/25 train/validation/gate by task digest).
5. **Register, promote, roll back.** `ModelRegistry` keeps digest-verified artifacts and an append-only history. `promote` requires a passed gate; `rollback` restores the previous version.

## Gate rules

All must hold, otherwise the candidate is rejected with the named reason:

- at least `min_tasks` (8) and `min_pairs` (20) held-out tasks and pairs;
- pair accuracy better than the incumbent by a sign test at `alpha` 0.05;
- no task group regresses by more than `group_tolerance` 0.05;
- every candidate selection solves to `OPTIMAL`;
- budget use of at least 0.9 of the incumbent's (evaluated under the configured `QuantizationPolicy`; the CLI default is `floor=1`, see 06);
- when logged outcomes exist for the same selections, at least `min_matched_tasks` (5) matched tasks and no loss on them.

Synthetic and measured outcomes are never mixed in one dataset.

## CLI

```bash
python -m dfl_bench.evolve retrain --log LOG --registry DIR --model-type linear
python -m dfl_bench.evolve promote --registry DIR --version V --approver NAME --reason TEXT
python -m dfl_bench.evolve rollback --registry DIR --approver NAME --reason TEXT
python -m dfl_bench.evolve status --registry DIR
```

## Limits

- No canary or live regression monitor yet: promotion is a human decision on gate evidence, and rollback is manual.
- Gate power depends on how much contrast the exploration produced; small logs reject by design.
- Outcome scores are only as trustworthy as the host's verifier; this step does not evaluate that.
- Nothing here has been run on real agent traffic. Tests use synthetic worlds; they show the machinery works, not that it improves a real agent.
