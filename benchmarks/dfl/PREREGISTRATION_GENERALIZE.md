# Pre-registration: generalization protocol `dfl-generalize-1`

Evidence kind: **synthetic oracle only**. No model is called; outcomes are Bernoulli draws from the
closed-form quality model in `dfl_bench/oracle.py`. Nothing here can establish downstream agent
performance, and no result of this protocol may be quoted as a measured one.

This file is separate from `PREREGISTRATION.md` and `PREREGISTRATION_LIVE.md`, which are not changed.
The SHA-256 of this file is recorded in the confirmatory report. There is no external timestamp
and no commit ordering evidence beyond file modification times.

## Question

Which learning formulation (pairwise regret-weighted ranking, listwise ranking, set-level
regression, Group-DRO variants), which feature schema (v1 or v2) and which safeguard
(validation-chosen floor, learned/static mixing, shift fallback) gives the best and least
fragile retention decisions on task families it was not trained on, measured against a tuned static
baseline and the previously evaluated learned methods?

## Design

**Worlds.** Six structurally different families (`dfl_bench/generalize/worlds.py`): `aligned-short`,
`shifted-short`, `aligned-long` (13-17 explorations, 6-10 actions, up to 3 prerequisites),
`shifted-long`, `additive-deep`, `uninformative` (relevance carries no signal). 5 templates x 4
instances per family per seed. Budgets are 0.55 of total tokens unless stated.

**Folds per seed (10).**
- `lofo:<family>` (6): train and validate on the five other families, test on all instances of
  the held-out family. Primary.
- `holdout-long`: test on both long-history families, train on the rest.
- `budget-low`, `budget-high`: train on base-budget worlds, test on new templates of every family at
  0.30 and 0.90 of total tokens.
- `iid-template`: unseen templates inside the training families.

Split identity keys are template, query text and recorded history; leakage is asserted per fold.

**Seeds.** Development seeds 0 and 1 were used to build and debug the protocol and are not evidence.
Confirmatory seeds are **3000-3002** (reduced from five to bound compute), not run before this file was frozen. Development (seeds 0-1, `results/generalize_dev2`) showed no learned method above the tuned static baseline on `lofo:` and `combined-validation` below it; P1 is therefore expected to fail and is kept as registered.

**Methods** (every learned method selects through `LEARNED_PCKP`, so feasibility, closure and
certificates come from the exact solver): `static-pckp` (default weights), `tuned-static-pckp`
(random search of the five static weights on training and validation instances, default included),
`linear-dfl` and `regression-pckp` (the previously evaluated trainers), `pairwise-v1`,
`regret-pairwise-v1`, `regret-pairwise-v1-unnormalized`, `listwise-v1`, `regression-v1`,
`regression-v2`, `listwise-v2`, `regret-pairwise-v2`, `dro-pairwise-v1`, `dro-listwise-v2`, the
`+boundary` variants, `calibrated-pckp` and `combined-validation`. Hyperparameters (L2, quantisation
floor 0 or 1) are chosen by mean validation decision quality; test instances are never used. The
calibration mixing weight is chosen by inner leave-one-family-out over the training families,
maximising the worst held-out training family. The shift threshold is the 0.95 quantile of
validation-context scores. `combined-validation` is the method with the best validation quality among
all others (including both static baselines).

**Metric.** Oracle quality of the selected set, deterministic, with regret against the exact
optimum (`fast_optimum_quality`, verified equal to exhaustive enumeration in tests).

## Hypotheses

Confirmatory, Holm-corrected together (2 tests), paired by instance, template-clustered bootstrap
over the pooled `lofo:` test instances of all confirmatory seeds:

- **P1**: `combined-validation` has higher mean quality than `tuned-static-pckp`.
- **P2**: `combined-validation` has higher mean quality than `regression-pckp`.

Supported only if the Holm-adjusted p is below 0.05 and the 95% interval excludes zero.

Secondary, reported with Holm correction within the family of secondary comparisons, no claim of
confirmation:

- S1 `regret-pairwise-v1` vs `pairwise-v1`; and normalised vs unnormalised weights.
- S2 `listwise-v1` vs `regression-v1`; S2b `listwise-v1` vs `pairwise-v1`.
- S3 `dro-pairwise-v1` vs `pairwise-v1` on the worst held-out family.
- S4 v2 vs v1 features for regression and listwise.
- S5 `calibrated-pckp` vs its base learner on the worst held-out family, and its fallback rate.
- S6 boundary augmentation vs none for `listwise-v1` and `regret-pairwise-v1`.
- S7 each method vs `tuned-static-pckp` on `holdout-long`, `budget-low`, `budget-high`.
- S8 worst-family quality of every method (mean over seeds of the minimum over `lofo:` folds); the
  strongest outcome we would report is a higher worst-family quality than the tuned static baseline.

## Rules

- All results are reported, including methods that lose, degenerate (all-zero) models, and folds
  where fitting failed (`failures`).
- A comparison is called "supported" only under the rule above; otherwise "not supported".
- Changing a method after seeing confirmatory results is a deviation that must be listed with the
  report, and the changed method may not be used for a confirmatory claim.
- The development stage found that early stopping started from the untrained model could return it
  (all-zero weights, indistinguishable methods). `ObjectiveConfig.keep_initial` now defaults to
  `False`; the first development run (`results/generalize_dev1_superseded`) predates that change.

## Known limits (stated before running)

- Synthetic oracle with a quality that depends only on retained requirement coverage; real agents are
  not modelled. The synthetic default static weights may or may not suit this oracle; the tuned
  baseline is the fairer comparison.
- 20 instances per family per seed; three confirmatory seeds give about 360 test instances in the primary
  endpoint but only 18 family-by-seed worst-fold values.
- Family labels are the environments of Group-DRO; with six of them the convex hull of training
  shifts is small.
- Inner-LOFO uses the same worlds; the held-out test family is never touched.
- No unseen model backends, recorded real histories, or live measurements are included. Those claims
  remain unverified (see `docs/dfl/09-generalization-evaluation.md`).
