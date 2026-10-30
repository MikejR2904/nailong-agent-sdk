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

## Round 3: static-anchored advantage model with a conservative gate (seeds 5000-5002)

Pre-registered in `benchmarks/dfl/PREREGISTRATION_GENERALIZE_3.md` (`generalize/advantage.py`).
A linear utility predicts `Q(S) - Q(S0)` against the exact static selection `S0`; a bootstrap
ensemble gates deviation (`mean - beta*spread > delta`), otherwise the static selection itself is
returned. Gate parameters come from leave-one-family-out gain on the training families. 360 `lofo:`
instances, schema v2:

| method | mean change vs static | worst-family change | negative-transfer rate | deviation rate | change when deviating |
|---|---|---|---|---|---|
| tuned-static-pckp | +0.009 | -0.032 | 0.08 | 0.37 | +0.024 |
| advantage-v2-ungated | -0.023 | -0.098 | 0.19 | 0.63 | -0.036 |
| advantage-v2 (gated) | -0.026 | -0.100 | 0.10 | 0.28 | -0.096 |
| shrunk-regression-v2 | -0.045 | -0.109 | 0.16 | 0.44 | -0.102 |
| pairwise-v1 / -equalcard | -0.029 / -0.075 | -0.083 / -0.120 | 0.21 / 0.33 | 0.74 / 0.92 | -0.039 / -0.082 |

- **R1 (advantage-v2 above static): not supported.** -0.026, 95% CI [-0.045, -0.009], Holm p 0.030:
  significantly below static. **R2 (above tuned static): not supported**, -0.036 (Holm p 0.081).
- The development gain (+0.005) did not replicate; it was within noise and selected on the same
  seeds used to choose the method.
- The gate did what a gate can: it cut negative transfer from 0.19 to 0.10 and deviations from 63% to
  28% of tasks. It did not make the deviations that remain profitable: on the instances where it
  deviates the mean change is -0.096, so the ensemble's agreement is not a calibrated indication of
  real advantage on an unseen family.
- **Equal- vs variable-cardinality pairs:** restricting pairwise training to equal-size pairs was worse,
  not better (-0.075 vs -0.029), so the offset-identifiability argument of section 6 of document 08 did
  not translate into a measurable selection effect in this world.
- Overall across rounds 1-3: no learned retention model beat static PCKP on held-out families;
  the safeguards (shrinkage, gating, fallback) reduce harm but do not create gain. Untested: few-shot
  adaptation with target-family outcomes, nonadditive coverage-aware objectives, real-agent families.

## Round 4: few-shot adaptation to an unseen family (seeds 6000-6004)

Pre-registered in `benchmarks/dfl/PREREGISTRATION_GENERALIZE_4.md` (`generalize/fewshot.py`). For
each held-out family, 2 of 6 templates are a support set with verified outcomes; every method is scored
on the other 4 templates (480 query instances over 5 seeds). The non-learning comparator
`tuned-static-support` tunes the five static weights on the same support set and consumes more outcome
evaluations (41 policies x 8 instances x 16 trials) than the adapted models (about 320).

| method | mean quality | change vs static | negative-transfer | deviation | worst-family change |
|---|---|---|---|---|---|
| adapt-listwise-static | 0.622 | +0.026 | 0.07 | 0.43 | +0.000 |
| adapt-regression-static | 0.618 | +0.022 | 0.09 | 0.45 | -0.018 |
| adapt-listwise-source | 0.602 | +0.006 | 0.23 | 0.86 | -0.059 |
| tuned-static-support | 0.598 | +0.002 | 0.07 | 0.35 | -0.022 |
| static-pckp | 0.596 | 0 | - | - | - |
| tuned-static-pckp (source-tuned) | 0.570 | -0.026 | 0.10 | 0.31 | -0.060 |
| adapt-regression-source | 0.568 | -0.028 | 0.28 | 0.88 | -0.097 |

**Pre-registered hypotheses (Holm over two):**
- **F1** `adapt-listwise-source` above `tuned-static-support`: **not supported** (+0.004, 95% CI [-0.028, +0.034]).
- **F2** `adapt-listwise-source` above `tuned-static-pckp`: **not supported** after correction
  (+0.032, raw p 0.0315, Holm over the two registered tests 0.063).

**Not registered, exploratory, and not confirmed:** the variants anchored to the static policy did better than the
registered one. `adapt-listwise-static` is +0.053 over `tuned-static-pckp` (95% CI [+0.030, +0.075], raw
p 0.0005) and +0.024 over `tuned-static-support` (CI [-0.001, +0.049], raw p 0.061), with no
worst-family loss against static and 7% negative transfer. This is the best result in the study and the
only candidate for a real gain over a tuned static baseline, but it was selected after seeing these data
and its interval against the equally-informed comparator includes zero. It needs its own pre-registered
confirmation on new seeds before it can be claimed.

Other observations: a static policy tuned on source families (`tuned-static-pckp`) is worse than the
untuned default on held-out families (-0.026), which is itself an instance of the transfer failure this
work set out to study. The source-model prior hurt (`adapt-regression-source` -0.028); anchoring to the
static prior did not.

## Live (measured) few-shot adaptation

**Evidence kind: measured.** Real model answers (`glm-5p3-flash` via Fireworks, same settings as the earlier live
runs) scored by the deterministic verifier on generated tool-call histories. Pre-registered in
`benchmarks/dfl/PREREGISTRATION_LIVE_FEWSHOT.md`; runner `dfl_bench/live/fewshot_run.py`; output in
`benchmarks/dfl/results/live_fewshot/`. Three families held out in turn; 2 of 6 templates (12 tasks) are
the support set, the other 4 templates (24 tasks per family, 72 in total, 3 trials each) are scored. Cost: 707 new
calls, 1.83M input and 0.06M output tokens (1,462 outcomes came from the earlier ledger); 6 output-format failures.

| method | ledger | deploy | triage | mean | worst family |
|---|---|---|---|---|---|
| adapt-listwise-static | 0.824 | 1.000 | 0.889 | **0.904** | **0.824** |
| adapt-regression-static | 0.690 | 1.000 | 0.806 | 0.832 | 0.690 |
| static-pckp | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |
| tuned-static-support | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |

- **L1** `adapt-listwise-static` above `tuned-static-support`: **supported.** +0.157, 95% CI [+0.062, +0.261], Holm p 0.002.
- **L2** `adapt-listwise-static` above `static-pckp`: **supported.** +0.157, same interval, Holm p 0.002.
- Unseen-family floor: the worst-family change against static is +0.000 (limit was -0.05); negative-transfer rate 4%,
  deviation rate 56%. The regression variant also beat both baselines (+0.085, Holm p 0.03) with no negative transfer.
- Validity controls passed in every family: retaining all evidence scored 1.0 and removing every fact-bearing episode scored 0.0.

**Where the gain comes from and what limits it.**
- Almost all of it is the `ledger` family (0.435 to 0.824); `triage` gains +0.083; `deploy` is at the ceiling
  for every method (static already scores 1.0), so the rest of the mean reflects that one family.
- The tuned-static baseline found nothing better than the default policy on the support set (20 candidate
  policies mostly produced the selection static already makes under this tight budget), so it equals static
  here. It is the correct comparator for the registered question, but a stronger search might close part
  of the gap.
- The adapted model deviated from static only where the support-set cross-validation said it helped:
  in `deploy` it fell back to the static prior.
- Only 72 query tasks, 12 template clusters, one model, generated histories, one run. The
  synthetic oracle round gave a much smaller gain (+0.026), so the size of this live effect should not be
  assumed to transfer to other families or models. Absolute unseen quality is 0.82-1.00 per family
  (mean 0.904), not uniformly above 0.9: `ledger` is 0.824.
- The few-shot setting uses 12 measured support tasks (about 500 model calls) per new family; the
  non-adapting baseline uses the same support set.

## Live: rarity features and leave-one-out supervision (negative result, plus a scorecard)

Pre-registered in `benchmarks/dfl/PREREGISTRATION_LIVE_LOO.md`; runner `dfl_bench/live/fewshot_run.py --loo`;
results in `benchmarks/dfl/results/live_fewshot_loo/`. Same worlds, model and 72 query tasks as the
few-shot run above. New: feature schema v3 (six rarity features), leave-one-out (LOO) criticality
(`generalize/loo.py`: remove one episode and its dependents from the full history, criticality = drop in
verifier score), and an evaluation-only scorecard. Cost: 3,105 new calls, 10.9M input tokens.

| method | ledger | triage | mean quality | critical recall | critical missed / total | distraction load |
|---|---|---|---|---|---|---|
| adapt-listwise-static (previous best) | 0.824 | 0.889 | **0.904** | 0.932 | 23 / 262 | 0.213 |
| adapt-listwise-static-v3 (features only) | 0.796 | 0.889 | 0.895 | 0.921 | 27 / 262 | 0.229 |
| adapt-regression-static | 0.690 | 0.806 | 0.832 | 0.895 | 37 / 262 | 0.251 |
| adapt-loo-listwise-static-v3 (candidate) | 0.495 | 0.889 | 0.795 | 0.878 | 45 / 262 | 0.274 |
| adapt-loo-listwise-static-v1 (LOO only) | 0.472 | 0.889 | 0.787 | 0.863 | 49 / 262 | 0.288 |
| static-pckp / tuned-static-support | 0.435 | 0.806 | 0.747 | 0.839 | 59 / 262 | 0.320 |

(`deploy` is 1.000 for every method.)

- **M1 (candidate above `adapt-listwise-static`): not supported; it is worse**, -0.110, 95% CI [-0.201, -0.026]
  (`ledger` fell from 0.824 to 0.495). **M2 (above static)** holds numerically (+0.048, CI [+0.015, +0.083],
  Holm over the two registered tests about 0.001) but is not useful: the candidate is far below the method it was
  meant to improve. The worst-family floor against static is met (+0.000).
- **Rarity features alone changed nothing** (-0.009, CI [-0.023, +0.000]).
- **LOO supervision hurt.** The labels are not sparse: in `ledger`, 60 of 107 support episodes (56%) and 119 of 227
  query episodes are critical when the rest of the history is present (`deploy` 28%, `triage` 39%). Removing an
  episode from the *full* history says it matters, not that it beats its neighbours for a place under a tight
  budget, so "critical > neutral" pairs mostly rank episodes the budget forces us to choose between, and they
  override the set-level outcomes that carry that information.
- **The scorecard is useful even though the method failed.** Across the seven methods quality, critical recall,
  critical episodes missed and distraction load all order the methods identically: static misses 59 of 262 critical
  episodes and spends 32% of retained tokens on episodes whose removal changes nothing, while the best method
  misses 23 and spends 21%. Evidence coverage alone orders them less cleanly (static 0.875 vs adapted 0.944, but
  `triage` static has coverage 1.0 and still scores 0.806), which is why retention quality needs the extra columns.
- **Not done / next:** use criticality as a *magnitude* target under the budget (marginal value per token, measured on
  the selected set rather than the full history) instead of as pairs; 3-level keep/summarize/drop retention;
  coverage-aware selection. None is built.

## Three-level retention: keep, summarise or drop (model-free check)

`memory/tiered_retention.py` plans, jointly and exactly under one token budget, which episodes to keep in full,
which to keep as a grounded extractive summary and which to drop. Each summarisable episode becomes a *base* item
(the summary: cost `s`, utility `f*u`) and an *upgrade* item (the rest: cost `c-s`, utility `(1-f)*u`, requires the
base), so any keep-or-drop selection stays feasible and the tiered optimum is not below the binary one (tested by
exhaustive enumeration over tier assignments). Summaries are structure-aware and extractive: short fields are
kept as `key=value`, prose is reduced to a word-boundary prefix plus its identifiers, fragments are chosen by
information per token; nothing is generated, and `verify_summary` rejects ungrounded or identifier-poor ones.
It plans only; applying a plan to the store is not built. Pre-registration:
`benchmarks/dfl/PREREGISTRATION_TIERED.md`; harness `dfl_bench/tiered.py`. **No model was called.**

The measure is *fact survival*: does each value the live verifier expects still appear in the retained
text (full episodes plus summaries) at the task's own budget? It is a necessary condition for a correct answer,
not answer quality, and the planner's fidelity is its own salience measure, so the check is partly self-referential.

**First summariser (term-based; seeds 4100-4102, `results/tiered_v1`): negative.** Under static utilities fact
survival fell in `deploy` (1.000 to 0.877) and `triage` (0.991 to 0.710) because expected values are short fields the
summariser did not protect, while the planner trusted a fidelity of about 0.9 enough to summarise nearly every
episode. This is a Goodhart failure of the fidelity proxy. The summariser was then rewritten (above), after seeing
that result, and re-tested on fresh worlds.

**Structure-aware summariser, fresh worlds (seeds 4200-4202, 18 template clusters, `results/tiered_v2_confirmatory`):**

| utilities / variant | ledger | deploy | triage | all families | episodes represented | tokens used |
|---|---|---|---|---|---|---|
| static, binary | 0.605 | 1.000 | 0.997 | 0.867 | 4.3 | 0.89 |
| static, tiered | 0.997 | 1.000 | 0.818 | **0.938** | **8.9** (3.0 full + 6.0 summary) | 0.96 |
| oracle, binary | 0.938 | 1.000 | 1.000 | 0.979 | 4.3 | 0.88 |
| oracle, tiered | 0.997 | 1.000 | 0.920 | 0.972 | 8.2 | 0.97 |

- **T1 (static utilities, tiered not below binary): not shown to be worse; not shown to be better.** +0.071 fact
  survival, 95% CI [-0.036, +0.189]; all facts kept +0.099, CI [-0.151, +0.361]. The gain is entirely `ledger`
  (0.605 to 0.997); `deploy` is unchanged; `triage` still loses facts (0.997 to 0.818).
- **T2 (oracle utilities, tiered above binary): not supported.** -0.007, CI [-0.035, +0.021]; binary retention is
  already at 0.979 with perfect utilities, so there is no room, and `triage` is again lower with summaries.
- The planner represents about twice as many episodes (8.9 vs 4.3) in the same budget, which is the intended effect,
  and every plan was optimal and within budget. Whether the extra, summarised episodes help or confuse a real model
  is exactly what this check cannot say.
- `triage` is the open weakness: its expected values include person names and queue names whose terms recur across
  episodes, so the information-per-token rule ranks them below rarer filler. A calibrated fidelity (discounting the
  summariser's own score by measured fact loss) or protecting fields that the task schema names would address it;
  neither is built, and the live harness has not been run on tiered plans.

## Live: three-level retention with calibrated fidelity (negative on the primary question)

Pre-registered in `benchmarks/dfl/PREREGISTRATION_LIVE_TIERED.md`; runner `dfl_bench/live/fewshot_run.py --tiered`;
results in `benchmarks/dfl/results/live_tiered/`. Same worlds, model, support/query split and 72 query tasks as the
few-shot run. Summary-tier episodes leave the retained store (the model sees their stub) and their extractive
summary enters the SDK model context through the existing `semantic_summaries` slot, with summary tokens counted
against the budget. Fidelity: conservative fidelity mapped through the isotonic calibration (seeds 4300-4302).
Cost: 375 new calls, 1.04M input tokens. One attempt failed first because the store requires fully retained
episodes to have full prerequisites; the planner default was changed to that rule (model-free recheck on seeds
4600-4602: fact survival +0.106 over binary, CI [+0.020, +0.204]).

| method | ledger | deploy | triage | mean | worst family | change vs static | negative transfer |
|---|---|---|---|---|---|---|---|
| adapt-listwise-static (current best) | 0.824 | 1.000 | 0.889 | **0.904** | 0.824 | +0.157 | 0.04 |
| tiered-adapt-listwise | 0.894 | 0.907 | 0.759 | 0.853 | 0.759 | +0.106 | 0.19 |
| tiered-static | 0.856 | 0.907 | 0.704 | 0.823 | 0.704 | +0.076 | 0.24 |
| static-pckp | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 | 0 | 0 |

- **G1 (guard, tiered-adapt not more than 0.05 below the current best): not met.** Difference -0.051, 95% CI
  [-0.113, +0.019]; the lower bound is below -0.05. **G2 (tiered-adapt above the current best): not supported**
  (negative point estimate, Holm p 0.63). **G3 (tiered-static above static): not supported** (+0.076, CI
  [-0.060, +0.224]). The worst-family floor is also missed (0.759 vs 0.824 -0.05 = 0.774).
- Per family: tiering helped `ledger` (static 0.435 to 0.856; adapted 0.824 to 0.894) and hurt `deploy`
  (1.000 to 0.907) and `triage` (0.889 to 0.759 for the adapted utilities). The current best stays the best method.
- **The model-free proxy overstated how usable summaries are.** Control with every episode replaced by its summary
  (budget ignored): live score vs fact-survival proxy was `ledger` 0.847 vs 1.00, `deploy` 0.750 vs 1.00,
  `triage` 0.319 vs 0.54. In `deploy` every expected value is present in the summaries and a real model still
  answers only 75% correctly, so presence of the fact is not enough; how the fact is presented matters. Likely
  contributors (not isolated by this run): the SDK prompt calls a semantic summary "derived, not evidence",
  `key=value` fragments lose the surrounding structure, and summaries of several episodes can be confused.
- Consequence for the calibration: it was fitted to fact survival, which is a necessary but not a sufficient
  condition. A calibration fitted to live usability (live score of a summary-only context per episode or per task,
  which the control above already starts to measure) is the logical next step. It would have to be fitted on
  worlds other than 4100-4102 before being tested again.
