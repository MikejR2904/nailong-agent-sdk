# 5. Experimental report

> **Evidence status: SYNTHETIC GROUND TRUTH ONLY.** Every number below comes from a closed-form oracle
> that I wrote (`benchmarks/dfl/dfl_bench/oracle.py`). They test the solver and the learning pipeline
> against a known truth. They are **not** measured downstream agent performance. No real downstream
> experiment was run, no model API was called, and nothing here supports a claim about real agent
> success. §5.6 lists the remaining work.

## 5.1 What was run

| Run | Command (from `benchmarks/dfl`, `PYTHONPATH=../../src:.`) | Seeds | Role |
|---|---|---|---|
| Exploratory | `python -m dfl_bench --replicates 3 --out results` (moved to `results/exploratory/`) | 100-102 | First look. I read its results before deciding to run more. Its table predates a label fix (the training-cost caption said "all rounds"; it shows the final-round fit). |
| **Confirmatory** | `python -m dfl_bench --replicates 20 --base-seed 2000 --out results/confirmatory` | 2000-2019 | Fresh seeds, 20 independent worlds per regime. The numbers reported below. |

The confirmatory run was fixed after the exploratory run showed learned methods ahead but not
significant after Holm correction with only about 54 test instances. That is a post-hoc sample-size
decision; it is disclosed here rather than hidden, and the exploratory results are kept in the repository.

Environment: Python 3.13.1, Windows 11, pure-Python models (no numpy), git base `f9fee55` plus the
uncommitted working tree. Wall time is recorded in `synthetic_results.json`. A second small
template-clustered bootstrap is in `results/confirmatory/cluster_bootstrap.json`.

## 5.2 Design

* **World.** Per replicate, 14 templates x 6 instances per regime (some dropped if the mandatory closure
  leaves under 15% of the budget); each instance is a graph of 7-13 episodes (5-8 exploratory, 2-5 action),
  budget 45-65% of total tokens, 50% have one protected (mandatory) episode. Splits are template-, graph-
  and task-disjoint: about 53 / 12 / 18 train / validation / test instances per replicate.
* **Oracle.** `Q(S) = 0.03 + 0.92 * (fraction of requirements met)^2`, a requirement being met if *any* of
  its 1-2 alternative episodes is retained. Redundant and complementary by design, so no additive
  utility can be exact (Spec §2.5).
* **Regimes.** `aligned`: requirement episodes mostly contain the task's words, so the static lexical
  relevance is informative. `shifted`: requirement episodes are preferentially failed, substrate-backed
  or heavily depended-on, and *decoys* contain the task's words, so static relevance is misleading. The
  shifted regime was built to give learning something to find; it is a capability check, not a neutral world.
  15% of requirement members are chosen uniformly (label noise).
* **Candidates** (all validated feasible, deduplicated): static PCKP, greedy PASK, 6 random closed
  selections, 4 perturbed-utility PCKP solves, 4 targeted remove-and-substitute edits, and in round 2 the
  current linear and MLP policies. Training outcomes: 8 matched Bernoulli trials per candidate with
  common random numbers.
* **Preferences.** Two-proportion z test (level 0.05) and an effect of at least 0.15; weight `1 - p`;
  ties omitted; at most 60 pairs per instance.
* **Training.** Linear: Newton, `l2` in {1e-3, 1e-2, 1e-1, 1} chosen on validation pairs, `tau = 1`. MLP:
  8 tanh units, Adam, early stopping on validation loss. Two rounds (round 2 adds learned-policy
  candidates). Regression baseline: ridge regression of a set's mean outcome on the sum of its feature
  vectors (same data), then PCKP on the resulting coefficients. Top-k baseline: rank by the linear model's
  scores, add each episode with its prerequisites while it fits (no exact optimisation).
* **Evaluation.** 18 test instances per replicate, 40 simulated trials; `Q` is also computed in closed form;
  regret is against the exhaustive optimum over all feasible sets (instances have at most 13 episodes).

## 5.3 Results (confirmatory; 353 and 352 test instances per method)

Expected success `Q` with 95% bootstrap intervals over instances, and the paired difference against static
PCKP (same instances). Instance-level bootstrap and the sign test treat instances as independent; instances
share templates, so those intervals are somewhat optimistic. The template-clustered intervals in the last
column (59 clusters) are the conservative ones.

**Regime `aligned`** (static relevance is informative)

| Method | Q | Regret | Paired diff vs static | Clustered 95% CI | Holm p |
|---|---|---|---|---|---|
| static-pckp | 0.622 [0.590, 0.654] | 0.253 | - | - | - |
| greedy-pask | 0.587 [0.555, 0.620] | 0.287 | -0.034 | [-0.055, -0.013] | 0.011 |
| random-feasible | 0.478 [0.445, 0.511] | 0.396 | -0.143 | [-0.178, -0.108] | <0.001 |
| **linear-dfl** | 0.666 [0.635, 0.699] | 0.209 | **+0.044** | [+0.015, +0.075] | 0.025 |
| mlp-dfl | 0.637 [0.606, 0.667] | 0.238 | +0.015 | [-0.018, +0.047] | 0.569 |
| regression-pckp | 0.652 [0.620, 0.682] | 0.223 | +0.030 | [+0.004, +0.056] | 0.430 |
| topk-repair | 0.680 [0.651, 0.711] | 0.194 | +0.059 | [+0.027, +0.091] | 0.014 |

**Regime `shifted`** (static relevance is misleading by construction)

| Method | Q | Regret | Paired diff vs static | Clustered 95% CI | Holm p |
|---|---|---|---|---|---|
| static-pckp | 0.530 [0.497, 0.562] | 0.376 | - | - | - |
| greedy-pask | 0.519 [0.487, 0.551] | 0.386 | -0.011 | [-0.032, +0.010] | 1.0 |
| random-feasible | 0.516 [0.485, 0.550] | 0.389 | -0.014 | [-0.050, +0.024] | 1.0 |
| **linear-dfl** | 0.713 [0.682, 0.744] | 0.192 | **+0.183** | [+0.146, +0.218] | <0.001 |
| **mlp-dfl** | 0.693 [0.662, 0.723] | 0.213 | **+0.163** | [+0.124, +0.201] | <0.001 |
| regression-pckp | 0.687 [0.654, 0.717] | 0.219 | +0.157 | [+0.119, +0.194] | <0.001 |
| topk-repair | 0.694 [0.662, 0.725] | 0.211 | +0.164 | [+0.128, +0.199] | <0.001 |

Holm p-values are the sign-test p-values adjusted over the six comparisons in each regime (conservative
relative to the two comparisons that are the pre-stated hypothesis).

**Other metrics** (pooled across both regimes' tables in `results/confirmatory/synthetic_tables.md`):

* *Held-out pairwise preference accuracy* (about 5,100-5,400 significance-filtered test pairs per regime):
  aligned: static 0.720, linear 0.764, MLP 0.733, regression 0.740; shifted: static **0.509 (chance)**,
  linear 0.739, MLP 0.719, regression 0.732.
* *Evidence coverage* (fraction of requirements met) tracks `Q`: linear 0.798 vs static 0.763 (aligned),
  0.835 vs 0.685 (shifted).
* *Failure-critical recall:* shifted 0.804 (linear) vs 0.544 (static); aligned 0.690 vs 0.702, i.e. no change.
* *Token usage:* every method uses 92-96% of the budget; the learned methods do not buy their gains by
  spending more.
* *Solver:* all 705 static and 705 each of linear / MLP / regression solves were `optimal` with gap 0.
  `BEST_EFFORT` was **never triggered** in the benchmark (instances are small); it is covered only by the
  unit test with `exact_max_branch_nodes=1`.
* *Closure violations:* 0 (every candidate and every test-time selection is validated against budget, closure and mandatory retention; a violation raises).
* *Cost:* training 0.8 s per model (final fit), about 2.6 s per replicate over all rounds; candidate
  generation and outcome evaluation 2.4 s per replicate. Mean selection latency (ms, a loaded Windows machine,
  one measurement each, not a calibrated benchmark): static 1.4-1.5, linear 2.1, MLP 2.2, random 0.2.

## 5.4 Ablations (linear Ranking-DFL, mean test `Q`, one fit per replicate, no intervals)

| Removed group | aligned (full 0.667) | shifted (full 0.712) |
|---|---|---|
| task | 0.507 | 0.639 |
| graph | 0.662 | 0.693 |
| provenance | 0.672 | 0.715 |
| dynamics | 0.663 | 0.711 |
| outcome | 0.676 | 0.703 |

| Training fraction | aligned | shifted |
|---|---|---|
| 25% of train instances | 0.630 | 0.677 |
| 50% | 0.661 | 0.697 |
| 100% | 0.667 | 0.712 |

Task features carry most of the signal in both regimes, graph features add a little in shifted, and removing
provenance, dynamics or outcome features changes `Q` by less than 0.01 in aligned (removing `outcome`
slightly *raises* it). With no intervals these single-number differences below roughly 0.01-0.02 should not
be interpreted. In the exploratory run the training-size curve was non-monotone (3 replicates), which is
the kind of noise these small differences contain.

## 5.5 Interpretation

What the data support, in this synthetic setting only:

1. **The pipeline works as designed.** Solver certificates are optimal, every candidate and selection is
   feasible, mandatory episodes survive, and the learned objective is optimised exactly. This is
   separate from, and does not imply, good downstream performance (Spec §2 table).
2. **When static weights are mismatched to what matters (shifted), ranking-trained coefficients help a lot**
   (+0.18 `Q`, confidence interval far from zero, preference accuracy 0.51 -> 0.74), and the gain survives
   template-clustered resampling and Holm correction. Because this regime was designed with the learned
   features in mind, this shows the pipeline *can* recover structure that exists; it is not evidence that
   real agent memories have such structure.
3. **When static weights are already well aligned (aligned), the gain is small** (+0.04 for the linear model)
   and the MLP's gain is not distinguishable from zero. In that regime the nonlinear model did **not**
   improve memory-selection decisions over the linear one.
4. **The linear model is as good as or better than the MLP in both regimes** (descriptively; I did not run
   a paired test between them). The MLP is small, lightly tuned and early-stopped; this does not show that
   no larger nonlinear model could help.
5. **Exact optimisation of the learned objective is not clearly what drives the gain.** Top-k with
   feasibility repair using the same linear scores matched or exceeded exact PCKP in the aligned regime
   (0.680 vs 0.666) and trailed it slightly in shifted (0.694 vs 0.713); I did not run paired tests between
   these two. Learning the coefficients mattered more than how they were optimised here. Exact PCKP's
   distinct value is the guarantee and certificate (Theorems 1-2), not a measured accuracy edge.
6. **Ranking vs regression:** regression + PCKP reached similar `Q` (0.652 vs 0.666 aligned, 0.687 vs 0.713
   shifted). Ranking was nominally ahead in both; no paired test was run, so I make no claim that it is
   better.
7. **The additive model cannot be exact here** (the oracle is redundancy/complementarity-shaped); regret to
   the exhaustive optimum stays 0.19-0.24 for the best methods, consistent with Spec §2.5 and §2.10.

Hypothesis `E[Q(Ranking-DFL + PCKP)] > E[Q(Static PCKP)]` under matched budgets: **supported in the synthetic
shifted regime (linear and MLP), weakly supported in the synthetic aligned regime (linear only; MLP not),
and untested on real downstream performance.**

## 5.6 Limits and remaining work

* **No measured downstream result exists.** The oracle's structure, its two regimes, the label noise, and
  which features correlate with requirement membership are my choices. A different oracle could reverse
  the ordering. Results must not be quoted as agent performance.
* Both training and test preferences use the same oracle, so preference accuracy on test pairs measures
  generalisation to unseen templates under one oracle, not agreement with real outcomes.
* Instances are small (7-13 episodes), so exact solves are always proven and fast; `BEST_EFFORT` and scaling
  were not exercised by the benchmark.
* Hyperparameters beyond `l2` were not tuned; the MLP is tiny. Ablations are single fits without intervals.
* Latency numbers are wall-clock on a machine under load; they are indicative only.

**To obtain real evidence** (not done): (1) choose tasks whose success a verifier can score; (2) implement
`DownstreamExecutor` (`evidence_kind="measured"`) that runs an agent on the task with the context built from
each candidate retention, under identical prompts, model and seeds; (3) run each candidate several times and
let `dfl_bench.preferences` decide ties; (4) pre-register the splits, the primary comparison (Ranking-DFL
vs static PCKP on task success, matched budgets) and the stopping rule before looking at test results;
(5) report paired differences with template-clustered intervals as above. This costs model calls and was
not run; I did not estimate the cost, and no model API was used for any result in this document.

## 5.7 Reproducing

```bash
cd benchmarks/dfl
PYTHONPATH=../../src:. python -m dfl_bench --replicates 20 --base-seed 2000 --out results/confirmatory
```

Raw per-instance rows, configuration, commit and wall time are in `results/confirmatory/synthetic_results.json`;
tables in `synthetic_tables.md`.
