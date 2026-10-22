# Pre-registration: Ranking-DFL holdout run (synthetic oracle)

Written 2026-10-21, before any run described here exists. Its SHA-256 is recorded in the results file.
No external timestamp exists unless the file is committed before the run; the hash only shows that the
text used for the analysis is the text below.

**Scope.** Everything here concerns the *synthetic* oracle. It cannot support a claim about real agent
performance (see `docs/dfl/05-experimental-report.md`).

## Why a new run

The earlier confirmatory run (seeds 2000-2019) fixed its sample size after an exploratory run was seen,
and its significance tests treated instances as independent although instances share templates. This
holdout run fixes the sample size, seeds, hypotheses and test in advance and uses a template-clustered
test.

## Design (fixed)

* Seeds 3000-3019, 20 independent replicates per regime. No seed is added, dropped or re-run after
  looking at results.
* Regimes: `aligned`, `shifted` (as before), plus two new controls: `null` (requirement membership is
  independent of every observable feature and of the task's words, so there is nothing to learn) and
  `additive` (aligned generation, but the oracle is exactly additive: `Q = 0.03 + 0.92 * fraction of
  required episodes retained`, no redundant alternatives).
* Everything else as in the confirmatory run: 14 templates x 6 instances, 8 training trials, 40
  evaluation trials, 2 on-policy rounds, preference policy (`alpha = 0.05`, `min_effect = 0.15`).
* One change from the earlier runs: the MLP is selected on validation preference accuracy from a grid
  `hidden_units in {8, 16}`, `l2 in {1e-3, 1e-2}` (previously one fixed configuration).

## Primary hypotheses

For each of the four (regime, model) cells, with `Q` the closed-form expected success on test instances
and the paired difference taken against static PCKP on the same instances:

* H1: `linear-dfl` > static, regime `shifted`
* H2: `mlp-dfl` > static, regime `shifted`
* H3: `linear-dfl` > static, regime `aligned`
* H4: `mlp-dfl` > static, regime `aligned`

**Test.** Two-sided template-clustered bootstrap p-value of the mean paired difference (resample whole
templates, 4,000 resamples, seed 0). Holm correction over the four hypotheses, family-wise level 0.05.
A hypothesis is *supported* iff its Holm-adjusted p < 0.05 and the mean difference is positive.
Otherwise it is *not supported*, whatever the point estimate.

## Secondary analyses (no claim of superiority beyond what is stated)

* `null`: the expected outcome is no improvement. A learned method with a positive mean difference and
  Holm-adjusted (over linear and MLP, family of 2) clustered p < 0.05 is reported as a false positive of
  the pipeline; a significantly negative difference is reported as overfitting cost.
* `additive`: regret to the exhaustive optimum for learned methods vs static, with clustered intervals.
* Paired comparisons among learned methods (linear vs MLP, linear vs regression+PCKP, linear vs top-k
  repair, MLP vs regression+PCKP), Holm over these 4 within each regime, clustered intervals. A
  difference is called real only if the clustered interval excludes 0 after that correction.
* Ablation effects with 95% intervals bootstrapped across replicates (paired difference from the full
  linear model).
* Solver scale check and a latency micro-benchmark (see `python -m dfl_bench.perf scale|latency`); both are
  descriptive.

## Known limits that this run does not remove

The oracle is my construction, so conclusions hold only for it. Training and test preferences share one
oracle. A measured downstream experiment is still required for any real claim.

## Deviations

Any change made after seeing holdout results will be listed here with its reason, and the original
analysis will still be reported.
