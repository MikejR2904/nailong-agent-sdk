# Generalization evaluation (protocol `dfl-generalize-1`)

**Evidence kind: synthetic oracle only.** No model was called. Nothing here is a measured agent
result. Pre-registration: `benchmarks/dfl/PREREGISTRATION_GENERALIZE.md`. Full tables:
`benchmarks/dfl/results/generalize_confirmatory/REPORT.md`. Mathematics: [08](08-generalization-mathematics.md).

## What was built

| Area | Where |
|---|---|
| Regret-weighted pairwise, listwise, set regression, Group-DRO | `memory/utility_objectives.py` (`effect_weights`, `build_objective_dataset`, `train_objective`, `dro_update`) |
| Feature schema v2 (task coverage, redundancy, value unlocked, exclusivity, failure propagation) | `memory/utility_features_v2.py`; `LearnedUtilityPredictor(feature_schema=...)` |
| Static mixing, shift detector, ensemble disagreement, logged fallback | `memory/utility_calibration.py`; audit in `dossier["learned_ranking"]["calibration"]` |
| Decisions logged under any schema | `build_decision(..., feature_schema)`, store `retention_feature_schema` |
| Offline evolution: any objective, schema recorded, gate against every incumbent including a tuned static policy, `disable` | `benchmarks/dfl/dfl_bench/evolve/{retrain,gate,registry,__main__}.py` |
| Benchmark: six families, ten folds, tuned static, boundary candidates, inner-LOFO calibration | `benchmarks/dfl/dfl_bench/generalize/` |

The v1 objective, trainers, default schema and static PCKP are unchanged. The v2 graph features are
bipartite-aware because the store only lets actions depend on exploratory episodes, which makes
transitive reachability identical to direct dependence; no GNN was built (the simpler features show
no limitation to motivate one).

## Baseline reproduction

Commit `0feb6c1986b5efd61fec09c5ba0ac380d341fa87`, Windows 11, Python 3.13.1. A clean worktree at
that commit ran `pytest tests -m "not live" --ignore=tests/docker`; it was run concurrently with
a 14-worker benchmark and showed one failure, `tests/specifications/test_evidence_selection.py::test_evidence_large_budget_behaviour`,
a 15-second wall-clock assertion. The same test passes on the current tree when the machine is idle.
The run's final summary line was not captured, so no total count is claimed for the baseline.

## Confirmatory results (seeds 3000-3002, 360 `lofo:` test instances)

| `lofo:` method | mean quality | worst fold | worst fold per seed |
|---|---|---|---|
| tuned-static-pckp | 0.644 | 0.497 | 0.568 |
| static-pckp | 0.643 | 0.578 | 0.588 |
| calibrated-pckp | 0.633 | 0.553 | 0.583 |
| regression-v1 | 0.605 | 0.506 | 0.525 |
| dro-pairwise-v1 | 0.597 | 0.510 | 0.530 |
| listwise-v1 | 0.597 | 0.490 | 0.522 |
| regret-pairwise-v1 | 0.595 | 0.506 | 0.519 |
| combined-validation | 0.595 | 0.435 | 0.481 |
| pairwise-v1 | 0.592 | 0.517 | 0.530 |
| linear-dfl (previous) | 0.549 | 0.287 | 0.413 |
| regression-pckp (previous) | 0.500 | 0.339 | 0.371 |

All selections were solver-optimal (no `BEST_EFFORT`), with 96-99% of the token budget used.

**Pre-registered tests (Holm over two):**

- **P1** `combined-validation` above `tuned-static-pckp`: **not supported.** Difference -0.049,
  95% interval [-0.075, -0.024], Holm p 0.016: it is significantly *worse*.
- **P2** `combined-validation` above `regression-pckp`: **supported.** +0.095, [+0.056, +0.133],
  Holm p 0.016.

## Findings, including negative ones

1. **No learned method beat the static baseline on held-out families.** Against `static-pckp`:
   regret-pairwise -0.048, listwise -0.046, DRO pairwise -0.046, regression-v1 -0.038 (Holm
   significant for the first three). The tuned static policy equals the default within noise.
2. **The new learners beat the previously evaluated ones.** Every new method is above
   `regression-pckp` (+0.095 to +0.105) and above `linear-dfl` (about +0.05). Part of this gain comes
   from choosing the quantisation floor and L2 on validation decision quality and from the new
   trainer, not from the objective; `pairwise-v1` (same loss as the old linear baseline, new
   trainer) shows the same lift. Objective effects are small by comparison.
3. **Objective ranking is not established.** Regret-weighted vs plain pairwise, listwise vs
   regression, normalised vs unnormalised weights, and Group-DRO vs pooled differ by about 0.01 on
   mean quality with overlapping intervals (see the report's secondary tables); no secondary
   comparison is claimed as confirmed.
4. **Calibration is the only safeguard that tracks static.** `calibrated-pckp` is within 0.01 of
   static on mean (not significant) and its worst fold (0.553) is far above the previous learned
   methods' (0.287, 0.339), at a fallback rate of 0.11. It is not above static.
5. **Validation-based selection did not transfer.** `combined-validation` picked learners that were
   best on in-distribution validation and lost on the held-out family (worst fold 0.435).
6. **Unseen budgets and long histories:** no method differs significantly from static at the high
   budget (all within +-0.02); at the low budget static is best (0.348) and listwise and DRO are
   significantly lower; on long histories static (0.681) is not significantly above listwise.
7. **Boundary-aware candidates, v2 features and DRO** gave no confirmed benefit
   (`+boundary` variants and `regression-v2` are in the report; none beats its v1 counterpart in a
   way that survives correction).
8. **Pairwise ranking accuracy on held-out candidate pools** is highest for static (0.634) and
   regret-pairwise (0.628), consistent with finding 1. Set-utility calibration error and
   per-method training seconds (0.2-32 s) are in the report; model-call cost is zero.

## Claims supported and not supported

Supported (synthetic oracle): the new training stack is a clear improvement over the previously
evaluated learned methods under leave-one-family-out; calibrated fallback bounds the downside
relative to those methods; every selection keeps PCKP feasibility, closure and certificates.

Not supported: that any learned retention beats a tuned static baseline on unseen families; that
ranking beats regression or the reverse; that regret weighting, listwise training, Group-DRO or v2
features help generalization; anything about real agents, other model backends or recorded real
histories.

## Not done

- Live measured evaluation of the new methods (it needs paid calls; the live harness is unchanged
  and the new methods have not been run through it). Command for the existing harness:
  `python -m dfl_bench.live --out results/live` from `benchmarks/dfl`.
- Unseen model backends, recorded real-agent histories, GNN.
- Five confirmatory seeds were planned; three were run (recorded in the pre-registration before the run).
- Development results (`generalize_dev2`, seeds 0-1) informed the protocol; `generalize_dev1_superseded`
  predates the early-stopping fix and is kept only for the record.

## Reproduce

```bash
cd benchmarks/dfl
PYTHONPATH=../../src:. python -m dfl_bench.generalize run --seeds 3000 3001 3002 --out results/generalize_confirmatory --workers 14 --templates 5 --per-template 4
PYTHONPATH=../../src:. python -m dfl_bench.generalize report --results results/generalize_confirmatory --protocol PREREGISTRATION_GENERALIZE.md
```

## Round 2: shrinkage towards the static policy (seeds 4000-4002)

Pre-registered in `benchmarks/dfl/PREREGISTRATION_GENERALIZE_2.md` after a development round on seeds
0-1. A linear model is regularised towards the static policy's own weights (`static_prior_weights`;
static is a point of the linear class) and the shrinkage is chosen by leave-one-family-out decision
quality on the training families; `shrunk-selected` is the best of six such variants by that same inner
criterion. Results on 360 `lofo:` instances:

| method | mean quality |
|---|---|
| static-pckp / tuned-static-pckp | 0.604 / 0.604 |
| shrunk-regression-v2 | 0.601 (-0.003 vs static, 95% CI [-0.014, +0.007]) |
| shrunk-dro-pairwise-v1, -regret-pairwise-v1, -listwise-v2 | 0.591-0.592 |
| shrunk-selected | 0.578 (-0.026 vs static, Holm p 0.105; vs tuned static Holm p 0.62) |
| unshrunk pairwise-v1 / regression-v1 | 0.540 / 0.573 |
| previous linear-dfl / regression-pckp | 0.509 / 0.487 |

- **Q1 and Q2 (shrunk-selected above static / tuned static): not supported.** The point estimate is
  below static, and the inner-LOFO selection picked a weaker variant than the best fixed one.
- Shrinkage removes most of the downside of unshrunk learning (the best fixed variant is statistically
  indistinguishable from static) but produces no gain. Nothing in this repository makes a learned
  retention model beat static PCKP on an unseen family in this synthetic world.
- Headroom exists per family: an in-sample random search over the linear weights improved on static by
  0.10-0.20 on individual families (shifted-short 0.525 to 0.725, aligned-short 0.600 to 0.713, shifted-long 0.650
  to 0.787; in-sample, optimistic), but the best weights differ by family, and an unseen family supplies no
  outcomes to find them from. Gains would need family-specific outcomes (online or few-shot adaptation),
  which this work does not build.
