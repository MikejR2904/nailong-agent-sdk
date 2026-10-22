# 5. Experimental report

> **Evidence status: SYNTHETIC GROUND TRUTH ONLY.** Every number below comes from closed-form oracles
> that I wrote (`benchmarks/dfl/dfl_bench/oracle.py`). They test the solver and the learning pipeline
> against a known truth. They are **not** measured downstream agent performance. **Measured results,
> from real model calls scored by a deterministic verifier, are in `06-measured-evaluation.md`**; this
> report is the earlier, synthetic sanity check and the solver scale and latency measurements. Its §5.8
> lists what was missing when it was written; the measured report addresses the first item.

## 5.1 What was run

| Run | Command (from `benchmarks/dfl`, `PYTHONPATH=../../src:.`) | Seeds | Status |
|---|---|---|---|
| Exploratory | `python -m dfl_bench --replicates 3 --out results` | 100-102 | First look; read before further runs were chosen. Kept in `results/exploratory/`. |
| Confirmatory | `python -m dfl_bench --replicates 20 --base-seed 2000` | 2000-2019 | Sample size chosen after the exploratory run; instance-level statistics only. Kept in `results/confirmatory/`. |
| **Pre-registered holdout** | `python -m dfl_bench --regimes aligned shifted null additive --replicates 20 --base-seed 3000 --out results/holdout` | 3000-3019 | Seeds, hypotheses, test and correction fixed in `benchmarks/dfl/PREREGISTRATION.md` before the run. **The results reported here.** |
| Solver scale, latency | `python -m dfl_bench.perf scale`, `... latency` | 7, 11 | Descriptive, run on an otherwise idle machine. |

The holdout's SHA-256 of the pre-registration text is stored in `results/holdout/synthetic_results.json`.
No external timestamp exists for it (it was not committed before the run), so the hash only shows which
text the analysis used. The holdout differs from the earlier runs in two disclosed ways: the MLP is chosen
from a 4-point grid (previously one fixed configuration) and two control regimes were added.

## 5.2 Design

* **World.** Per replicate and regime, 14 templates x 6 instances (some dropped if the mandatory closure
  leaves under 15% of the budget); 7-13 episodes each, budget 45-65% of total tokens, half with one protected
  episode. Template-, graph- and task-disjoint splits: about 54 / 12 / 17-18 train / validation / test
  instances per replicate, 350-353 test instances per regime in total.
* **Regimes.**
  `aligned`: requirement episodes mostly contain the task's words; static relevance is informative.
  `shifted`: requirement episodes are preferentially failed, substrate-backed or heavily depended-on and
  decoys contain the task's words; static relevance is misleading *by construction*.
  `null`: requirement membership is independent of every observable feature (nothing to learn).
  `additive`: aligned generation but the oracle is exactly additive, with no redundant alternatives.
  In the first three the oracle is `0.03 + 0.92 * (fraction of requirements met)^2`, a requirement being met
  if any of its 1-2 alternatives is retained. In all regimes 15% of requirement members are chosen uniformly.
* **Candidates, preferences, training, baselines**: as in §3 of the design document and
  `benchmarks/dfl/README.md` (six candidate generators, two on-policy rounds, significance-filtered
  confidence-weighted pairs, linear model with `l2` chosen on validation, MLP with `hidden in {8,16}` and
  `l2 in {1e-3, 1e-2}` chosen on validation, set-level regression + PCKP, top-k with feasibility repair).
* **Evaluation.** Closed-form expected success `Q` on test instances, regret against the exhaustive optimum,
  40 simulated trials per selection. Every selection (candidate or test-time) is validated against budget,
  closure and mandatory retention; a violation raises.
* **Statistics.** Paired differences against static PCKP on the same instances. Because instances share
  templates, the primary test resamples whole templates (4,000 resamples) rather than instances.

## 5.3 Pre-registered primary hypotheses (holdout)

`H`: the learned method's expected success `Q` exceeds static PCKP's under matched budgets. Two-sided
template-clustered bootstrap p, Holm over the four cells, supported iff Holm p < 0.05 and the difference
is positive.

| Cell | Mean difference in Q | Clustered 95% CI | Holm p | Outcome |
|---|---|---|---|---|
| shifted / linear-dfl | +0.209 | [+0.173, +0.247] | 0.002 | **supported** |
| shifted / mlp-dfl | +0.197 | [+0.162, +0.233] | 0.002 | **supported** |
| aligned / linear-dfl | +0.054 | [+0.027, +0.082] | 0.002 | **supported** |
| aligned / mlp-dfl | +0.028 | [-0.008, +0.062] | 0.139 | **not supported** |

The smallest attainable clustered p-value with 4,000 resamples is about 0.0005, so "0.002" is the floor
after Holm, not a measured probability.

## 5.4 Results by regime (holdout)

Expected success `Q` (regret to the exhaustive optimum in parentheses). Full tables with intervals,
failure-critical recall, evidence coverage, token usage and solver certificates are in
`results/holdout/synthetic_tables.md`.

| Method | aligned | shifted | null | additive |
|---|---|---|---|---|
| static-pckp | 0.649 (0.238) | 0.526 (0.381) | 0.559 (0.330) | 0.674 (0.194) |
| greedy-pask | 0.627 | 0.540 | 0.551 | 0.657 |
| random-feasible | 0.470 | 0.520 | 0.457 | 0.529 |
| linear-dfl | **0.704** (0.184) | **0.735** (0.172) | 0.541 (0.348) | **0.714** (0.154) |
| mlp-dfl | 0.677 (0.210) | 0.723 (0.184) | 0.537 (0.352) | 0.670 (0.198) |
| regression-pckp | 0.673 | 0.728 | 0.527 | 0.696 |
| topk-repair | 0.680 | 0.715 | 0.474 | 0.703 |

Paired difference against static PCKP, template-clustered 95% CI (Holm p over the six comparisons):

| Method | aligned | shifted | null | additive |
|---|---|---|---|---|
| linear-dfl | +0.054 [+0.027, +0.082] (0.003) | +0.209 [+0.173, +0.247] (0.003) | -0.018 [-0.053, +0.017] (0.95) | +0.041 [+0.019, +0.063] (0.008) |
| mlp-dfl | +0.028 [-0.008, +0.062] (0.28) | +0.197 [+0.162, +0.233] (0.003) | -0.022 [-0.072, +0.023] (0.95) | -0.003 [-0.052, +0.039] (0.94) |
| regression-pckp | +0.024 [-0.009, +0.058] (0.28) | +0.202 [+0.165, +0.241] (0.003) | -0.032 [-0.066, +0.002] (0.25) | +0.022 [-0.004, +0.047] (0.23) |
| topk-repair | +0.031 [-0.000, +0.063] (0.21) | +0.190 [+0.152, +0.230] (0.003) | **-0.085** [-0.120, -0.046] (0.003) | +0.029 [+0.007, +0.053] (0.044) |

What each regime says:

* **shifted.** All four learned methods gain about 0.19-0.21 `Q` and cut regret roughly in half
  (0.381 -> 0.17-0.19). Preference accuracy on held-out pairs is 0.51 (chance) for the static scorer and
  0.75-0.76 for the learned ones. Because this regime was built so that the learned features carry the
  signal, this shows the pipeline *can* recover structure that exists; it is not evidence that real
  memories have such structure.
* **aligned.** Static weights already fit; the linear model still gains +0.054. The MLP's gain is not
  distinguishable from zero.
* **null (false-positive control).** With nothing to learn, no learned method beats static; point estimates
  are slightly negative but their clustered intervals include zero (regression + PCKP is borderline at
  -0.032 [-0.066, +0.002]). The pipeline does not manufacture gains. Top-k with repair, which ignores
  cost, is significantly *worse* than static here (-0.085). Held-out pair accuracy is 0.58-0.61 for every
  scorer, static included, so about 0.6 is what structure unrelated to requirement membership (such as how
  many episodes fit) provides, not learned signal.
* **additive (exactly additive oracle).** Linear Ranking-DFL gains +0.041 and has the lowest regret
  (0.154), but regret is far from zero. The limit here is the *information in the features*, not additivity:
  requirement membership is only partly visible through lexical relevance and 15% of members are random.
  The MLP does not beat static here (-0.003).

**Among learned methods** (Holm over 4 pairs per regime): the only difference that survives is top-k's
deficit in `null` (linear - top-k +0.067, Holm 0.002). No other pair, including linear vs MLP, linear vs
regression + PCKP and linear vs top-k, is significant after correction. The sign of linear - top-k is
positive in all four regimes (+0.023, +0.020, +0.067, +0.011; raw clustered p 0.022, 0.018, <0.001, 0.22),
which is suggestive of a small benefit from exact optimisation of the learned objective but not established.
The confirmatory run had top-k ahead of linear in `aligned` (0.680 vs 0.666); the holdout has it behind
(0.680 vs 0.704). The two runs disagree, so I draw no conclusion about exact versus greedy optimisation
from them.

## 5.5 Ablations (holdout; linear Ranking-DFL, one fit per replicate, 95% bootstrap interval over 20 replicates)

Difference in test `Q` from the full model.

| Removed group | aligned | shifted | null | additive |
|---|---|---|---|---|
| task | **-0.206** [-0.241, -0.167] | **-0.071** [-0.100, -0.042] | +0.003 [-0.015, +0.021] | **-0.177** [-0.206, -0.149] |
| graph | -0.010 [-0.025, +0.005] | -0.003 [-0.018, +0.013] | +0.014 [-0.008, +0.039] | +0.009 [-0.000, +0.020] |
| provenance | -0.014 [-0.029, +0.000] | +0.005 [-0.014, +0.024] | -0.010 [-0.029, +0.008] | +0.009 [-0.005, +0.023] |
| dynamics | +0.002 [-0.017, +0.018] | +0.001 [-0.014, +0.017] | +0.005 [-0.025, +0.032] | **+0.017** [+0.006, +0.029] |
| outcome | -0.006 [-0.021, +0.007] | -0.006 [-0.024, +0.011] | +0.010 [-0.002, +0.022] | +0.005 [-0.004, +0.015] |

| Training fraction | aligned | shifted | null | additive |
|---|---|---|---|---|
| 25% | **-0.044** [-0.077, -0.012] | **-0.062** [-0.089, -0.036] | **-0.042** [-0.071, -0.012] | **-0.039** [-0.063, -0.016] |
| 50% | **-0.029** [-0.046, -0.011] | -0.021 [-0.041, +0.001] | **-0.029** [-0.047, -0.011] | -0.014 [-0.032, +0.004] |

The task-relevance features carry nearly all of the signal wherever there is any. Removing graph,
provenance, dynamics or outcome features rarely changes `Q` beyond noise; removing the dynamics group
slightly *improves* the additive regime (+0.017), i.e. those features add noise there. More training data
helps monotonically in every regime (also the null one, where it presumably reduces overfitting to noise);
whether it keeps helping beyond the 100% point was not tested. The earlier confirmatory run's ablations,
reported without intervals, should be read in this light: differences below about 0.015 are not reliable.

## 5.6 Solver scale (descriptive)

General DAGs (actions depend on 1-3 exploratory episodes), 8 instances per size, default solver limits
(`exact_max_branch_nodes = 50,000`), learned coefficients equal to perturbed static utilities (the point is
the solver). From `results/perf/scale.md`:

| Episodes | Static PCKP | Learned PCKP |
|---|---|---|
| ~12 | all `optimal`, median 2.5 ms | all `optimal`, 2.3 ms |
| ~24 | all `optimal`, 22 ms | all `optimal`, 23 ms |
| ~48 | all `optimal`, 391 ms, up to 22,491 nodes | all `optimal`, 320 ms |
| ~96 | all `best-effort` (node cap), 2.3 s, relative gap up to 0.16 | all `best-effort`, 2.3 s, gap up to 0.36 |
| ~160 | all `best-effort`, 4.7 s, gap up to 0.12 | all `best-effort`, 4.7 s, gap up to 0.52 |

This is a real limit of the existing solver, not of the learned objective: on general DAGs of roughly
50-100 episodes, branch-and-bound with the default node cap stops before proving optimality, and the
certificate (`best-effort`, with its bound) says so. The relative gap is an upper bound on how far the
returned set is from the learned-objective optimum and is looser for the more varied learned coefficients.
Theorem 2's guarantee therefore holds in the stated `OPTIMAL` form only below about 50 episodes in this
benchmark's graph family; above it the `BEST_EFFORT` form (`n/s + g/s`) applies. The benchmark's
accuracy experiments use at most 13 episodes, so none of them exercised `BEST_EFFORT`; the unit test
`test_best_effort_status_is_preserved...` covers the code path. Raising `exact_max_branch_nodes` trades time
for proof; rooted-forest graphs use a tree DP and are not affected.

## 5.7 Latency (descriptive)

`compact()` wall time on 6 instances of about 12 episodes, 30 timed repeats each after warm-up, store
construction excluded, random model weights (cost only), idle machine (`results/perf/latency.md`):

| Method | Median ms | p10-p90 |
|---|---|---|
| static-pckp | 1.55 | 0.80-3.27 |
| greedy-pask | 2.48 | 0.94-3.09 |
| linear-dfl | 2.44 | 1.56-3.91 |
| mlp-dfl (16 hidden) | 2.00 | 1.79-2.43 |

The predictor alone (feature extraction plus scoring) takes a median 0.58 ms (linear) and 0.85 ms (MLP).
The spread between medians is within the p10-p90 ranges, so the ordering of linear and MLP here is noise;
the supported statement is that a learned decision costs about one extra millisecond at this size.
Training one model takes 0.8 s (linear) and about 4 s (MLP grid) per replicate; the whole pipeline takes
about 11 s per replicate. These times are for pure-Python code on one Windows machine.

## 5.8 Limitations and what is still missing

Addressed since the first version of this report: the post-hoc sample size (a pre-registered holdout
replaces it), instance-level statistics that ignored template clustering (the primary test is clustered),
the lack of controls (the `null` and `additive` regimes), the single fixed MLP configuration (now a
validation-selected grid; the MLP still does not beat the linear model), single-number ablations (now with
intervals), no paired comparison among learned methods (added, with Holm), unexercised `BEST_EFFORT` and
scale (§5.6), load-contaminated latency (§5.7) and unverified citations (verification status in
`04-literature-comparison.md`).

Not removable here:

* **At the time of this report there was no measured downstream result (see `06-measured-evaluation.md`).** The oracles, regimes and the choice of which features
  correlate with requirement membership are mine; another oracle could reverse the ordering. Training and
  test preferences use the same oracle, so held-out preference accuracy shows generalisation to unseen
  templates under one oracle, not agreement with real outcomes. The null regime shows the pipeline does not
  invent gains when nothing is learnable, which is necessary for trust but not sufficient for usefulness.
* The accuracy experiments use 7-13 episodes; behaviour on larger memories is known only for the solver.
* The pre-registration has no external timestamp (§5.1).
* The MLP is small and pure-Python; a larger network, other optimisers or more data could behave
  differently.

**To obtain real evidence** (not done): (1) choose tasks whose success a verifier can score; (2) run an
agent on each task with the context built from each candidate retention, under identical prompts, model and
seeds, and record each run with `dfl_bench.ledger.write_outcomes`; (3) serve the records through
`LedgerExecutor` (`evidence_kind = "measured"`, which refuses to invent a missing outcome) and let
`dfl_bench.preferences` decide ties; (4) pre-register the splits, the primary comparison (Ranking-DFL vs
static PCKP on task success at matched budgets), the clustered test and the stopping rule before looking at
test results, exactly as `PREREGISTRATION.md` does for the synthetic holdout. This costs model calls and
needs your go-ahead; I have not estimated its cost.

## 5.9 Reproducing

```bash
cd benchmarks/dfl
PYTHONPATH=../../src:. python -m dfl_bench --regimes aligned shifted null additive \
    --replicates 20 --base-seed 3000 --out results/holdout
PYTHONPATH=../../src:. python -m dfl_bench.perf scale --out results/perf
PYTHONPATH=../../src:. python -m dfl_bench.perf latency --out results/perf
```

Quality numbers are deterministic for a given seed (the confirmatory run was repeated and matched exactly);
latency and training-time numbers are not. The raw per-instance JSON is git-ignored and regenerated by the
command above.
