# Generalization results: dev1 (seed 0 only; superseded by the keep_initial change)

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `415dde76eb85c1341d0c640c70fdaee5d1e55bed97ec896547e06a4691bb4ca3`. Seeds: [0]. Fold results: 9.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| dro-pairwise-v1 | 0.558 | 0.379 | 0.485 | 0.485 | 0.97 | 0.74 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| calibrated-pckp | 0.576 | 0.361 | 0.474 | 0.474 | 0.97 | 0.71 | {'optimal': 120} (max gap 0.0000) | 0.16 | 120 |
| static-pckp | 0.587 | 0.350 | 0.463 | 0.463 | 0.98 | 0.55 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| tuned-static-pckp | 0.575 | 0.362 | 0.463 | 0.463 | 0.98 | 0.55 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v1 | 0.527 | 0.410 | 0.461 | 0.461 | 0.95 | 0.67 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| combined-validation | 0.530 | 0.408 | 0.449 | 0.449 | 0.97 | 0.78 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v2 | 0.573 | 0.364 | 0.449 | 0.449 | 0.97 | 0.70 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-v2 | 0.528 | 0.409 | 0.432 | 0.432 | 0.98 | 0.55 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| dro-listwise-v2 | 0.495 | 0.442 | 0.430 | 0.430 | 0.95 | 0.63 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| linear-dfl | 0.520 | 0.417 | 0.430 | 0.430 | 0.95 | 0.72 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| pairwise-v1 | 0.552 | 0.385 | 0.430 | 0.430 | 0.97 | 0.65 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1 | 0.523 | 0.414 | 0.430 | 0.430 | 0.96 | 0.60 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1-unnormalized | 0.523 | 0.414 | 0.430 | 0.430 | 0.96 | 0.60 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1+boundary | 0.524 | 0.413 | 0.424 | 0.424 | 0.96 | 0.63 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-v1 | 0.513 | 0.424 | 0.423 | 0.423 | 0.98 | 0.57 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v2 | 0.524 | 0.413 | 0.418 | 0.418 | 0.95 | 0.67 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v1+boundary | 0.516 | 0.421 | 0.370 | 0.370 | 0.95 | 0.66 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-pckp | 0.428 | 0.510 | 0.288 | 0.288 | 0.99 | 0.59 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.064 | [-0.126, -0.004] | 0.0375 | 0.7123 | 30 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.053 | [-0.119, +0.009] | 0.1000 | 1.0000 | 30 |
| regret-pairwise-v1 vs linear-dfl | +0.003 | [-0.055, +0.062] | 0.9213 | 1.0000 | 30 |
| regret-pairwise-v1 vs regression-pckp | +0.095 | [+0.039, +0.150] | 0.0010 | 0.0270 | 30 |
| listwise-v1 vs static-pckp | -0.060 | [-0.128, +0.009] | 0.0795 | 1.0000 | 30 |
| listwise-v1 vs tuned-static-pckp | -0.048 | [-0.118, +0.022] | 0.1730 | 1.0000 | 30 |
| listwise-v1 vs linear-dfl | +0.007 | [-0.052, +0.069] | 0.8298 | 1.0000 | 30 |
| listwise-v1 vs regression-pckp | +0.099 | [+0.035, +0.165] | 0.0040 | 0.0920 | 30 |
| dro-pairwise-v1 vs static-pckp | -0.029 | [-0.078, +0.018] | 0.2424 | 1.0000 | 30 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.017 | [-0.071, +0.034] | 0.5364 | 1.0000 | 30 |
| dro-pairwise-v1 vs linear-dfl | +0.038 | [-0.015, +0.095] | 0.1750 | 1.0000 | 30 |
| dro-pairwise-v1 vs regression-pckp | +0.131 | [+0.073, +0.185] | 0.0005 | 0.0160 | 30 |
| regression-v1 vs static-pckp | -0.074 | [-0.116, -0.032] | 0.0010 | 0.0270 | 30 |
| regression-v1 vs tuned-static-pckp | -0.063 | [-0.104, -0.022] | 0.0045 | 0.0990 | 30 |
| regression-v1 vs linear-dfl | -0.007 | [-0.064, +0.051] | 0.7948 | 1.0000 | 30 |
| regression-v1 vs regression-pckp | +0.085 | [+0.032, +0.142] | 0.0035 | 0.0840 | 30 |
| regression-v2 vs static-pckp | -0.059 | [-0.101, -0.021] | 0.0020 | 0.0500 | 30 |
| regression-v2 vs tuned-static-pckp | -0.047 | [-0.091, -0.009] | 0.0160 | 0.3359 | 30 |
| regression-v2 vs linear-dfl | +0.009 | [-0.052, +0.067] | 0.7773 | 1.0000 | 30 |
| regression-v2 vs regression-pckp | +0.101 | [+0.047, +0.157] | 0.0005 | 0.0160 | 30 |
| listwise-v2 vs static-pckp | -0.063 | [-0.122, -0.002] | 0.0435 | 0.7828 | 30 |
| listwise-v2 vs tuned-static-pckp | -0.051 | [-0.113, +0.012] | 0.1165 | 1.0000 | 30 |
| listwise-v2 vs linear-dfl | +0.004 | [-0.055, +0.065] | 0.8828 | 1.0000 | 30 |
| listwise-v2 vs regression-pckp | +0.097 | [+0.042, +0.155] | 0.0005 | 0.0160 | 30 |
| calibrated-pckp vs static-pckp | -0.011 | [-0.046, +0.028] | 0.5694 | 1.0000 | 30 |
| calibrated-pckp vs tuned-static-pckp | +0.001 | [-0.039, +0.041] | 0.9313 | 1.0000 | 30 |
| calibrated-pckp vs linear-dfl | +0.057 | [+0.006, +0.109] | 0.0300 | 0.5999 | 30 |
| calibrated-pckp vs regression-pckp | +0.149 | [+0.101, +0.198] | 0.0005 | 0.0160 | 30 |
| combined-validation vs static-pckp | -0.058 | [-0.110, -0.001] | 0.0470 | 0.7988 | 30 |
| combined-validation vs tuned-static-pckp | -0.046 | [-0.103, +0.013] | 0.1315 | 1.0000 | 30 |
| combined-validation vs linear-dfl | +0.010 | [-0.022, +0.043] | 0.5609 | 1.0000 | 30 |
| combined-validation vs regression-pckp | +0.102 | [+0.049, +0.153] | 0.0005 | 0.0160 | 30 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| calibrated-pckp | 0.662 | 0.623 | 0.584 | 0.518 | 0.474 | 0.597 |
| combined-validation | 0.597 | 0.546 | 0.461 | 0.449 | 0.508 | 0.616 |
| dro-listwise-v2 | 0.471 | 0.504 | 0.501 | 0.430 | 0.462 | 0.604 |
| dro-pairwise-v1 | 0.597 | 0.573 | 0.519 | 0.485 | 0.543 | 0.632 |
| linear-dfl | 0.624 | 0.535 | 0.454 | 0.430 | 0.571 | 0.506 |
| listwise-v1 | 0.471 | 0.546 | 0.461 | 0.608 | 0.473 | 0.604 |
| listwise-v1+boundary | 0.471 | 0.530 | 0.516 | 0.608 | 0.370 | 0.604 |
| listwise-v2 | 0.471 | 0.620 | 0.494 | 0.539 | 0.418 | 0.604 |
| pairwise-v1 | 0.647 | 0.590 | 0.519 | 0.430 | 0.522 | 0.604 |
| regression-pckp | 0.471 | 0.459 | 0.513 | 0.288 | 0.378 | 0.456 |
| regression-v1 | 0.632 | 0.423 | 0.533 | 0.483 | 0.504 | 0.503 |
| regression-v2 | 0.697 | 0.509 | 0.554 | 0.432 | 0.432 | 0.547 |
| regret-pairwise-v1 | 0.471 | 0.591 | 0.519 | 0.430 | 0.522 | 0.604 |
| regret-pairwise-v1+boundary | 0.471 | 0.591 | 0.519 | 0.424 | 0.534 | 0.604 |
| regret-pairwise-v1-unnormalized | 0.471 | 0.591 | 0.519 | 0.430 | 0.522 | 0.604 |
| regret-pairwise-v2 | 0.662 | 0.609 | 0.595 | 0.449 | 0.508 | 0.616 |
| static-pckp | 0.720 | 0.557 | 0.639 | 0.553 | 0.463 | 0.591 |
| tuned-static-pckp | 0.670 | 0.557 | 0.639 | 0.553 | 0.463 | 0.571 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| linear-dfl | 0.572 | 0.378 | 0.572 | 0.572 | 0.99 | 0.67 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| calibrated-pckp | 0.561 | 0.389 | 0.561 | 0.561 | 0.99 | 0.58 | {'optimal': 40} (max gap 0.0000) | 0.00 | 40 |
| regression-v1 | 0.561 | 0.389 | 0.561 | 0.561 | 0.99 | 0.57 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| combined-validation | 0.555 | 0.395 | 0.555 | 0.555 | 0.99 | 0.54 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| static-pckp | 0.555 | 0.395 | 0.555 | 0.555 | 0.99 | 0.54 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| tuned-static-pckp | 0.555 | 0.395 | 0.555 | 0.555 | 0.99 | 0.54 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| dro-listwise-v2 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| dro-pairwise-v1 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| listwise-v1 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| listwise-v1+boundary | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| listwise-v2 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| pairwise-v1 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regret-pairwise-v1 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regret-pairwise-v1+boundary | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regret-pairwise-v1-unnormalized | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regret-pairwise-v2 | 0.536 | 0.414 | 0.536 | 0.536 | 0.96 | 0.52 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regression-v2 | 0.522 | 0.428 | 0.522 | 0.522 | 0.99 | 0.46 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |
| regression-pckp | 0.450 | 0.500 | 0.450 | 0.450 | 0.99 | 0.63 | {'optimal': 40} (max gap 0.0000) | n/a | 40 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| regret-pairwise-v1 vs linear-dfl | -0.036 | [-0.159, +0.087] | 0.5694 | 1.0000 | 10 |
| regret-pairwise-v1 vs regression-pckp | +0.086 | [-0.039, +0.204] | 0.1685 | 1.0000 | 10 |
| listwise-v1 vs static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| listwise-v1 vs tuned-static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| listwise-v1 vs linear-dfl | -0.036 | [-0.159, +0.087] | 0.5694 | 1.0000 | 10 |
| listwise-v1 vs regression-pckp | +0.086 | [-0.039, +0.204] | 0.1685 | 1.0000 | 10 |
| dro-pairwise-v1 vs static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| dro-pairwise-v1 vs linear-dfl | -0.036 | [-0.159, +0.087] | 0.5694 | 1.0000 | 10 |
| dro-pairwise-v1 vs regression-pckp | +0.086 | [-0.039, +0.204] | 0.1685 | 1.0000 | 10 |
| regression-v1 vs static-pckp | +0.005 | [-0.068, +0.082] | 0.9068 | 1.0000 | 10 |
| regression-v1 vs tuned-static-pckp | +0.005 | [-0.068, +0.082] | 0.9068 | 1.0000 | 10 |
| regression-v1 vs linear-dfl | -0.011 | [-0.112, +0.070] | 0.8713 | 1.0000 | 10 |
| regression-v1 vs regression-pckp | +0.111 | [-0.016, +0.222] | 0.0830 | 1.0000 | 10 |
| regression-v2 vs static-pckp | -0.033 | [-0.141, +0.074] | 0.5159 | 1.0000 | 10 |
| regression-v2 vs tuned-static-pckp | -0.033 | [-0.141, +0.074] | 0.5159 | 1.0000 | 10 |
| regression-v2 vs linear-dfl | -0.050 | [-0.164, +0.056] | 0.3674 | 1.0000 | 10 |
| regression-v2 vs regression-pckp | +0.072 | [-0.042, +0.181] | 0.2189 | 1.0000 | 10 |
| listwise-v2 vs static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| listwise-v2 vs tuned-static-pckp | -0.019 | [-0.116, +0.081] | 0.6918 | 1.0000 | 10 |
| listwise-v2 vs linear-dfl | -0.036 | [-0.159, +0.087] | 0.5694 | 1.0000 | 10 |
| listwise-v2 vs regression-pckp | +0.086 | [-0.039, +0.204] | 0.1685 | 1.0000 | 10 |
| calibrated-pckp vs static-pckp | +0.005 | [-0.067, +0.079] | 0.8818 | 1.0000 | 10 |
| calibrated-pckp vs tuned-static-pckp | +0.005 | [-0.067, +0.079] | 0.8818 | 1.0000 | 10 |
| calibrated-pckp vs linear-dfl | -0.011 | [-0.107, +0.063] | 0.8773 | 1.0000 | 10 |
| calibrated-pckp vs regression-pckp | +0.111 | [-0.018, +0.224] | 0.0835 | 1.0000 | 10 |
| combined-validation vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 10 |
| combined-validation vs tuned-static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 10 |
| combined-validation vs linear-dfl | -0.017 | [-0.108, +0.069] | 0.7288 | 1.0000 | 10 |
| combined-validation vs regression-pckp | +0.105 | [-0.009, +0.207] | 0.0680 | 1.0000 | 10 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| linear-dfl | 0.361 | 0.382 | 0.361 | 0.361 | 0.95 | 0.53 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regret-pairwise-v2 | 0.353 | 0.390 | 0.353 | 0.353 | 0.96 | 0.59 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regret-pairwise-v1 | 0.350 | 0.393 | 0.350 | 0.350 | 0.95 | 0.55 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regression-v1 | 0.348 | 0.395 | 0.348 | 0.348 | 0.97 | 0.40 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regression-v2 | 0.345 | 0.398 | 0.345 | 0.345 | 0.97 | 0.42 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regret-pairwise-v1+boundary | 0.344 | 0.399 | 0.344 | 0.344 | 0.96 | 0.54 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| pairwise-v1 | 0.343 | 0.400 | 0.343 | 0.343 | 0.96 | 0.56 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| dro-pairwise-v1 | 0.339 | 0.404 | 0.339 | 0.339 | 0.96 | 0.57 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regret-pairwise-v1-unnormalized | 0.333 | 0.410 | 0.333 | 0.333 | 0.95 | 0.59 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| static-pckp | 0.331 | 0.412 | 0.331 | 0.331 | 0.96 | 0.38 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| tuned-static-pckp | 0.331 | 0.412 | 0.331 | 0.331 | 0.96 | 0.38 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| listwise-v1 | 0.318 | 0.425 | 0.318 | 0.318 | 0.95 | 0.65 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| calibrated-pckp | 0.315 | 0.427 | 0.315 | 0.315 | 0.96 | 0.52 | {'optimal': 113} (max gap 0.0000) | 0.34 | 113 |
| dro-listwise-v2 | 0.314 | 0.429 | 0.314 | 0.314 | 0.96 | 0.58 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| listwise-v2 | 0.297 | 0.445 | 0.297 | 0.297 | 0.96 | 0.58 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| combined-validation | 0.294 | 0.449 | 0.294 | 0.294 | 0.96 | 0.61 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| listwise-v1+boundary | 0.294 | 0.449 | 0.294 | 0.294 | 0.96 | 0.61 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |
| regression-pckp | 0.291 | 0.452 | 0.291 | 0.291 | 0.97 | 0.34 | {'optimal': 113} (max gap 0.0000) | n/a | 113 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | +0.019 | [-0.023, +0.060] | 0.3824 | 1.0000 | 30 |
| regret-pairwise-v1 vs tuned-static-pckp | +0.019 | [-0.023, +0.060] | 0.3824 | 1.0000 | 30 |
| regret-pairwise-v1 vs linear-dfl | -0.011 | [-0.030, +0.007] | 0.2369 | 1.0000 | 30 |
| regret-pairwise-v1 vs regression-pckp | +0.059 | [-0.004, +0.126] | 0.0725 | 1.0000 | 30 |
| listwise-v1 vs static-pckp | -0.013 | [-0.066, +0.036] | 0.6308 | 1.0000 | 30 |
| listwise-v1 vs tuned-static-pckp | -0.013 | [-0.066, +0.036] | 0.6308 | 1.0000 | 30 |
| listwise-v1 vs linear-dfl | -0.043 | [-0.090, +0.005] | 0.0780 | 1.0000 | 30 |
| listwise-v1 vs regression-pckp | +0.027 | [-0.027, +0.086] | 0.3324 | 1.0000 | 30 |
| dro-pairwise-v1 vs static-pckp | +0.008 | [-0.038, +0.054] | 0.7278 | 1.0000 | 30 |
| dro-pairwise-v1 vs tuned-static-pckp | +0.008 | [-0.038, +0.054] | 0.7278 | 1.0000 | 30 |
| dro-pairwise-v1 vs linear-dfl | -0.022 | [-0.062, +0.016] | 0.2824 | 1.0000 | 30 |
| dro-pairwise-v1 vs regression-pckp | +0.049 | [-0.009, +0.110] | 0.0965 | 1.0000 | 30 |
| regression-v1 vs static-pckp | +0.017 | [-0.034, +0.064] | 0.4924 | 1.0000 | 30 |
| regression-v1 vs tuned-static-pckp | +0.017 | [-0.034, +0.064] | 0.4924 | 1.0000 | 30 |
| regression-v1 vs linear-dfl | -0.013 | [-0.065, +0.036] | 0.6388 | 1.0000 | 30 |
| regression-v1 vs regression-pckp | +0.057 | [+0.008, +0.106] | 0.0265 | 0.7948 | 30 |
| regression-v2 vs static-pckp | +0.014 | [-0.043, +0.066] | 0.6153 | 1.0000 | 30 |
| regression-v2 vs tuned-static-pckp | +0.014 | [-0.043, +0.066] | 0.6153 | 1.0000 | 30 |
| regression-v2 vs linear-dfl | -0.016 | [-0.069, +0.037] | 0.5374 | 1.0000 | 30 |
| regression-v2 vs regression-pckp | +0.054 | [-0.001, +0.111] | 0.0575 | 1.0000 | 30 |
| listwise-v2 vs static-pckp | -0.034 | [-0.087, +0.018] | 0.2074 | 1.0000 | 30 |
| listwise-v2 vs tuned-static-pckp | -0.034 | [-0.087, +0.018] | 0.2074 | 1.0000 | 30 |
| listwise-v2 vs linear-dfl | -0.063 | [-0.114, -0.013] | 0.0165 | 0.5114 | 30 |
| listwise-v2 vs regression-pckp | +0.007 | [-0.057, +0.076] | 0.8738 | 1.0000 | 30 |
| calibrated-pckp vs static-pckp | -0.016 | [-0.049, +0.018] | 0.3519 | 1.0000 | 30 |
| calibrated-pckp vs tuned-static-pckp | -0.016 | [-0.049, +0.018] | 0.3519 | 1.0000 | 30 |
| calibrated-pckp vs linear-dfl | -0.046 | [-0.087, -0.004] | 0.0315 | 0.9133 | 30 |
| calibrated-pckp vs regression-pckp | +0.025 | [-0.043, +0.099] | 0.5109 | 1.0000 | 30 |
| combined-validation vs static-pckp | -0.037 | [-0.085, +0.005] | 0.0940 | 1.0000 | 30 |
| combined-validation vs tuned-static-pckp | -0.037 | [-0.085, +0.005] | 0.0940 | 1.0000 | 30 |
| combined-validation vs linear-dfl | -0.067 | [-0.109, -0.028] | 0.0005 | 0.0160 | 30 |
| combined-validation vs regression-pckp | +0.004 | [-0.051, +0.066] | 0.9503 | 1.0000 | 30 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| linear-dfl | 0.631 | 0.287 | 0.631 | 0.631 | 0.98 | 0.79 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| calibrated-pckp | 0.524 | 0.394 | 0.524 | 0.524 | 0.98 | 0.44 | {'optimal': 28} (max gap 0.0000) | 1.00 | 28 |
| combined-validation | 0.524 | 0.394 | 0.524 | 0.524 | 0.98 | 0.44 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| static-pckp | 0.524 | 0.394 | 0.524 | 0.524 | 0.98 | 0.44 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| tuned-static-pckp | 0.524 | 0.394 | 0.524 | 0.524 | 0.98 | 0.44 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regression-v1 | 0.500 | 0.417 | 0.500 | 0.500 | 0.98 | 0.47 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| dro-listwise-v2 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| dro-pairwise-v1 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| listwise-v1 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| listwise-v1+boundary | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| listwise-v2 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| pairwise-v1 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regret-pairwise-v1 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regret-pairwise-v1+boundary | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regret-pairwise-v1-unnormalized | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regret-pairwise-v2 | 0.483 | 0.434 | 0.483 | 0.483 | 0.93 | 0.53 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regression-pckp | 0.460 | 0.458 | 0.460 | 0.460 | 0.98 | 0.59 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |
| regression-v2 | 0.419 | 0.499 | 0.419 | 0.419 | 0.99 | 0.30 | {'optimal': 28} (max gap 0.0000) | n/a | 28 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.543
- dro-listwise-v2: 0.508
- dro-pairwise-v1: 0.602
- linear-dfl: 0.561
- listwise-v1: 0.514
- listwise-v1+boundary: 0.515
- listwise-v2: 0.506
- pairwise-v1: 0.574
- regression-pckp: 0.499
- regression-v1: 0.557
- regression-v2: 0.579
- regret-pairwise-v1: 0.556
- regret-pairwise-v1+boundary: 0.543
- regret-pairwise-v1-unnormalized: 0.556
- regret-pairwise-v2: 0.573
- static-pckp: 0.594

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- regression-v1: 0.303
- regression-v2: 0.310

## Degenerate (all-zero) trained models, share of folds

- combined-validation: 0.00
- dro-listwise-v2: 0.44
- dro-pairwise-v1: 0.22
- listwise-v1: 0.56
- listwise-v1+boundary: 0.56
- listwise-v2: 0.44
- pairwise-v1: 0.33
- regression-v1: 0.00
- regression-v2: 0.00
- regret-pairwise-v1: 0.44
- regret-pairwise-v1+boundary: 0.44
- regret-pairwise-v1-unnormalized: 0.44
- regret-pairwise-v2: 0.22

## Mean training seconds per fold (model-call cost: none, synthetic)

- calibrated-pckp: 0.0
- combined-validation: 7.0
- dro-listwise-v2: 10.6
- dro-pairwise-v1: 8.4
- linear-dfl: 4.9
- listwise-v1: 10.2
- listwise-v1+boundary: 10.2
- listwise-v2: 11.0
- pairwise-v1: 10.8
- regression-pckp: 0.2
- regression-v1: 10.6
- regression-v2: 13.2
- regret-pairwise-v1: 10.9
- regret-pairwise-v1+boundary: 11.5
- regret-pairwise-v1-unnormalized: 10.8
- regret-pairwise-v2: 10.0
- static-pckp: 0.0
- tuned-static-pckp: 142.7
