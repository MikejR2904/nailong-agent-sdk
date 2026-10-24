# Generalization results: fewshot-dev

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `8166e8b72c856176685173866d9272b928346dbcade9ca412e09f284e4a9e855`. Seeds: [0, 1]. Fold results: 12.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-support | 0.585 | 0.345 | 0.520 | 0.527 | 0.98 | 0.61 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| tuned-static-pckp | 0.585 | 0.345 | 0.501 | 0.513 | 0.97 | 0.59 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| static-pckp | 0.573 | 0.357 | 0.463 | 0.493 | 0.98 | 0.57 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| adapt-regression-static | 0.587 | 0.343 | 0.445 | 0.484 | 0.97 | 0.56 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| adapt-listwise-source | 0.604 | 0.326 | 0.420 | 0.459 | 0.98 | 0.72 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| adapt-regression-source | 0.571 | 0.359 | 0.420 | 0.444 | 0.98 | 0.71 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |
| adapt-listwise-static | 0.580 | 0.350 | 0.351 | 0.399 | 0.97 | 0.63 | {'optimal': 192} (max gap 0.0000) | n/a | 192 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 120 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| adapt-regression-static vs static-pckp | +0.014 | [-0.015, +0.045] | 0.3574 | 1.0000 | 48 |
| adapt-regression-static vs tuned-static-pckp | +0.002 | [-0.035, +0.038] | 0.9078 | 1.0000 | 48 |
| adapt-regression-static vs tuned-static-support | +0.002 | [-0.039, +0.042] | 0.9458 | 1.0000 | 48 |
| adapt-listwise-static vs static-pckp | +0.007 | [-0.022, +0.034] | 0.6623 | 1.0000 | 48 |
| adapt-listwise-static vs tuned-static-pckp | -0.005 | [-0.046, +0.034] | 0.8148 | 1.0000 | 48 |
| adapt-listwise-static vs tuned-static-support | -0.005 | [-0.043, +0.031] | 0.7543 | 1.0000 | 48 |
| adapt-regression-source vs static-pckp | -0.002 | [-0.047, +0.044] | 0.9118 | 1.0000 | 48 |
| adapt-regression-source vs tuned-static-pckp | -0.014 | [-0.060, +0.032] | 0.5379 | 1.0000 | 48 |
| adapt-regression-source vs tuned-static-support | -0.014 | [-0.060, +0.032] | 0.5204 | 1.0000 | 48 |
| adapt-listwise-source vs static-pckp | +0.031 | [-0.016, +0.079] | 0.2134 | 1.0000 | 48 |
| adapt-listwise-source vs tuned-static-pckp | +0.019 | [-0.027, +0.065] | 0.4239 | 1.0000 | 48 |
| adapt-listwise-source vs tuned-static-support | +0.019 | [-0.025, +0.062] | 0.4174 | 1.0000 | 48 |

Change against static PCKP on the same instance:

| method | mean delta | worst-family delta | negative-transfer rate | deviation rate | delta when deviating | n |
|---|---|---|---|---|---|---|
| adapt-listwise-source | +0.031 | -0.088 | 0.20 | 0.88 | 0.035 | 192 |
| adapt-regression-static | +0.014 | -0.058 | 0.06 | 0.39 | 0.037 | 192 |
| tuned-static-support | +0.012 | -0.025 | 0.06 | 0.31 | 0.040 | 192 |
| tuned-static-pckp | +0.012 | -0.025 | 0.09 | 0.43 | 0.028 | 192 |
| adapt-listwise-static | +0.007 | -0.088 | 0.07 | 0.36 | 0.019 | 192 |
| adapt-regression-source | -0.002 | -0.081 | 0.22 | 0.86 | -0.002 | 192 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| adapt-listwise-source | 0.593 | 0.480 | 0.597 | 0.737 | 0.658 | 0.559 |
| adapt-listwise-static | 0.624 | 0.577 | 0.566 | 0.557 | 0.637 | 0.519 |
| adapt-regression-source | 0.600 | 0.495 | 0.485 | 0.628 | 0.658 | 0.559 |
| adapt-regression-static | 0.677 | 0.667 | 0.566 | 0.485 | 0.521 | 0.607 |
| static-pckp | 0.624 | 0.568 | 0.566 | 0.543 | 0.530 | 0.607 |
| tuned-static-pckp | 0.617 | 0.576 | 0.576 | 0.590 | 0.569 | 0.581 |
| tuned-static-support | 0.624 | 0.557 | 0.541 | 0.597 | 0.598 | 0.594 |

## Pairwise ranking accuracy on held-out candidate pools


## Set-utility calibration error (mean |U(S) - outcome|, regression methods)


## Degenerate (all-zero) trained models, share of folds

- adapt-listwise-source: 0.00
- adapt-listwise-static: 0.00
- adapt-regression-source: 0.00
- adapt-regression-static: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- adapt-listwise-source: 2.2
- adapt-listwise-static: 2.1
- adapt-regression-source: 2.3
- adapt-regression-static: 2.3
- static-pckp: 0.0
- tuned-static-pckp: 129.4
- tuned-static-support: 8.2
