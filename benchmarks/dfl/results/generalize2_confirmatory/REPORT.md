# Generalization results: confirmatory-2

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `1924f7bec387c327d0c7c9c8ab50b9bdc581931c5e7a0c97e59b29ef2d92b15f`. Seeds: [4000, 4001, 4002]. Fold results: 30.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-dro-pairwise-v1 | 0.591 | 0.328 | 0.398 | 0.493 | 0.98 | 0.66 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| regression-v1 | 0.573 | 0.346 | 0.463 | 0.486 | 0.98 | 0.68 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| static-pckp | 0.604 | 0.316 | 0.398 | 0.485 | 0.98 | 0.63 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-listwise-v1 | 0.588 | 0.332 | 0.398 | 0.483 | 0.98 | 0.67 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-regret-pairwise-v1 | 0.591 | 0.328 | 0.398 | 0.480 | 0.98 | 0.65 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-listwise-v2 | 0.592 | 0.327 | 0.398 | 0.477 | 0.98 | 0.64 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-regression-v2 | 0.601 | 0.318 | 0.398 | 0.469 | 0.98 | 0.63 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| tuned-static-pckp | 0.604 | 0.315 | 0.390 | 0.467 | 0.97 | 0.62 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| combined-validation | 0.558 | 0.361 | 0.425 | 0.464 | 0.98 | 0.65 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-selected | 0.578 | 0.341 | 0.398 | 0.455 | 0.98 | 0.65 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| pairwise-v1 | 0.540 | 0.379 | 0.426 | 0.446 | 0.98 | 0.66 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| linear-dfl | 0.509 | 0.410 | 0.363 | 0.387 | 0.97 | 0.61 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| regression-pckp | 0.487 | 0.433 | 0.360 | 0.379 | 0.98 | 0.66 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |
| shrunk-regression-v1 | 0.575 | 0.344 | 0.333 | 0.379 | 0.97 | 0.61 | {'optimal': 359} (max gap 0.0000) | n/a | 359 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.031 | [-0.072, +0.010] | 0.1300 | 0.9548 | 90 |
| regression-v1 vs tuned-static-pckp | -0.031 | [-0.070, +0.007] | 0.1000 | 0.9548 | 90 |
| regression-v1 vs linear-dfl | +0.064 | [+0.022, +0.105] | 0.0035 | 0.0630 | 90 |
| regression-v1 vs regression-pckp | +0.087 | [+0.056, +0.119] | 0.0005 | 0.0180 | 90 |
| combined-validation vs static-pckp | -0.046 | [-0.078, -0.012] | 0.0060 | 0.0960 | 90 |
| combined-validation vs tuned-static-pckp | -0.047 | [-0.078, -0.016] | 0.0025 | 0.0500 | 90 |
| combined-validation vs linear-dfl | +0.049 | [+0.016, +0.081] | 0.0020 | 0.0420 | 90 |
| combined-validation vs regression-pckp | +0.071 | [+0.038, +0.108] | 0.0005 | 0.0180 | 90 |
| shrunk-regression-v1 vs static-pckp | -0.028 | [-0.052, -0.009] | 0.0040 | 0.0680 | 90 |
| shrunk-regression-v1 vs tuned-static-pckp | -0.029 | [-0.057, -0.003] | 0.0310 | 0.4339 | 90 |
| shrunk-regression-v1 vs linear-dfl | +0.066 | [+0.025, +0.109] | 0.0025 | 0.0500 | 90 |
| shrunk-regression-v1 vs regression-pckp | +0.089 | [+0.051, +0.128] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v1 vs static-pckp | -0.016 | [-0.034, +0.001] | 0.0675 | 0.8098 | 90 |
| shrunk-listwise-v1 vs tuned-static-pckp | -0.017 | [-0.043, +0.009] | 0.1945 | 1.0000 | 90 |
| shrunk-listwise-v1 vs linear-dfl | +0.078 | [+0.041, +0.117] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v1 vs regression-pckp | +0.101 | [+0.065, +0.139] | 0.0005 | 0.0180 | 90 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.012 | [-0.028, +0.002] | 0.0955 | 0.9548 | 90 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | -0.013 | [-0.037, +0.010] | 0.2739 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.082 | [+0.042, +0.123] | 0.0005 | 0.0180 | 90 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.105 | [+0.067, +0.143] | 0.0005 | 0.0180 | 90 |
| shrunk-dro-pairwise-v1 vs static-pckp | -0.013 | [-0.028, +0.002] | 0.0985 | 0.9548 | 90 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | -0.014 | [-0.040, +0.012] | 0.3024 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.082 | [+0.041, +0.124] | 0.0005 | 0.0180 | 90 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.104 | [+0.066, +0.144] | 0.0005 | 0.0180 | 90 |
| shrunk-regression-v2 vs static-pckp | -0.003 | [-0.014, +0.007] | 0.6188 | 1.0000 | 90 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.004 | [-0.025, +0.018] | 0.7463 | 1.0000 | 90 |
| shrunk-regression-v2 vs linear-dfl | +0.092 | [+0.050, +0.133] | 0.0005 | 0.0180 | 90 |
| shrunk-regression-v2 vs regression-pckp | +0.114 | [+0.077, +0.154] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v2 vs static-pckp | -0.011 | [-0.028, +0.001] | 0.0770 | 0.8468 | 90 |
| shrunk-listwise-v2 vs tuned-static-pckp | -0.012 | [-0.038, +0.012] | 0.3259 | 1.0000 | 90 |
| shrunk-listwise-v2 vs linear-dfl | +0.083 | [+0.045, +0.123] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v2 vs regression-pckp | +0.106 | [+0.068, +0.145] | 0.0005 | 0.0180 | 90 |
| shrunk-selected vs static-pckp | -0.026 | [-0.045, -0.006] | 0.0070 | 0.1050 | 90 |
| shrunk-selected vs tuned-static-pckp | -0.026 | [-0.054, -0.000] | 0.0480 | 0.6238 | 90 |
| shrunk-selected vs linear-dfl | +0.069 | [+0.029, +0.109] | 0.0005 | 0.0180 | 90 |
| shrunk-selected vs regression-pckp | +0.091 | [+0.054, +0.131] | 0.0005 | 0.0180 | 90 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| combined-validation | 0.560 | 0.652 | 0.548 | 0.527 | 0.552 | 0.509 |
| linear-dfl | 0.540 | 0.599 | 0.540 | 0.405 | 0.438 | 0.531 |
| pairwise-v1 | 0.540 | 0.600 | 0.532 | 0.460 | 0.544 | 0.564 |
| regression-pckp | 0.499 | 0.543 | 0.424 | 0.461 | 0.506 | 0.486 |
| regression-v1 | 0.560 | 0.627 | 0.527 | 0.595 | 0.571 | 0.556 |
| shrunk-dro-pairwise-v1 | 0.608 | 0.716 | 0.582 | 0.560 | 0.509 | 0.571 |
| shrunk-listwise-v1 | 0.619 | 0.716 | 0.581 | 0.535 | 0.511 | 0.564 |
| shrunk-listwise-v2 | 0.608 | 0.721 | 0.609 | 0.573 | 0.477 | 0.564 |
| shrunk-regression-v1 | 0.613 | 0.740 | 0.577 | 0.430 | 0.528 | 0.564 |
| shrunk-regression-v2 | 0.613 | 0.740 | 0.609 | 0.547 | 0.533 | 0.564 |
| shrunk-regret-pairwise-v1 | 0.608 | 0.716 | 0.573 | 0.572 | 0.516 | 0.564 |
| shrunk-selected | 0.613 | 0.700 | 0.582 | 0.495 | 0.509 | 0.571 |
| static-pckp | 0.613 | 0.740 | 0.609 | 0.564 | 0.533 | 0.564 |
| tuned-static-pckp | 0.620 | 0.743 | 0.602 | 0.566 | 0.565 | 0.531 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-listwise-v1 | 0.655 | 0.291 | 0.559 | 0.655 | 0.99 | 0.64 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-regression-v2 | 0.652 | 0.295 | 0.548 | 0.652 | 0.99 | 0.62 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| static-pckp | 0.652 | 0.295 | 0.548 | 0.652 | 0.99 | 0.62 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-listwise-v2 | 0.651 | 0.296 | 0.546 | 0.651 | 0.99 | 0.66 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-dro-pairwise-v1 | 0.645 | 0.301 | 0.539 | 0.645 | 0.99 | 0.65 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-pckp | 0.641 | 0.305 | 0.605 | 0.641 | 0.99 | 0.74 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-selected | 0.641 | 0.306 | 0.559 | 0.641 | 0.99 | 0.64 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| combined-validation | 0.637 | 0.309 | 0.549 | 0.637 | 0.99 | 0.62 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-regret-pairwise-v1 | 0.637 | 0.309 | 0.548 | 0.637 | 0.99 | 0.62 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-v1 | 0.636 | 0.311 | 0.549 | 0.636 | 0.99 | 0.74 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| shrunk-regression-v1 | 0.627 | 0.320 | 0.473 | 0.627 | 0.99 | 0.60 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| linear-dfl | 0.619 | 0.328 | 0.556 | 0.619 | 0.98 | 0.68 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| tuned-static-pckp | 0.615 | 0.331 | 0.532 | 0.615 | 0.99 | 0.60 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| pairwise-v1 | 0.605 | 0.342 | 0.502 | 0.605 | 0.99 | 0.72 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.016 | [-0.073, +0.047] | 0.6233 | 1.0000 | 30 |
| regression-v1 vs tuned-static-pckp | +0.021 | [-0.029, +0.073] | 0.4329 | 1.0000 | 30 |
| regression-v1 vs linear-dfl | +0.017 | [-0.040, +0.074] | 0.5659 | 1.0000 | 30 |
| regression-v1 vs regression-pckp | -0.005 | [-0.057, +0.049] | 0.8458 | 1.0000 | 30 |
| combined-validation vs static-pckp | -0.014 | [-0.063, +0.039] | 0.5764 | 1.0000 | 30 |
| combined-validation vs tuned-static-pckp | +0.022 | [-0.026, +0.071] | 0.3584 | 1.0000 | 30 |
| combined-validation vs linear-dfl | +0.019 | [-0.047, +0.086] | 0.5904 | 1.0000 | 30 |
| combined-validation vs regression-pckp | -0.004 | [-0.064, +0.058] | 0.8988 | 1.0000 | 30 |
| shrunk-regression-v1 vs static-pckp | -0.025 | [-0.056, +0.002] | 0.0710 | 1.0000 | 30 |
| shrunk-regression-v1 vs tuned-static-pckp | +0.011 | [-0.032, +0.053] | 0.6043 | 1.0000 | 30 |
| shrunk-regression-v1 vs linear-dfl | +0.008 | [-0.056, +0.070] | 0.8198 | 1.0000 | 30 |
| shrunk-regression-v1 vs regression-pckp | -0.015 | [-0.081, +0.049] | 0.6553 | 1.0000 | 30 |
| shrunk-listwise-v1 vs static-pckp | +0.003 | [-0.009, +0.018] | 0.6288 | 1.0000 | 30 |
| shrunk-listwise-v1 vs tuned-static-pckp | +0.040 | [-0.002, +0.083] | 0.0640 | 1.0000 | 30 |
| shrunk-listwise-v1 vs linear-dfl | +0.036 | [-0.019, +0.091] | 0.2099 | 1.0000 | 30 |
| shrunk-listwise-v1 vs regression-pckp | +0.014 | [-0.047, +0.073] | 0.6593 | 1.0000 | 30 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.014 | [-0.037, +0.008] | 0.1975 | 1.0000 | 30 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | +0.022 | [-0.018, +0.065] | 0.3164 | 1.0000 | 30 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.019 | [-0.038, +0.077] | 0.5574 | 1.0000 | 30 |
| shrunk-regret-pairwise-v1 vs regression-pckp | -0.004 | [-0.070, +0.060] | 0.8863 | 1.0000 | 30 |
| shrunk-dro-pairwise-v1 vs static-pckp | -0.006 | [-0.033, +0.019] | 0.6473 | 1.0000 | 30 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | +0.030 | [-0.011, +0.070] | 0.1440 | 1.0000 | 30 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.027 | [-0.030, +0.082] | 0.3644 | 1.0000 | 30 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.004 | [-0.062, +0.070] | 0.9068 | 1.0000 | 30 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 30 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.036 | [-0.007, +0.080] | 0.1020 | 1.0000 | 30 |
| shrunk-regression-v2 vs linear-dfl | +0.033 | [-0.024, +0.090] | 0.2809 | 1.0000 | 30 |
| shrunk-regression-v2 vs regression-pckp | +0.010 | [-0.056, +0.075] | 0.7688 | 1.0000 | 30 |
| shrunk-listwise-v2 vs static-pckp | -0.001 | [-0.018, +0.017] | 0.9493 | 1.0000 | 30 |
| shrunk-listwise-v2 vs tuned-static-pckp | +0.035 | [-0.012, +0.081] | 0.1380 | 1.0000 | 30 |
| shrunk-listwise-v2 vs linear-dfl | +0.032 | [-0.024, +0.088] | 0.2754 | 1.0000 | 30 |
| shrunk-listwise-v2 vs regression-pckp | +0.010 | [-0.056, +0.072] | 0.7748 | 1.0000 | 30 |
| shrunk-selected vs static-pckp | -0.011 | [-0.031, +0.010] | 0.2829 | 1.0000 | 30 |
| shrunk-selected vs tuned-static-pckp | +0.025 | [-0.016, +0.068] | 0.2469 | 1.0000 | 30 |
| shrunk-selected vs linear-dfl | +0.022 | [-0.032, +0.078] | 0.4514 | 1.0000 | 30 |
| shrunk-selected vs regression-pckp | -0.000 | [-0.058, +0.058] | 0.9683 | 1.0000 | 30 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-regression-v1 | 0.359 | 0.367 | 0.347 | 0.359 | 0.96 | 0.40 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-selected | 0.359 | 0.367 | 0.347 | 0.359 | 0.96 | 0.40 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-dro-pairwise-v1 | 0.358 | 0.368 | 0.347 | 0.359 | 0.96 | 0.40 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-listwise-v2 | 0.357 | 0.369 | 0.347 | 0.357 | 0.96 | 0.39 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-regression-v2 | 0.357 | 0.369 | 0.347 | 0.357 | 0.96 | 0.39 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| static-pckp | 0.357 | 0.369 | 0.347 | 0.357 | 0.96 | 0.39 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-regret-pairwise-v1 | 0.356 | 0.370 | 0.345 | 0.357 | 0.96 | 0.40 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| tuned-static-pckp | 0.349 | 0.377 | 0.345 | 0.349 | 0.96 | 0.41 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| shrunk-listwise-v1 | 0.349 | 0.377 | 0.323 | 0.349 | 0.96 | 0.41 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| combined-validation | 0.340 | 0.386 | 0.319 | 0.340 | 0.96 | 0.37 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| pairwise-v1 | 0.324 | 0.402 | 0.312 | 0.324 | 0.96 | 0.49 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| regression-v1 | 0.318 | 0.408 | 0.306 | 0.318 | 0.97 | 0.41 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| linear-dfl | 0.318 | 0.409 | 0.290 | 0.318 | 0.96 | 0.43 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |
| regression-pckp | 0.291 | 0.436 | 0.282 | 0.291 | 0.97 | 0.42 | {'optimal': 346} (max gap 0.0000) | n/a | 346 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.039 | [-0.068, -0.009] | 0.0100 | 0.2199 | 90 |
| regression-v1 vs tuned-static-pckp | -0.031 | [-0.058, -0.003] | 0.0265 | 0.5299 | 90 |
| regression-v1 vs linear-dfl | +0.000 | [-0.030, +0.033] | 0.9938 | 1.0000 | 90 |
| regression-v1 vs regression-pckp | +0.028 | [-0.002, +0.057] | 0.0650 | 1.0000 | 90 |
| combined-validation vs static-pckp | -0.017 | [-0.043, +0.009] | 0.2049 | 1.0000 | 90 |
| combined-validation vs tuned-static-pckp | -0.010 | [-0.027, +0.009] | 0.2934 | 1.0000 | 90 |
| combined-validation vs linear-dfl | +0.022 | [-0.008, +0.054] | 0.1695 | 1.0000 | 90 |
| combined-validation vs regression-pckp | +0.049 | [+0.017, +0.081] | 0.0010 | 0.0290 | 90 |
| shrunk-regression-v1 vs static-pckp | +0.002 | [-0.006, +0.009] | 0.6448 | 1.0000 | 90 |
| shrunk-regression-v1 vs tuned-static-pckp | +0.009 | [-0.013, +0.032] | 0.4124 | 1.0000 | 90 |
| shrunk-regression-v1 vs linear-dfl | +0.041 | [+0.012, +0.071] | 0.0045 | 0.1260 | 90 |
| shrunk-regression-v1 vs regression-pckp | +0.068 | [+0.036, +0.101] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v1 vs static-pckp | -0.008 | [-0.020, +0.004] | 0.1875 | 1.0000 | 90 |
| shrunk-listwise-v1 vs tuned-static-pckp | -0.000 | [-0.022, +0.021] | 0.9688 | 1.0000 | 90 |
| shrunk-listwise-v1 vs linear-dfl | +0.031 | [+0.005, +0.058] | 0.0230 | 0.4829 | 90 |
| shrunk-listwise-v1 vs regression-pckp | +0.059 | [+0.029, +0.090] | 0.0005 | 0.0180 | 90 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.001 | [-0.005, +0.004] | 0.6993 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | +0.007 | [-0.015, +0.029] | 0.5584 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.039 | [+0.009, +0.068] | 0.0085 | 0.2124 | 90 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.066 | [+0.035, +0.098] | 0.0005 | 0.0180 | 90 |
| shrunk-dro-pairwise-v1 vs static-pckp | +0.001 | [-0.002, +0.005] | 0.4794 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | +0.009 | [-0.014, +0.032] | 0.4409 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.041 | [+0.011, +0.071] | 0.0055 | 0.1430 | 90 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.068 | [+0.037, +0.100] | 0.0005 | 0.0180 | 90 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 90 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.008 | [-0.015, +0.030] | 0.5119 | 1.0000 | 90 |
| shrunk-regression-v2 vs linear-dfl | +0.040 | [+0.009, +0.070] | 0.0090 | 0.2159 | 90 |
| shrunk-regression-v2 vs regression-pckp | +0.067 | [+0.035, +0.099] | 0.0005 | 0.0180 | 90 |
| shrunk-listwise-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 90 |
| shrunk-listwise-v2 vs tuned-static-pckp | +0.008 | [-0.015, +0.030] | 0.5119 | 1.0000 | 90 |
| shrunk-listwise-v2 vs linear-dfl | +0.040 | [+0.009, +0.070] | 0.0090 | 0.2159 | 90 |
| shrunk-listwise-v2 vs regression-pckp | +0.067 | [+0.035, +0.099] | 0.0005 | 0.0180 | 90 |
| shrunk-selected vs static-pckp | +0.002 | [-0.006, +0.009] | 0.6448 | 1.0000 | 90 |
| shrunk-selected vs tuned-static-pckp | +0.009 | [-0.013, +0.032] | 0.4124 | 1.0000 | 90 |
| shrunk-selected vs linear-dfl | +0.041 | [+0.012, +0.071] | 0.0045 | 0.1260 | 90 |
| shrunk-selected vs regression-pckp | +0.068 | [+0.036, +0.101] | 0.0005 | 0.0180 | 90 |

## Unseen high budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-listwise-v1 | 0.863 | 0.087 | 0.840 | 0.863 | 0.97 | 0.89 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-regret-pairwise-v1 | 0.854 | 0.096 | 0.840 | 0.855 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-dro-pairwise-v1 | 0.854 | 0.096 | 0.840 | 0.854 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-regression-v1 | 0.852 | 0.098 | 0.840 | 0.853 | 0.97 | 0.86 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-selected | 0.852 | 0.098 | 0.840 | 0.853 | 0.97 | 0.86 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| pairwise-v1 | 0.852 | 0.098 | 0.836 | 0.852 | 0.97 | 0.94 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-listwise-v2 | 0.847 | 0.103 | 0.835 | 0.847 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| shrunk-regression-v2 | 0.847 | 0.103 | 0.835 | 0.847 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| static-pckp | 0.847 | 0.103 | 0.835 | 0.847 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| tuned-static-pckp | 0.846 | 0.104 | 0.829 | 0.846 | 0.97 | 0.87 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| combined-validation | 0.844 | 0.106 | 0.821 | 0.844 | 0.97 | 0.84 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| linear-dfl | 0.842 | 0.108 | 0.808 | 0.842 | 0.96 | 0.89 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| regression-v1 | 0.839 | 0.111 | 0.821 | 0.839 | 0.98 | 0.86 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |
| regression-pckp | 0.822 | 0.128 | 0.806 | 0.822 | 0.98 | 0.86 | {'optimal': 288} (max gap 0.0000) | n/a | 288 |

Paired comparisons, unseen high budget (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.008 | [-0.036, +0.021] | 0.5879 | 1.0000 | 90 |
| regression-v1 vs tuned-static-pckp | -0.007 | [-0.034, +0.022] | 0.6278 | 1.0000 | 90 |
| regression-v1 vs linear-dfl | -0.003 | [-0.029, +0.024] | 0.8058 | 1.0000 | 90 |
| regression-v1 vs regression-pckp | +0.017 | [-0.007, +0.041] | 0.1725 | 1.0000 | 90 |
| combined-validation vs static-pckp | -0.003 | [-0.020, +0.012] | 0.6648 | 1.0000 | 90 |
| combined-validation vs tuned-static-pckp | -0.002 | [-0.018, +0.014] | 0.7663 | 1.0000 | 90 |
| combined-validation vs linear-dfl | +0.002 | [-0.030, +0.033] | 0.9263 | 1.0000 | 90 |
| combined-validation vs regression-pckp | +0.021 | [-0.008, +0.049] | 0.1555 | 1.0000 | 90 |
| shrunk-regression-v1 vs static-pckp | +0.005 | [-0.003, +0.015] | 0.2404 | 1.0000 | 90 |
| shrunk-regression-v1 vs tuned-static-pckp | +0.006 | [-0.002, +0.016] | 0.1565 | 1.0000 | 90 |
| shrunk-regression-v1 vs linear-dfl | +0.010 | [-0.018, +0.039] | 0.4579 | 1.0000 | 90 |
| shrunk-regression-v1 vs regression-pckp | +0.030 | [-0.001, +0.060] | 0.0570 | 1.0000 | 90 |
| shrunk-listwise-v1 vs static-pckp | +0.016 | [+0.005, +0.028] | 0.0040 | 0.1440 | 90 |
| shrunk-listwise-v1 vs tuned-static-pckp | +0.017 | [+0.005, +0.032] | 0.0050 | 0.1750 | 90 |
| shrunk-listwise-v1 vs linear-dfl | +0.021 | [-0.008, +0.052] | 0.1550 | 1.0000 | 90 |
| shrunk-listwise-v1 vs regression-pckp | +0.041 | [+0.011, +0.070] | 0.0075 | 0.2549 | 90 |
| shrunk-regret-pairwise-v1 vs static-pckp | +0.007 | [-0.002, +0.018] | 0.1560 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | +0.008 | [-0.002, +0.020] | 0.1220 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.012 | [-0.017, +0.042] | 0.4094 | 1.0000 | 90 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.032 | [+0.001, +0.061] | 0.0410 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs static-pckp | +0.007 | [-0.003, +0.017] | 0.2574 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | +0.008 | [-0.003, +0.019] | 0.1660 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.012 | [-0.018, +0.041] | 0.4369 | 1.0000 | 90 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.031 | [+0.000, +0.060] | 0.0485 | 1.0000 | 90 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 90 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.001 | [-0.006, +0.008] | 0.7798 | 1.0000 | 90 |
| shrunk-regression-v2 vs linear-dfl | +0.005 | [-0.025, +0.035] | 0.7478 | 1.0000 | 90 |
| shrunk-regression-v2 vs regression-pckp | +0.025 | [-0.006, +0.055] | 0.1220 | 1.0000 | 90 |
| shrunk-listwise-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 90 |
| shrunk-listwise-v2 vs tuned-static-pckp | +0.001 | [-0.006, +0.008] | 0.7798 | 1.0000 | 90 |
| shrunk-listwise-v2 vs linear-dfl | +0.005 | [-0.025, +0.035] | 0.7478 | 1.0000 | 90 |
| shrunk-listwise-v2 vs regression-pckp | +0.025 | [-0.006, +0.055] | 0.1220 | 1.0000 | 90 |
| shrunk-selected vs static-pckp | +0.005 | [-0.003, +0.015] | 0.2404 | 1.0000 | 90 |
| shrunk-selected vs tuned-static-pckp | +0.006 | [-0.002, +0.016] | 0.1565 | 1.0000 | 90 |
| shrunk-selected vs linear-dfl | +0.010 | [-0.018, +0.039] | 0.4579 | 1.0000 | 90 |
| shrunk-selected vs regression-pckp | +0.030 | [-0.001, +0.060] | 0.0570 | 1.0000 | 90 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.663 | 0.269 | 0.613 | 0.663 | 0.98 | 0.65 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-listwise-v2 | 0.650 | 0.282 | 0.602 | 0.650 | 0.98 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-regression-v2 | 0.650 | 0.282 | 0.602 | 0.650 | 0.98 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| static-pckp | 0.650 | 0.282 | 0.602 | 0.650 | 0.98 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| combined-validation | 0.649 | 0.283 | 0.574 | 0.649 | 0.98 | 0.72 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-dro-pairwise-v1 | 0.646 | 0.286 | 0.602 | 0.646 | 0.98 | 0.67 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-regret-pairwise-v1 | 0.646 | 0.286 | 0.602 | 0.646 | 0.98 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-selected | 0.646 | 0.286 | 0.602 | 0.646 | 0.98 | 0.67 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-listwise-v1 | 0.640 | 0.292 | 0.602 | 0.640 | 0.98 | 0.65 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| shrunk-regression-v1 | 0.636 | 0.297 | 0.559 | 0.636 | 0.98 | 0.62 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regression-v1 | 0.585 | 0.347 | 0.564 | 0.585 | 0.98 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regression-pckp | 0.568 | 0.365 | 0.547 | 0.568 | 0.98 | 0.69 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| linear-dfl | 0.566 | 0.366 | 0.528 | 0.566 | 0.97 | 0.58 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| pairwise-v1 | 0.560 | 0.372 | 0.494 | 0.560 | 0.98 | 0.64 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.553
- linear-dfl: 0.576
- pairwise-v1: 0.594
- regression-pckp: 0.548
- regression-v1: 0.577
- shrunk-dro-pairwise-v1: 0.618
- shrunk-listwise-v1: 0.615
- shrunk-listwise-v2: 0.619
- shrunk-regression-v1: 0.613
- shrunk-regression-v2: 0.619
- shrunk-regret-pairwise-v1: 0.619
- shrunk-selected: 0.614
- static-pckp: 0.622

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.296
- regression-v1: 0.293
- shrunk-regression-v1: 3.946
- shrunk-regression-v2: 4.729
- shrunk-selected: 3.841

## Degenerate (all-zero) trained models, share of folds

- combined-validation: 0.00
- pairwise-v1: 0.00
- regression-v1: 0.00
- shrunk-dro-pairwise-v1: 0.00
- shrunk-listwise-v1: 0.00
- shrunk-listwise-v2: 0.00
- shrunk-regression-v1: 0.00
- shrunk-regression-v2: 0.00
- shrunk-regret-pairwise-v1: 0.00
- shrunk-selected: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- combined-validation: 50.2
- linear-dfl: 4.8
- pairwise-v1: 10.8
- regression-pckp: 0.3
- regression-v1: 12.4
- shrunk-dro-pairwise-v1: 56.6
- shrunk-listwise-v1: 89.3
- shrunk-listwise-v2: 89.3
- shrunk-regression-v1: 101.6
- shrunk-regression-v2: 116.1
- shrunk-regret-pairwise-v1: 131.7
- shrunk-selected: 85.7
- static-pckp: 0.0
- tuned-static-pckp: 155.2
