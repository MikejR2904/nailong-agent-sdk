# Pre-registration: three-level retention, model-free check (`dfl_bench.tiered`)

No model is called. The measure is **fact survival**: whether each value the live verifier expects
still appears in the retained representation (full episode text plus extractive summaries) at the task's
own token budget. It is a necessary condition for a correct answer, not answer quality: it cannot see
distractors, ordering or reasoning errors, and the summary fidelity it reports is the planner's own salience
measure (identifiers and rare terms), so it is partly self-referential. Real answer quality needs the live
harness, which has not been run for tiered retention.

Data: the live scenario worlds seeded 4100, 4101, 4102 (all three families, 6 variants x 6 instances each).
Utilities: the store's static utilities, and an oracle that rates fact-bearing episodes 1.0 and others 0.1
(an upper bound on what the planner can do with perfect utilities). Binary = the same planner with
summaries disabled.

## Hypotheses (paired by task, template-clustered bootstrap)
- **T1** (static utilities): tiered fact survival is not below binary (mean difference >= 0).
- **T2** (oracle utilities): tiered fact survival exceeds binary (95% interval excludes zero).
- **T3** (guarantees, tested offline): every plan respects the budget, prerequisites are represented,
  mandatory episodes are in full, summaries are grounded and keep every identifier, and the tiered
  optimum is not below the binary optimum (`tests/memory/test_tiered_retention.py`).
Reported either way: tokens used, episodes represented, full/summary split, expected fidelity.

## Amendment (before the confirmatory run)
The first run (seeds 4100-4102, with the original term-based summarizer; kept in `results/tiered_v1`) lost
facts in `deploy` and `triage` (fact survival 0.877 and 0.710 against 1.000 and 0.991 for binary retention):
expected values are short structured fields, the summarizer kept only identifiers and rare words, and the
planner trusted its fidelity score (about 0.9) enough to summarise almost every episode. T1 and T2 were not
supported by that run. The summarizer was then replaced by a structure-aware one (short scalar fields kept
as `key=value`, prose reduced to a word-boundary prefix plus its identifiers, fragments chosen by
information per token), developed after seeing those results. T1 and T2 are therefore re-tested on **fresh
worlds, seeds 4200-4202**; the 4100-4102 result for the new summarizer is reported but is not confirmatory.

## Amendment 2: calibrated fidelity (before the run below)
The planner now uses the conservative fidelity `min(information-weighted fidelity, share of short fields kept)`,
optionally mapped through an isotonic calibration (`FidelityCalibration`) fitted to the measured rate at which
each fact-bearing episode's expected value survives in its summary. Fitted on seeds 4300-4302
(`results/tiered_calibration/fidelity_calibration.json`, 1,401 episodes); held-out expected calibration error on
seeds 4401-4403 is 0.098 calibrated vs 0.164 uncalibrated. Variants evaluated: binary, tiered (conservative
fidelity, uncalibrated), tiered-calibrated. **Fresh worlds, seeds 4500-4502.**
- **C1**: tiered-calibrated fact survival is not below binary under static utilities (difference >= 0),
  and not worse than binary in any single family by more than 0.05.
- **C2**: tiered-calibrated represents more episodes than binary at no more than the same budget.
The earlier T1/T2 results stand as reported.

## Amendment 3: store-consistent prerequisites
The first live attempt failed because the store requires a fully retained episode to have its prerequisites
retained in full, while the planner allowed summarised prerequisites. The default is now
`full_requires_prerequisite_tier="full"`. The model-free check is repeated for this policy on **fresh worlds, seeds
4600-4602**, with the same calibration; the calibrated result of Amendment 2 (seeds 4500-4502) used the looser policy
and is reported as such.
