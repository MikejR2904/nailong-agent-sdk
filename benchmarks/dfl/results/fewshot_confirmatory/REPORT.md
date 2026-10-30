# Generalization results: fewshot-confirmatory

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `f10df4ff385a3b7fd63c281bc96ad7fdd1237c190bb78b97b0d47258a43e53ee`. Seeds: [6000, 6001, 6002, 6003, 6004]. Fold results: 30.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| adapt-listwise-static | 0.622 | 0.307 | 0.470 | 0.517 | 0.97 | 0.68 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| adapt-listwise-source | 0.602 | 0.327 | 0.464 | 0.510 | 0.97 | 0.72 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| tuned-static-support | 0.598 | 0.331 | 0.438 | 0.506 | 0.97 | 0.64 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| adapt-regression-static | 0.618 | 0.311 | 0.438 | 0.495 | 0.97 | 0.62 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| static-pckp | 0.596 | 0.333 | 0.448 | 0.484 | 0.98 | 0.63 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| tuned-static-pckp | 0.570 | 0.359 | 0.403 | 0.461 | 0.97 | 0.61 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |
| adapt-regression-source | 0.568 | 0.361 | 0.421 | 0.448 | 0.98 | 0.64 | {'optimal': 480} (max gap 0.0000) | n/a | 480 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 120 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| adapt-regression-static vs static-pckp | +0.022 | [+0.000, +0.043] | 0.0490 | 0.3919 | 120 |
| adapt-regression-static vs tuned-static-pckp | +0.049 | [+0.025, +0.072] | 0.0010 | 0.0110 | 120 |
| adapt-regression-static vs tuned-static-support | +0.020 | [-0.005, +0.044] | 0.1220 | 0.4879 | 120 |
| adapt-listwise-static vs static-pckp | +0.026 | [+0.006, +0.046] | 0.0155 | 0.1550 | 120 |
| adapt-listwise-static vs tuned-static-pckp | +0.053 | [+0.030, +0.075] | 0.0005 | 0.0060 | 120 |
| adapt-listwise-static vs tuned-static-support | +0.024 | [-0.001, +0.049] | 0.0610 | 0.3989 | 120 |
| adapt-regression-source vs static-pckp | -0.028 | [-0.058, +0.001] | 0.0610 | 0.3989 | 120 |
| adapt-regression-source vs tuned-static-pckp | -0.002 | [-0.032, +0.027] | 0.8913 | 1.0000 | 120 |
| adapt-regression-source vs tuned-static-support | -0.031 | [-0.061, +0.001] | 0.0570 | 0.3989 | 120 |
| adapt-listwise-source vs static-pckp | +0.006 | [-0.023, +0.034] | 0.6983 | 1.0000 | 120 |
| adapt-listwise-source vs tuned-static-pckp | +0.032 | [+0.003, +0.061] | 0.0315 | 0.2834 | 120 |
| adapt-listwise-source vs tuned-static-support | +0.004 | [-0.028, +0.034] | 0.8368 | 1.0000 | 120 |

Change against static PCKP on the same instance:

| method | mean delta | worst-family delta | negative-transfer rate | deviation rate | delta when deviating | n |
|---|---|---|---|---|---|---|
| adapt-listwise-static | +0.026 | +0.000 | 0.07 | 0.43 | 0.060 | 480 |
| adapt-regression-static | +0.022 | -0.018 | 0.09 | 0.45 | 0.049 | 480 |
| adapt-listwise-source | +0.006 | -0.059 | 0.23 | 0.86 | 0.007 | 480 |
| tuned-static-support | +0.002 | -0.022 | 0.07 | 0.35 | 0.007 | 480 |
| tuned-static-pckp | -0.026 | -0.060 | 0.10 | 0.31 | -0.085 | 480 |
| adapt-regression-source | -0.028 | -0.097 | 0.28 | 0.88 | -0.033 | 480 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| adapt-listwise-source | 0.591 | 0.594 | 0.566 | 0.687 | 0.568 | 0.605 |
| adapt-listwise-static | 0.694 | 0.674 | 0.632 | 0.619 | 0.564 | 0.549 |
| adapt-regression-source | 0.556 | 0.528 | 0.542 | 0.640 | 0.591 | 0.548 |
| adapt-regression-static | 0.716 | 0.645 | 0.639 | 0.608 | 0.571 | 0.530 |
| static-pckp | 0.650 | 0.625 | 0.586 | 0.608 | 0.558 | 0.549 |
| tuned-static-pckp | 0.617 | 0.565 | 0.573 | 0.607 | 0.517 | 0.538 |
| tuned-static-support | 0.628 | 0.616 | 0.632 | 0.613 | 0.574 | 0.527 |

## Pairwise ranking accuracy on held-out candidate pools


## Set-utility calibration error (mean |U(S) - outcome|, regression methods)


## Degenerate (all-zero) trained models, share of folds

- adapt-listwise-source: 0.00
- adapt-listwise-static: 0.00
- adapt-regression-source: 0.00
- adapt-regression-static: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- adapt-listwise-source: 2.7
- adapt-listwise-static: 2.8
- adapt-regression-source: 2.8
- adapt-regression-static: 2.9
- static-pckp: 0.0
- tuned-static-pckp: 163.9
- tuned-static-support: 10.9
