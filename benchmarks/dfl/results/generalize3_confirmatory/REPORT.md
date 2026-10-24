# Generalization results: confirmatory-3

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `8166e8b72c856176685173866d9272b928346dbcade9ca412e09f284e4a9e855`. Seeds: [5000, 5001, 5002]. Fold results: 18.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.609 | 0.304 | 0.475 | 0.493 | 0.98 | 0.66 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| pairwise-v1 | 0.571 | 0.341 | 0.444 | 0.487 | 0.98 | 0.73 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| static-pckp | 0.600 | 0.313 | 0.432 | 0.487 | 0.98 | 0.65 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regression-v1 | 0.562 | 0.350 | 0.440 | 0.475 | 0.98 | 0.72 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| combined-validation | 0.572 | 0.341 | 0.432 | 0.475 | 0.98 | 0.66 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| shrunk-regression-v2 | 0.555 | 0.357 | 0.432 | 0.471 | 0.97 | 0.57 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| advantage-v2 | 0.573 | 0.339 | 0.386 | 0.466 | 0.98 | 0.63 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| advantage-v2-ungated | 0.577 | 0.335 | 0.404 | 0.452 | 0.98 | 0.71 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| pairwise-v1-equalcard | 0.524 | 0.388 | 0.406 | 0.445 | 0.95 | 0.67 | {'optimal': 359, 'best-effort': 1} (max gap 2.0000) | n/a | 360 |
| linear-dfl | 0.517 | 0.395 | 0.294 | 0.400 | 0.97 | 0.67 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regression-pckp | 0.485 | 0.427 | 0.255 | 0.335 | 0.98 | 0.60 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 80 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.037 | [-0.066, -0.008] | 0.0105 | 0.0810 | 90 |
| regression-v1 vs tuned-static-pckp | -0.046 | [-0.074, -0.020] | 0.0010 | 0.0150 | 90 |
| regression-v1 vs linear-dfl | +0.045 | [+0.011, +0.079] | 0.0090 | 0.0810 | 90 |
| regression-v1 vs regression-pckp | +0.077 | [+0.037, +0.117] | 0.0005 | 0.0120 | 90 |
| combined-validation vs static-pckp | -0.028 | [-0.050, -0.007] | 0.0095 | 0.0810 | 90 |
| combined-validation vs tuned-static-pckp | -0.037 | [-0.060, -0.016] | 0.0005 | 0.0120 | 90 |
| combined-validation vs linear-dfl | +0.055 | [+0.021, +0.087] | 0.0015 | 0.0165 | 90 |
| combined-validation vs regression-pckp | +0.086 | [+0.044, +0.128] | 0.0005 | 0.0120 | 90 |
| shrunk-regression-v2 vs static-pckp | -0.045 | [-0.074, -0.017] | 0.0005 | 0.0120 | 90 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.054 | [-0.085, -0.025] | 0.0010 | 0.0150 | 90 |
| shrunk-regression-v2 vs linear-dfl | +0.038 | [+0.004, +0.072] | 0.0315 | 0.1575 | 90 |
| shrunk-regression-v2 vs regression-pckp | +0.070 | [+0.031, +0.108] | 0.0010 | 0.0150 | 90 |
| advantage-v2 vs static-pckp | -0.026 | [-0.045, -0.009] | 0.0030 | 0.0300 | 90 |
| advantage-v2 vs tuned-static-pckp | -0.036 | [-0.064, -0.009] | 0.0090 | 0.0810 | 90 |
| advantage-v2 vs linear-dfl | +0.056 | [+0.026, +0.087] | 0.0005 | 0.0120 | 90 |
| advantage-v2 vs regression-pckp | +0.088 | [+0.048, +0.128] | 0.0005 | 0.0120 | 90 |
| advantage-v2-ungated vs static-pckp | -0.023 | [-0.050, +0.004] | 0.1005 | 0.2009 | 90 |
| advantage-v2-ungated vs tuned-static-pckp | -0.032 | [-0.065, -0.000] | 0.0475 | 0.1900 | 90 |
| advantage-v2-ungated vs linear-dfl | +0.060 | [+0.029, +0.091] | 0.0010 | 0.0150 | 90 |
| advantage-v2-ungated vs regression-pckp | +0.091 | [+0.050, +0.133] | 0.0005 | 0.0120 | 90 |
| pairwise-v1-equalcard vs static-pckp | -0.075 | [-0.110, -0.040] | 0.0005 | 0.0120 | 90 |
| pairwise-v1-equalcard vs tuned-static-pckp | -0.085 | [-0.117, -0.050] | 0.0005 | 0.0120 | 90 |
| pairwise-v1-equalcard vs linear-dfl | +0.007 | [-0.028, +0.043] | 0.6903 | 0.6903 | 90 |
| pairwise-v1-equalcard vs regression-pckp | +0.039 | [-0.002, +0.078] | 0.0650 | 0.1950 | 90 |

Change against static PCKP on the same instance:

| method | mean delta | worst-family delta | negative-transfer rate | deviation rate | delta when deviating | n |
|---|---|---|---|---|---|---|
| tuned-static-pckp | +0.009 | -0.032 | 0.08 | 0.37 | 0.024 | 360 |
| advantage-v2-ungated | -0.023 | -0.098 | 0.19 | 0.63 | -0.036 | 360 |
| advantage-v2 | -0.026 | -0.100 | 0.10 | 0.28 | -0.096 | 360 |
| combined-validation | -0.028 | -0.085 | 0.12 | 0.47 | -0.059 | 360 |
| pairwise-v1 | -0.029 | -0.083 | 0.21 | 0.74 | -0.039 | 360 |
| regression-v1 | -0.037 | -0.097 | 0.26 | 0.86 | -0.043 | 360 |
| shrunk-regression-v2 | -0.045 | -0.109 | 0.16 | 0.44 | -0.102 | 360 |
| pairwise-v1-equalcard | -0.075 | -0.120 | 0.33 | 0.92 | -0.082 | 360 |
| linear-dfl | -0.083 | -0.188 | 0.32 | 0.86 | -0.096 | 360 |
| regression-pckp | -0.114 | -0.272 | 0.38 | 0.94 | -0.121 | 360 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| advantage-v2 | 0.636 | 0.626 | 0.605 | 0.530 | 0.568 | 0.474 |
| advantage-v2-ungated | 0.622 | 0.633 | 0.576 | 0.532 | 0.600 | 0.499 |
| combined-validation | 0.643 | 0.614 | 0.513 | 0.621 | 0.549 | 0.490 |
| linear-dfl | 0.523 | 0.525 | 0.582 | 0.442 | 0.510 | 0.521 |
| pairwise-v1 | 0.572 | 0.607 | 0.563 | 0.547 | 0.631 | 0.507 |
| pairwise-v1-equalcard | 0.533 | 0.571 | 0.496 | 0.562 | 0.507 | 0.475 |
| regression-pckp | 0.573 | 0.390 | 0.503 | 0.450 | 0.560 | 0.437 |
| regression-v1 | 0.563 | 0.592 | 0.501 | 0.661 | 0.549 | 0.508 |
| shrunk-regression-v2 | 0.641 | 0.555 | 0.579 | 0.520 | 0.559 | 0.476 |
| static-pckp | 0.654 | 0.662 | 0.598 | 0.629 | 0.566 | 0.490 |
| tuned-static-pckp | 0.646 | 0.664 | 0.566 | 0.677 | 0.584 | 0.515 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.518
- linear-dfl: 0.528
- pairwise-v1: 0.562
- pairwise-v1-equalcard: 0.508
- regression-pckp: 0.501
- regression-v1: 0.546
- shrunk-regression-v2: 0.574
- static-pckp: 0.591

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.367
- regression-v1: 0.318
- shrunk-regression-v2: 2.033

## Degenerate (all-zero) trained models, share of folds

- advantage-v2: 0.00
- advantage-v2-ungated: 0.00
- combined-validation: 0.00
- pairwise-v1: 0.00
- pairwise-v1-equalcard: 0.00
- regression-v1: 0.00
- shrunk-regression-v2: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- advantage-v2: 1037.4
- advantage-v2-ungated: 0.0
- combined-validation: 31.6
- linear-dfl: 4.2
- pairwise-v1: 9.7
- pairwise-v1-equalcard: 8.2
- regression-pckp: 0.3
- regression-v1: 13.1
- shrunk-regression-v2: 130.3
- static-pckp: 0.0
- tuned-static-pckp: 179.8
