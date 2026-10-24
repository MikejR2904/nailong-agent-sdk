# Pre-registration 3: static-anchored advantage model with a conservative gate

Synthetic oracle only; no model calls. Earlier registrations and their results stand (no learned
method beat static; shrinkage reached parity).

## Method (chosen on development seeds 0-1, `results/generalize3_dev`)
A linear utility is trained so `U(S) - U(S0)` predicts `Q(S) - Q(S0)`, where `S0` is the exact static
PCKP selection (`generalize/advantage.py`). A bootstrap ensemble (8 models, resampled by template)
gives a mean advantage and spread for the learned PCKP selection `S_L`. `S_L` is returned only if
`mean - beta * spread > delta`; otherwise the static selection itself is returned. Ridge strength,
`beta` and `delta` are chosen by leave-one-family-out mean gain over static on the training families;
if no setting gains, the gate never opens. `advantage-v2-ungated` (gate always open) is reported
as the control. Features: schema v2.

Development outcome (for context, not evidence): `advantage-v2` +0.005 mean change vs static,
24% deviation rate, 5% negative transfer; ungated 66% deviation, 12% negative transfer.
The equal-cardinality pairwise diagnostic (`pairwise-v1-equalcard`) was no better than variable pools.

## Confirmatory run
Seeds **5000-5002**, `lofo:` folds only (6 per seed), plan `gate`, `--gate-v2-only`, 5 templates x 4
instances per family. Not run before this file was written.

## Hypotheses (Holm over two; paired by instance, template-clustered bootstrap)
- **R1**: `advantage-v2` has higher mean quality than `static-pckp`.
- **R2**: `advantage-v2` has higher mean quality than `tuned-static-pckp`.
Supported only if Holm p < 0.05 and the 95% interval excludes zero.
Reported descriptively: deviation rate, negative-transfer rate, worst-family change, change when
deviating, and the gate-versus-ungated difference. A gate that never opens counts as parity, not as an
improvement.
