# Generalization results: dev4

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `1924f7bec387c327d0c7c9c8ab50b9bdc581931c5e7a0c97e59b29ef2d92b15f`. Seeds: [0, 1]. Fold results: 20.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| advantage-v1-ungated | 0.589 | 0.343 | 0.534 | 0.544 | 0.97 | 0.69 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| tuned-static-pckp | 0.597 | 0.335 | 0.463 | 0.498 | 0.98 | 0.57 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| advantage-v2-ungated | 0.599 | 0.333 | 0.474 | 0.485 | 0.98 | 0.68 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| advantage-v1 | 0.595 | 0.337 | 0.468 | 0.476 | 0.98 | 0.63 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| pairwise-v1-equalcard | 0.543 | 0.390 | 0.458 | 0.459 | 0.96 | 0.64 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| advantage-v2 | 0.598 | 0.335 | 0.442 | 0.456 | 0.98 | 0.63 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-regression-v2 | 0.592 | 0.340 | 0.447 | 0.455 | 0.98 | 0.58 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| static-pckp | 0.592 | 0.340 | 0.447 | 0.455 | 0.98 | 0.58 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| pairwise-v1 | 0.557 | 0.376 | 0.394 | 0.412 | 0.98 | 0.64 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| linear-dfl | 0.512 | 0.421 | 0.379 | 0.404 | 0.96 | 0.65 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| combined-validation | 0.532 | 0.401 | 0.360 | 0.395 | 0.98 | 0.61 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-v1 | 0.529 | 0.403 | 0.360 | 0.391 | 0.98 | 0.59 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-pckp | 0.432 | 0.500 | 0.272 | 0.280 | 0.98 | 0.53 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 80 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.063 | [-0.107, -0.018] | 0.0050 | 0.0900 | 60 |
| regression-v1 vs tuned-static-pckp | -0.068 | [-0.107, -0.028] | 0.0005 | 0.0160 | 60 |
| regression-v1 vs linear-dfl | +0.018 | [-0.026, +0.065] | 0.4404 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.097 | [+0.054, +0.139] | 0.0005 | 0.0160 | 60 |
| combined-validation vs static-pckp | -0.061 | [-0.105, -0.016] | 0.0070 | 0.1120 | 60 |
| combined-validation vs tuned-static-pckp | -0.066 | [-0.110, -0.021] | 0.0065 | 0.1105 | 60 |
| combined-validation vs linear-dfl | +0.020 | [-0.017, +0.056] | 0.2834 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.099 | [+0.057, +0.142] | 0.0005 | 0.0160 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.005 | [-0.026, +0.017] | 0.6818 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.080 | [+0.037, +0.123] | 0.0005 | 0.0160 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.160 | [+0.109, +0.209] | 0.0005 | 0.0160 | 60 |
| advantage-v1 vs static-pckp | +0.003 | [-0.018, +0.025] | 0.7708 | 1.0000 | 60 |
| advantage-v1 vs tuned-static-pckp | -0.002 | [-0.028, +0.023] | 0.8938 | 1.0000 | 60 |
| advantage-v1 vs linear-dfl | +0.084 | [+0.037, +0.129] | 0.0010 | 0.0200 | 60 |
| advantage-v1 vs regression-pckp | +0.163 | [+0.111, +0.215] | 0.0005 | 0.0160 | 60 |
| advantage-v2 vs static-pckp | +0.005 | [-0.012, +0.024] | 0.5924 | 1.0000 | 60 |
| advantage-v2 vs tuned-static-pckp | +0.000 | [-0.027, +0.028] | 0.9648 | 1.0000 | 60 |
| advantage-v2 vs linear-dfl | +0.086 | [+0.044, +0.128] | 0.0005 | 0.0160 | 60 |
| advantage-v2 vs regression-pckp | +0.165 | [+0.117, +0.213] | 0.0005 | 0.0160 | 60 |
| advantage-v1-ungated vs static-pckp | -0.003 | [-0.037, +0.035] | 0.8738 | 1.0000 | 60 |
| advantage-v1-ungated vs tuned-static-pckp | -0.008 | [-0.040, +0.027] | 0.6573 | 1.0000 | 60 |
| advantage-v1-ungated vs linear-dfl | +0.078 | [+0.035, +0.120] | 0.0010 | 0.0200 | 60 |
| advantage-v1-ungated vs regression-pckp | +0.157 | [+0.108, +0.206] | 0.0005 | 0.0160 | 60 |
| advantage-v2-ungated vs static-pckp | +0.007 | [-0.020, +0.036] | 0.6553 | 1.0000 | 60 |
| advantage-v2-ungated vs tuned-static-pckp | +0.002 | [-0.028, +0.033] | 0.8868 | 1.0000 | 60 |
| advantage-v2-ungated vs linear-dfl | +0.087 | [+0.045, +0.129] | 0.0005 | 0.0160 | 60 |
| advantage-v2-ungated vs regression-pckp | +0.167 | [+0.119, +0.214] | 0.0005 | 0.0160 | 60 |
| pairwise-v1-equalcard vs static-pckp | -0.050 | [-0.094, -0.006] | 0.0330 | 0.4619 | 60 |
| pairwise-v1-equalcard vs tuned-static-pckp | -0.055 | [-0.098, -0.010] | 0.0160 | 0.2399 | 60 |
| pairwise-v1-equalcard vs linear-dfl | +0.031 | [-0.005, +0.068] | 0.0890 | 1.0000 | 60 |
| pairwise-v1-equalcard vs regression-pckp | +0.110 | [+0.066, +0.153] | 0.0005 | 0.0160 | 60 |

Change against static PCKP on the same instance:

| method | mean delta | worst-family delta | negative-transfer rate | deviation rate | delta when deviating | n |
|---|---|---|---|---|---|---|
| advantage-v2-ungated | +0.007 | -0.038 | 0.12 | 0.66 | 0.010 | 240 |
| advantage-v2 | +0.005 | -0.002 | 0.05 | 0.24 | 0.022 | 240 |
| tuned-static-pckp | +0.005 | -0.044 | 0.07 | 0.28 | 0.017 | 240 |
| advantage-v1 | +0.003 | -0.040 | 0.06 | 0.24 | 0.013 | 240 |
| shrunk-regression-v2 | +0.000 | +0.000 | 0.00 | 0.00 | n/a | 240 |
| advantage-v1-ungated | -0.003 | -0.069 | 0.17 | 0.75 | -0.004 | 240 |
| pairwise-v1 | -0.036 | -0.119 | 0.21 | 0.74 | -0.048 | 240 |
| pairwise-v1-equalcard | -0.050 | -0.184 | 0.27 | 0.85 | -0.058 | 240 |
| combined-validation | -0.061 | -0.213 | 0.26 | 0.77 | -0.079 | 240 |
| regression-v1 | -0.063 | -0.220 | 0.28 | 0.92 | -0.068 | 240 |
| linear-dfl | -0.080 | -0.176 | 0.31 | 0.86 | -0.093 | 240 |
| regression-pckp | -0.160 | -0.314 | 0.47 | 0.97 | -0.166 | 240 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| advantage-v1 | 0.684 | 0.596 | 0.653 | 0.561 | 0.476 | 0.603 |
| advantage-v1-ungated | 0.647 | 0.563 | 0.585 | 0.598 | 0.544 | 0.598 |
| advantage-v2 | 0.716 | 0.601 | 0.653 | 0.519 | 0.456 | 0.640 |
| advantage-v2-ungated | 0.678 | 0.628 | 0.619 | 0.534 | 0.485 | 0.653 |
| combined-validation | 0.503 | 0.566 | 0.541 | 0.412 | 0.550 | 0.618 |
| linear-dfl | 0.609 | 0.493 | 0.477 | 0.404 | 0.571 | 0.518 |
| pairwise-v1 | 0.624 | 0.582 | 0.534 | 0.412 | 0.529 | 0.660 |
| pairwise-v1-equalcard | 0.532 | 0.548 | 0.495 | 0.495 | 0.550 | 0.635 |
| regression-pckp | 0.402 | 0.366 | 0.483 | 0.363 | 0.470 | 0.510 |
| regression-v1 | 0.496 | 0.471 | 0.551 | 0.593 | 0.522 | 0.544 |
| shrunk-regression-v2 | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.642 |
| static-pckp | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.642 |
| tuned-static-pckp | 0.672 | 0.601 | 0.643 | 0.554 | 0.498 | 0.615 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| advantage-v2-ungated | 0.617 | 0.333 | 0.600 | 0.617 | 0.99 | 0.72 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| advantage-v2 | 0.610 | 0.340 | 0.563 | 0.610 | 0.99 | 0.65 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| advantage-v1-ungated | 0.604 | 0.346 | 0.544 | 0.604 | 0.99 | 0.70 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regression-v1 | 0.603 | 0.347 | 0.561 | 0.603 | 0.99 | 0.68 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| advantage-v1 | 0.596 | 0.354 | 0.555 | 0.596 | 0.99 | 0.59 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| combined-validation | 0.560 | 0.390 | 0.535 | 0.560 | 0.99 | 0.58 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| pairwise-v1-equalcard | 0.560 | 0.390 | 0.535 | 0.560 | 0.99 | 0.58 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| pairwise-v1 | 0.558 | 0.392 | 0.529 | 0.558 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| linear-dfl | 0.555 | 0.395 | 0.538 | 0.555 | 0.99 | 0.53 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-regression-v2 | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| tuned-static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regression-pckp | 0.502 | 0.448 | 0.450 | 0.502 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 80 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs tuned-static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs linear-dfl | +0.048 | [-0.038, +0.136] | 0.2844 | 1.0000 | 20 |
| regression-v1 vs regression-pckp | +0.101 | [+0.008, +0.194] | 0.0305 | 0.7013 | 20 |
| combined-validation vs static-pckp | +0.017 | [-0.035, +0.072] | 0.5434 | 1.0000 | 20 |
| combined-validation vs tuned-static-pckp | +0.017 | [-0.035, +0.072] | 0.5434 | 1.0000 | 20 |
| combined-validation vs linear-dfl | +0.006 | [-0.037, +0.043] | 0.7508 | 1.0000 | 20 |
| combined-validation vs regression-pckp | +0.059 | [-0.027, +0.141] | 0.1785 | 1.0000 | 20 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v2 vs linear-dfl | -0.011 | [-0.069, +0.045] | 0.7068 | 1.0000 | 20 |
| shrunk-regression-v2 vs regression-pckp | +0.042 | [-0.043, +0.129] | 0.3539 | 1.0000 | 20 |
| advantage-v1 vs static-pckp | +0.052 | [+0.013, +0.098] | 0.0065 | 0.1820 | 20 |
| advantage-v1 vs tuned-static-pckp | +0.052 | [+0.013, +0.098] | 0.0065 | 0.1820 | 20 |
| advantage-v1 vs linear-dfl | +0.041 | [-0.035, +0.118] | 0.2844 | 1.0000 | 20 |
| advantage-v1 vs regression-pckp | +0.094 | [+0.008, +0.185] | 0.0325 | 0.7148 | 20 |
| advantage-v2 vs static-pckp | +0.066 | [+0.023, +0.113] | 0.0005 | 0.0160 | 20 |
| advantage-v2 vs tuned-static-pckp | +0.066 | [+0.023, +0.113] | 0.0005 | 0.0160 | 20 |
| advantage-v2 vs linear-dfl | +0.055 | [-0.017, +0.131] | 0.1415 | 1.0000 | 20 |
| advantage-v2 vs regression-pckp | +0.108 | [+0.023, +0.197] | 0.0130 | 0.3249 | 20 |
| advantage-v1-ungated vs static-pckp | +0.060 | [-0.011, +0.127] | 0.0940 | 1.0000 | 20 |
| advantage-v1-ungated vs tuned-static-pckp | +0.060 | [-0.011, +0.127] | 0.0940 | 1.0000 | 20 |
| advantage-v1-ungated vs linear-dfl | +0.049 | [-0.038, +0.138] | 0.2904 | 1.0000 | 20 |
| advantage-v1-ungated vs regression-pckp | +0.102 | [+0.017, +0.193] | 0.0165 | 0.3959 | 20 |
| advantage-v2-ungated vs static-pckp | +0.073 | [+0.026, +0.117] | 0.0025 | 0.0750 | 20 |
| advantage-v2-ungated vs tuned-static-pckp | +0.073 | [+0.026, +0.117] | 0.0025 | 0.0750 | 20 |
| advantage-v2-ungated vs linear-dfl | +0.062 | [-0.003, +0.128] | 0.0625 | 1.0000 | 20 |
| advantage-v2-ungated vs regression-pckp | +0.115 | [+0.031, +0.198] | 0.0080 | 0.2079 | 20 |
| pairwise-v1-equalcard vs static-pckp | +0.017 | [-0.035, +0.072] | 0.5434 | 1.0000 | 20 |
| pairwise-v1-equalcard vs tuned-static-pckp | +0.017 | [-0.035, +0.072] | 0.5434 | 1.0000 | 20 |
| pairwise-v1-equalcard vs linear-dfl | +0.006 | [-0.037, +0.043] | 0.7508 | 1.0000 | 20 |
| pairwise-v1-equalcard vs regression-pckp | +0.059 | [-0.027, +0.141] | 0.1785 | 1.0000 | 20 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| advantage-v1-ungated | 0.360 | 0.377 | 0.342 | 0.360 | 0.96 | 0.52 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-v1 | 0.360 | 0.378 | 0.348 | 0.360 | 0.97 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| advantage-v2-ungated | 0.355 | 0.382 | 0.328 | 0.355 | 0.96 | 0.50 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| combined-validation | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-regression-v2 | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| static-pckp | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| pairwise-v1 | 0.354 | 0.383 | 0.343 | 0.354 | 0.96 | 0.54 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| linear-dfl | 0.352 | 0.385 | 0.343 | 0.352 | 0.95 | 0.47 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| tuned-static-pckp | 0.352 | 0.386 | 0.331 | 0.351 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| advantage-v1 | 0.350 | 0.387 | 0.331 | 0.350 | 0.96 | 0.44 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| advantage-v2 | 0.349 | 0.388 | 0.321 | 0.349 | 0.96 | 0.45 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| pairwise-v1-equalcard | 0.328 | 0.409 | 0.304 | 0.328 | 0.95 | 0.48 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-pckp | 0.291 | 0.446 | 0.291 | 0.291 | 0.97 | 0.35 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 80 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | +0.005 | [-0.031, +0.040] | 0.7788 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | +0.008 | [-0.028, +0.044] | 0.6693 | 1.0000 | 60 |
| regression-v1 vs linear-dfl | +0.008 | [-0.027, +0.042] | 0.6473 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.069 | [+0.028, +0.109] | 0.0010 | 0.0310 | 60 |
| combined-validation vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| combined-validation vs tuned-static-pckp | +0.003 | [-0.015, +0.022] | 0.7778 | 1.0000 | 60 |
| combined-validation vs linear-dfl | +0.003 | [-0.021, +0.028] | 0.8173 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.064 | [+0.017, +0.110] | 0.0085 | 0.2549 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.003 | [-0.015, +0.022] | 0.7778 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.003 | [-0.021, +0.028] | 0.8173 | 1.0000 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.064 | [+0.017, +0.110] | 0.0085 | 0.2549 | 60 |
| advantage-v1 vs static-pckp | -0.005 | [-0.024, +0.014] | 0.6108 | 1.0000 | 60 |
| advantage-v1 vs tuned-static-pckp | -0.002 | [-0.019, +0.017] | 0.8073 | 1.0000 | 60 |
| advantage-v1 vs linear-dfl | -0.002 | [-0.028, +0.023] | 0.8768 | 1.0000 | 60 |
| advantage-v1 vs regression-pckp | +0.059 | [+0.015, +0.103] | 0.0090 | 0.2549 | 60 |
| advantage-v2 vs static-pckp | -0.006 | [-0.020, +0.009] | 0.4049 | 1.0000 | 60 |
| advantage-v2 vs tuned-static-pckp | -0.002 | [-0.022, +0.017] | 0.7703 | 1.0000 | 60 |
| advantage-v2 vs linear-dfl | -0.003 | [-0.029, +0.023] | 0.8388 | 1.0000 | 60 |
| advantage-v2 vs regression-pckp | +0.058 | [+0.012, +0.103] | 0.0135 | 0.3509 | 60 |
| advantage-v1-ungated vs static-pckp | +0.005 | [-0.026, +0.035] | 0.7438 | 1.0000 | 60 |
| advantage-v1-ungated vs tuned-static-pckp | +0.008 | [-0.016, +0.033] | 0.5044 | 1.0000 | 60 |
| advantage-v1-ungated vs linear-dfl | +0.008 | [-0.023, +0.041] | 0.6178 | 1.0000 | 60 |
| advantage-v1-ungated vs regression-pckp | +0.069 | [+0.027, +0.112] | 0.0005 | 0.0160 | 60 |
| advantage-v2-ungated vs static-pckp | +0.000 | [-0.024, +0.026] | 0.9733 | 1.0000 | 60 |
| advantage-v2-ungated vs tuned-static-pckp | +0.004 | [-0.021, +0.030] | 0.8193 | 1.0000 | 60 |
| advantage-v2-ungated vs linear-dfl | +0.003 | [-0.027, +0.034] | 0.8168 | 1.0000 | 60 |
| advantage-v2-ungated vs regression-pckp | +0.064 | [+0.015, +0.114] | 0.0110 | 0.2969 | 60 |
| pairwise-v1-equalcard vs static-pckp | -0.027 | [-0.059, +0.005] | 0.1045 | 1.0000 | 60 |
| pairwise-v1-equalcard vs tuned-static-pckp | -0.023 | [-0.053, +0.007] | 0.1200 | 1.0000 | 60 |
| pairwise-v1-equalcard vs linear-dfl | -0.024 | [-0.054, +0.006] | 0.1195 | 1.0000 | 60 |
| pairwise-v1-equalcard vs regression-pckp | +0.037 | [-0.002, +0.079] | 0.0675 | 1.0000 | 60 |

## Unseen high budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.864 | 0.086 | 0.842 | 0.863 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| combined-validation | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-regression-v2 | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| static-pckp | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| pairwise-v1-equalcard | 0.858 | 0.092 | 0.846 | 0.858 | 0.96 | 0.96 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| advantage-v2 | 0.854 | 0.096 | 0.840 | 0.853 | 0.97 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| advantage-v1 | 0.854 | 0.096 | 0.842 | 0.853 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| advantage-v2-ungated | 0.844 | 0.106 | 0.819 | 0.843 | 0.97 | 0.95 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| advantage-v1-ungated | 0.844 | 0.106 | 0.830 | 0.843 | 0.97 | 0.96 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| pairwise-v1 | 0.838 | 0.112 | 0.813 | 0.837 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| linear-dfl | 0.832 | 0.118 | 0.823 | 0.831 | 0.97 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-v1 | 0.826 | 0.124 | 0.822 | 0.826 | 0.98 | 0.91 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-pckp | 0.792 | 0.158 | 0.771 | 0.792 | 0.98 | 0.87 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |

Paired comparisons, unseen high budget (template-clustered bootstrap, Holm over 80 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.033 | [-0.066, +0.000] | 0.0515 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | -0.038 | [-0.072, -0.002] | 0.0400 | 0.9998 | 60 |
| regression-v1 vs linear-dfl | -0.005 | [-0.041, +0.032] | 0.7733 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.035 | [-0.008, +0.074] | 0.1185 | 1.0000 | 60 |
| combined-validation vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| combined-validation vs tuned-static-pckp | -0.004 | [-0.016, +0.006] | 0.5749 | 1.0000 | 60 |
| combined-validation vs linear-dfl | +0.028 | [-0.007, +0.067] | 0.1260 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.068 | [+0.027, +0.108] | 0.0015 | 0.0465 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.004 | [-0.016, +0.006] | 0.5749 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.028 | [-0.007, +0.067] | 0.1260 | 1.0000 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.068 | [+0.027, +0.108] | 0.0015 | 0.0465 | 60 |
| advantage-v1 vs static-pckp | -0.006 | [-0.025, +0.008] | 0.4839 | 1.0000 | 60 |
| advantage-v1 vs tuned-static-pckp | -0.010 | [-0.028, +0.002] | 0.1475 | 1.0000 | 60 |
| advantage-v1 vs linear-dfl | +0.022 | [-0.017, +0.064] | 0.2794 | 1.0000 | 60 |
| advantage-v1 vs regression-pckp | +0.062 | [+0.021, +0.102] | 0.0040 | 0.1120 | 60 |
| advantage-v2 vs static-pckp | -0.006 | [-0.022, +0.010] | 0.4869 | 1.0000 | 60 |
| advantage-v2 vs tuned-static-pckp | -0.010 | [-0.028, +0.007] | 0.2804 | 1.0000 | 60 |
| advantage-v2 vs linear-dfl | +0.022 | [-0.011, +0.057] | 0.2029 | 1.0000 | 60 |
| advantage-v2 vs regression-pckp | +0.062 | [+0.023, +0.100] | 0.0020 | 0.0580 | 60 |
| advantage-v1-ungated vs static-pckp | -0.016 | [-0.041, +0.009] | 0.2244 | 1.0000 | 60 |
| advantage-v1-ungated vs tuned-static-pckp | -0.020 | [-0.044, +0.004] | 0.1120 | 1.0000 | 60 |
| advantage-v1-ungated vs linear-dfl | +0.012 | [-0.027, +0.054] | 0.5679 | 1.0000 | 60 |
| advantage-v1-ungated vs regression-pckp | +0.052 | [+0.012, +0.092] | 0.0100 | 0.2699 | 60 |
| advantage-v2-ungated vs static-pckp | -0.016 | [-0.040, +0.008] | 0.2069 | 1.0000 | 60 |
| advantage-v2-ungated vs tuned-static-pckp | -0.020 | [-0.044, +0.004] | 0.1115 | 1.0000 | 60 |
| advantage-v2-ungated vs linear-dfl | +0.013 | [-0.018, +0.044] | 0.4124 | 1.0000 | 60 |
| advantage-v2-ungated vs regression-pckp | +0.052 | [+0.013, +0.089] | 0.0105 | 0.2729 | 60 |
| pairwise-v1-equalcard vs static-pckp | -0.002 | [-0.036, +0.036] | 0.9468 | 1.0000 | 60 |
| pairwise-v1-equalcard vs tuned-static-pckp | -0.006 | [-0.039, +0.030] | 0.7833 | 1.0000 | 60 |
| pairwise-v1-equalcard vs linear-dfl | +0.027 | [-0.014, +0.070] | 0.1930 | 1.0000 | 60 |
| pairwise-v1-equalcard vs regression-pckp | +0.066 | [+0.029, +0.105] | 0.0005 | 0.0160 | 60 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| advantage-v1 | 0.636 | 0.278 | 0.613 | 0.636 | 0.98 | 0.72 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| advantage-v1-ungated | 0.619 | 0.295 | 0.591 | 0.619 | 0.98 | 0.72 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| advantage-v2-ungated | 0.612 | 0.303 | 0.554 | 0.612 | 0.98 | 0.74 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| linear-dfl | 0.607 | 0.307 | 0.583 | 0.607 | 0.97 | 0.65 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| advantage-v2 | 0.577 | 0.337 | 0.538 | 0.577 | 0.98 | 0.63 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-regression-v2 | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| combined-validation | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.45 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| tuned-static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.45 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| pairwise-v1 | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| pairwise-v1-equalcard | 0.532 | 0.383 | 0.508 | 0.532 | 0.95 | 0.55 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-v1 | 0.502 | 0.413 | 0.500 | 0.502 | 0.98 | 0.56 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-pckp | 0.472 | 0.442 | 0.460 | 0.472 | 0.98 | 0.64 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.537
- linear-dfl: 0.559
- pairwise-v1: 0.587
- pairwise-v1-equalcard: 0.573
- regression-pckp: 0.488
- regression-v1: 0.555
- shrunk-regression-v2: 0.593
- static-pckp: 0.593

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.302
- regression-v1: 0.324
- shrunk-regression-v2: 4.960

## Degenerate (all-zero) trained models, share of folds

- advantage-v1: 0.00
- advantage-v1-ungated: 0.00
- advantage-v2: 0.00
- advantage-v2-ungated: 0.00
- combined-validation: 0.00
- pairwise-v1: 0.00
- pairwise-v1-equalcard: 0.00
- regression-v1: 0.00
- shrunk-regression-v2: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- advantage-v1: 768.5
- advantage-v1-ungated: 0.0
- advantage-v2: 1101.2
- advantage-v2-ungated: 0.0
- combined-validation: 122.0
- linear-dfl: 5.2
- pairwise-v1: 8.0
- pairwise-v1-equalcard: 5.5
- regression-pckp: 0.2
- regression-v1: 9.8
- shrunk-regression-v2: 112.9
- static-pckp: 0.0
- tuned-static-pckp: 152.5
