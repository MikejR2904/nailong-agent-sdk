# Generalization results: dev2

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `eac7749b727855126f468c1c467476a077d13ed53a95cd4ec162903bfdab5edb`. Seeds: [0, 1]. Fold results: 20.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.597 | 0.335 | 0.463 | 0.498 | 0.98 | 0.57 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| dro-pairwise-v1 | 0.562 | 0.371 | 0.485 | 0.496 | 0.98 | 0.67 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| static-pckp | 0.592 | 0.340 | 0.447 | 0.455 | 0.98 | 0.58 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| listwise-v1+boundary | 0.546 | 0.387 | 0.407 | 0.453 | 0.97 | 0.77 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| calibrated-pckp | 0.560 | 0.372 | 0.425 | 0.450 | 0.98 | 0.66 | {'optimal': 240} (max gap 0.0000) | 0.10 | 240 |
| regret-pairwise-v2 | 0.557 | 0.375 | 0.422 | 0.436 | 0.98 | 0.69 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-v2 | 0.526 | 0.407 | 0.413 | 0.423 | 0.98 | 0.61 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regret-pairwise-v1 | 0.558 | 0.375 | 0.404 | 0.417 | 0.98 | 0.62 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regret-pairwise-v1-unnormalized | 0.543 | 0.390 | 0.404 | 0.417 | 0.98 | 0.62 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| pairwise-v1 | 0.557 | 0.376 | 0.394 | 0.412 | 0.98 | 0.64 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| listwise-v2 | 0.499 | 0.434 | 0.402 | 0.410 | 0.97 | 0.66 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| linear-dfl | 0.512 | 0.421 | 0.379 | 0.404 | 0.96 | 0.65 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| dro-listwise-v2 | 0.496 | 0.436 | 0.362 | 0.396 | 0.97 | 0.65 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-v1 | 0.529 | 0.403 | 0.360 | 0.391 | 0.98 | 0.59 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regret-pairwise-v1+boundary | 0.546 | 0.387 | 0.347 | 0.385 | 0.98 | 0.63 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| listwise-v1 | 0.515 | 0.418 | 0.309 | 0.377 | 0.97 | 0.67 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| combined-validation | 0.502 | 0.431 | 0.309 | 0.370 | 0.97 | 0.69 | {'optimal': 240} (max gap 0.0000) | 0.00 | 240 |
| regression-pckp | 0.432 | 0.500 | 0.272 | 0.280 | 0.98 | 0.53 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.035 | [-0.067, -0.003] | 0.0305 | 0.2439 | 60 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.039 | [-0.074, -0.005] | 0.0220 | 0.1980 | 60 |
| regret-pairwise-v1 vs linear-dfl | +0.046 | [+0.013, +0.079] | 0.0070 | 0.0980 | 60 |
| regret-pairwise-v1 vs regression-pckp | +0.125 | [+0.082, +0.170] | 0.0005 | 0.0160 | 60 |
| listwise-v1 vs static-pckp | -0.078 | [-0.109, -0.044] | 0.0005 | 0.0160 | 60 |
| listwise-v1 vs tuned-static-pckp | -0.082 | [-0.115, -0.048] | 0.0005 | 0.0160 | 60 |
| listwise-v1 vs linear-dfl | +0.003 | [-0.030, +0.036] | 0.8663 | 1.0000 | 60 |
| listwise-v1 vs regression-pckp | +0.082 | [+0.038, +0.127] | 0.0005 | 0.0160 | 60 |
| dro-pairwise-v1 vs static-pckp | -0.031 | [-0.063, +0.001] | 0.0595 | 0.3569 | 60 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.036 | [-0.068, -0.002] | 0.0350 | 0.2449 | 60 |
| dro-pairwise-v1 vs linear-dfl | +0.050 | [+0.012, +0.086] | 0.0095 | 0.1235 | 60 |
| dro-pairwise-v1 vs regression-pckp | +0.129 | [+0.085, +0.174] | 0.0005 | 0.0160 | 60 |
| regression-v1 vs static-pckp | -0.063 | [-0.107, -0.018] | 0.0050 | 0.0850 | 60 |
| regression-v1 vs tuned-static-pckp | -0.068 | [-0.107, -0.028] | 0.0005 | 0.0160 | 60 |
| regression-v1 vs linear-dfl | +0.018 | [-0.026, +0.065] | 0.4404 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.097 | [+0.054, +0.139] | 0.0005 | 0.0160 | 60 |
| regression-v2 vs static-pckp | -0.066 | [-0.108, -0.026] | 0.0010 | 0.0190 | 60 |
| regression-v2 vs tuned-static-pckp | -0.071 | [-0.109, -0.034] | 0.0010 | 0.0190 | 60 |
| regression-v2 vs linear-dfl | +0.014 | [-0.028, +0.055] | 0.4874 | 1.0000 | 60 |
| regression-v2 vs regression-pckp | +0.094 | [+0.057, +0.130] | 0.0005 | 0.0160 | 60 |
| listwise-v2 vs static-pckp | -0.093 | [-0.135, -0.053] | 0.0005 | 0.0160 | 60 |
| listwise-v2 vs tuned-static-pckp | -0.098 | [-0.138, -0.058] | 0.0005 | 0.0160 | 60 |
| listwise-v2 vs linear-dfl | -0.013 | [-0.056, +0.031] | 0.5424 | 1.0000 | 60 |
| listwise-v2 vs regression-pckp | +0.067 | [+0.020, +0.114] | 0.0050 | 0.0850 | 60 |
| calibrated-pckp vs static-pckp | -0.032 | [-0.059, -0.006] | 0.0155 | 0.1550 | 60 |
| calibrated-pckp vs tuned-static-pckp | -0.037 | [-0.065, -0.009] | 0.0120 | 0.1320 | 60 |
| calibrated-pckp vs linear-dfl | +0.048 | [+0.013, +0.082] | 0.0105 | 0.1260 | 60 |
| calibrated-pckp vs regression-pckp | +0.128 | [+0.083, +0.172] | 0.0005 | 0.0160 | 60 |
| combined-validation vs static-pckp | -0.090 | [-0.132, -0.048] | 0.0005 | 0.0160 | 60 |
| combined-validation vs tuned-static-pckp | -0.095 | [-0.140, -0.050] | 0.0005 | 0.0160 | 60 |
| combined-validation vs linear-dfl | -0.010 | [-0.048, +0.027] | 0.5959 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.070 | [+0.019, +0.120] | 0.0060 | 0.0900 | 60 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| calibrated-pckp | 0.609 | 0.621 | 0.608 | 0.455 | 0.468 | 0.601 |
| combined-validation | 0.500 | 0.583 | 0.490 | 0.370 | 0.457 | 0.612 |
| dro-listwise-v2 | 0.534 | 0.493 | 0.553 | 0.427 | 0.412 | 0.560 |
| dro-pairwise-v1 | 0.599 | 0.572 | 0.526 | 0.507 | 0.525 | 0.640 |
| linear-dfl | 0.609 | 0.493 | 0.477 | 0.404 | 0.571 | 0.518 |
| listwise-v1 | 0.584 | 0.535 | 0.517 | 0.410 | 0.441 | 0.601 |
| listwise-v1+boundary | 0.613 | 0.567 | 0.524 | 0.493 | 0.465 | 0.612 |
| listwise-v2 | 0.471 | 0.511 | 0.598 | 0.436 | 0.413 | 0.566 |
| pairwise-v1 | 0.624 | 0.582 | 0.534 | 0.412 | 0.529 | 0.660 |
| regression-pckp | 0.402 | 0.366 | 0.483 | 0.363 | 0.470 | 0.510 |
| regression-v1 | 0.496 | 0.471 | 0.551 | 0.593 | 0.522 | 0.544 |
| regression-v2 | 0.557 | 0.461 | 0.530 | 0.525 | 0.502 | 0.581 |
| regret-pairwise-v1 | 0.638 | 0.613 | 0.534 | 0.417 | 0.503 | 0.642 |
| regret-pairwise-v1+boundary | 0.630 | 0.574 | 0.519 | 0.385 | 0.522 | 0.645 |
| regret-pairwise-v1-unnormalized | 0.620 | 0.558 | 0.534 | 0.417 | 0.503 | 0.625 |
| regret-pairwise-v2 | 0.678 | 0.574 | 0.557 | 0.436 | 0.474 | 0.624 |
| static-pckp | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.642 |
| tuned-static-pckp | 0.672 | 0.601 | 0.643 | 0.554 | 0.498 | 0.615 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| regression-v1 | 0.603 | 0.347 | 0.561 | 0.603 | 0.99 | 0.68 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| listwise-v1+boundary | 0.590 | 0.360 | 0.567 | 0.590 | 0.99 | 0.65 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| listwise-v1 | 0.588 | 0.362 | 0.583 | 0.588 | 0.99 | 0.76 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regression-v2 | 0.585 | 0.365 | 0.522 | 0.585 | 0.99 | 0.61 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| calibrated-pckp | 0.570 | 0.380 | 0.548 | 0.570 | 0.99 | 0.71 | {'optimal': 80} (max gap 0.0000) | 0.03 | 80 |
| regret-pairwise-v2 | 0.570 | 0.380 | 0.562 | 0.570 | 0.99 | 0.65 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| dro-listwise-v2 | 0.566 | 0.384 | 0.556 | 0.566 | 0.99 | 0.63 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| combined-validation | 0.563 | 0.387 | 0.533 | 0.563 | 0.99 | 0.70 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| listwise-v2 | 0.562 | 0.388 | 0.551 | 0.562 | 0.99 | 0.70 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regret-pairwise-v1 | 0.560 | 0.390 | 0.544 | 0.560 | 0.99 | 0.62 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regret-pairwise-v1+boundary | 0.560 | 0.390 | 0.544 | 0.560 | 0.99 | 0.62 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regret-pairwise-v1-unnormalized | 0.560 | 0.390 | 0.544 | 0.560 | 0.99 | 0.62 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| pairwise-v1 | 0.558 | 0.392 | 0.529 | 0.558 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| linear-dfl | 0.555 | 0.395 | 0.538 | 0.555 | 0.99 | 0.53 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| tuned-static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| dro-pairwise-v1 | 0.538 | 0.412 | 0.533 | 0.538 | 0.99 | 0.57 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regression-pckp | 0.502 | 0.448 | 0.450 | 0.502 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | +0.016 | [-0.032, +0.065] | 0.4944 | 1.0000 | 20 |
| regret-pairwise-v1 vs tuned-static-pckp | +0.016 | [-0.032, +0.065] | 0.4944 | 1.0000 | 20 |
| regret-pairwise-v1 vs linear-dfl | +0.006 | [-0.047, +0.056] | 0.8358 | 1.0000 | 20 |
| regret-pairwise-v1 vs regression-pckp | +0.058 | [-0.022, +0.135] | 0.1405 | 1.0000 | 20 |
| listwise-v1 vs static-pckp | +0.044 | [-0.009, +0.098] | 0.1090 | 1.0000 | 20 |
| listwise-v1 vs tuned-static-pckp | +0.044 | [-0.009, +0.098] | 0.1090 | 1.0000 | 20 |
| listwise-v1 vs linear-dfl | +0.033 | [-0.022, +0.091] | 0.2634 | 1.0000 | 20 |
| listwise-v1 vs regression-pckp | +0.086 | [+0.012, +0.158] | 0.0240 | 0.7678 | 20 |
| dro-pairwise-v1 vs static-pckp | -0.006 | [-0.053, +0.039] | 0.8118 | 1.0000 | 20 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.006 | [-0.053, +0.039] | 0.8118 | 1.0000 | 20 |
| dro-pairwise-v1 vs linear-dfl | -0.017 | [-0.066, +0.034] | 0.5034 | 1.0000 | 20 |
| dro-pairwise-v1 vs regression-pckp | +0.036 | [-0.050, +0.120] | 0.4169 | 1.0000 | 20 |
| regression-v1 vs static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs tuned-static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs linear-dfl | +0.048 | [-0.038, +0.136] | 0.2844 | 1.0000 | 20 |
| regression-v1 vs regression-pckp | +0.101 | [+0.008, +0.194] | 0.0305 | 0.9453 | 20 |
| regression-v2 vs static-pckp | +0.041 | [-0.036, +0.116] | 0.2729 | 1.0000 | 20 |
| regression-v2 vs tuned-static-pckp | +0.041 | [-0.036, +0.116] | 0.2729 | 1.0000 | 20 |
| regression-v2 vs linear-dfl | +0.030 | [-0.061, +0.123] | 0.5269 | 1.0000 | 20 |
| regression-v2 vs regression-pckp | +0.083 | [-0.003, +0.169] | 0.0615 | 1.0000 | 20 |
| listwise-v2 vs static-pckp | +0.018 | [-0.046, +0.080] | 0.5624 | 1.0000 | 20 |
| listwise-v2 vs tuned-static-pckp | +0.018 | [-0.046, +0.080] | 0.5624 | 1.0000 | 20 |
| listwise-v2 vs linear-dfl | +0.007 | [-0.049, +0.062] | 0.7988 | 1.0000 | 20 |
| listwise-v2 vs regression-pckp | +0.060 | [-0.011, +0.129] | 0.1005 | 1.0000 | 20 |
| calibrated-pckp vs static-pckp | +0.026 | [-0.025, +0.081] | 0.3364 | 1.0000 | 20 |
| calibrated-pckp vs tuned-static-pckp | +0.026 | [-0.025, +0.081] | 0.3364 | 1.0000 | 20 |
| calibrated-pckp vs linear-dfl | +0.016 | [-0.041, +0.074] | 0.6498 | 1.0000 | 20 |
| calibrated-pckp vs regression-pckp | +0.068 | [-0.019, +0.155] | 0.1270 | 1.0000 | 20 |
| combined-validation vs static-pckp | +0.019 | [-0.035, +0.075] | 0.5249 | 1.0000 | 20 |
| combined-validation vs tuned-static-pckp | +0.019 | [-0.035, +0.075] | 0.5249 | 1.0000 | 20 |
| combined-validation vs linear-dfl | +0.008 | [-0.050, +0.070] | 0.8428 | 1.0000 | 20 |
| combined-validation vs regression-pckp | +0.061 | [-0.030, +0.150] | 0.1890 | 1.0000 | 20 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| regression-v1 | 0.360 | 0.378 | 0.348 | 0.360 | 0.97 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regret-pairwise-v1 | 0.358 | 0.379 | 0.350 | 0.358 | 0.96 | 0.53 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regret-pairwise-v1+boundary | 0.355 | 0.382 | 0.344 | 0.355 | 0.96 | 0.52 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| static-pckp | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| pairwise-v1 | 0.354 | 0.383 | 0.343 | 0.354 | 0.96 | 0.54 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| dro-pairwise-v1 | 0.353 | 0.384 | 0.339 | 0.353 | 0.96 | 0.54 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| linear-dfl | 0.352 | 0.385 | 0.343 | 0.352 | 0.95 | 0.47 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| tuned-static-pckp | 0.352 | 0.386 | 0.331 | 0.351 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regret-pairwise-v1-unnormalized | 0.350 | 0.388 | 0.333 | 0.349 | 0.96 | 0.55 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regret-pairwise-v2 | 0.348 | 0.389 | 0.343 | 0.348 | 0.96 | 0.54 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-v2 | 0.347 | 0.390 | 0.345 | 0.347 | 0.96 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| calibrated-pckp | 0.345 | 0.392 | 0.315 | 0.345 | 0.96 | 0.50 | {'optimal': 229} (max gap 0.0000) | 0.33 | 229 |
| listwise-v1 | 0.342 | 0.395 | 0.318 | 0.342 | 0.96 | 0.58 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| combined-validation | 0.337 | 0.400 | 0.294 | 0.336 | 0.96 | 0.53 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| listwise-v1+boundary | 0.336 | 0.401 | 0.294 | 0.336 | 0.96 | 0.58 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| listwise-v2 | 0.321 | 0.417 | 0.297 | 0.320 | 0.96 | 0.53 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| dro-listwise-v2 | 0.318 | 0.420 | 0.314 | 0.317 | 0.96 | 0.50 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-pckp | 0.291 | 0.446 | 0.291 | 0.291 | 0.97 | 0.35 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | +0.003 | [-0.027, +0.033] | 0.8223 | 1.0000 | 60 |
| regret-pairwise-v1 vs tuned-static-pckp | +0.006 | [-0.022, +0.034] | 0.6593 | 1.0000 | 60 |
| regret-pairwise-v1 vs linear-dfl | +0.006 | [-0.016, +0.029] | 0.5634 | 1.0000 | 60 |
| regret-pairwise-v1 vs regression-pckp | +0.067 | [+0.024, +0.111] | 0.0040 | 0.1200 | 60 |
| listwise-v1 vs static-pckp | -0.013 | [-0.046, +0.020] | 0.4799 | 1.0000 | 60 |
| listwise-v1 vs tuned-static-pckp | -0.009 | [-0.041, +0.021] | 0.5744 | 1.0000 | 60 |
| listwise-v1 vs linear-dfl | -0.010 | [-0.040, +0.022] | 0.5709 | 1.0000 | 60 |
| listwise-v1 vs regression-pckp | +0.051 | [+0.011, +0.093] | 0.0155 | 0.4339 | 60 |
| dro-pairwise-v1 vs static-pckp | -0.002 | [-0.033, +0.030] | 0.9313 | 1.0000 | 60 |
| dro-pairwise-v1 vs tuned-static-pckp | +0.001 | [-0.028, +0.030] | 0.9433 | 1.0000 | 60 |
| dro-pairwise-v1 vs linear-dfl | +0.001 | [-0.026, +0.028] | 0.9388 | 1.0000 | 60 |
| dro-pairwise-v1 vs regression-pckp | +0.062 | [+0.022, +0.103] | 0.0035 | 0.1085 | 60 |
| regression-v1 vs static-pckp | +0.005 | [-0.031, +0.040] | 0.7788 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | +0.008 | [-0.028, +0.044] | 0.6693 | 1.0000 | 60 |
| regression-v1 vs linear-dfl | +0.008 | [-0.027, +0.042] | 0.6473 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.069 | [+0.028, +0.109] | 0.0010 | 0.0320 | 60 |
| regression-v2 vs static-pckp | -0.008 | [-0.042, +0.028] | 0.6893 | 1.0000 | 60 |
| regression-v2 vs tuned-static-pckp | -0.004 | [-0.041, +0.031] | 0.8208 | 1.0000 | 60 |
| regression-v2 vs linear-dfl | -0.005 | [-0.038, +0.029] | 0.7958 | 1.0000 | 60 |
| regression-v2 vs regression-pckp | +0.056 | [+0.013, +0.100] | 0.0100 | 0.2899 | 60 |
| listwise-v2 vs static-pckp | -0.034 | [-0.067, -0.002] | 0.0385 | 1.0000 | 60 |
| listwise-v2 vs tuned-static-pckp | -0.031 | [-0.064, +0.002] | 0.0695 | 1.0000 | 60 |
| listwise-v2 vs linear-dfl | -0.031 | [-0.064, +0.001] | 0.0625 | 1.0000 | 60 |
| listwise-v2 vs regression-pckp | +0.030 | [-0.019, +0.077] | 0.2179 | 1.0000 | 60 |
| calibrated-pckp vs static-pckp | -0.010 | [-0.028, +0.008] | 0.2784 | 1.0000 | 60 |
| calibrated-pckp vs tuned-static-pckp | -0.007 | [-0.030, +0.016] | 0.5669 | 1.0000 | 60 |
| calibrated-pckp vs linear-dfl | -0.007 | [-0.037, +0.020] | 0.6253 | 1.0000 | 60 |
| calibrated-pckp vs regression-pckp | +0.054 | [+0.004, +0.104] | 0.0350 | 0.9448 | 60 |
| combined-validation vs static-pckp | -0.018 | [-0.042, +0.002] | 0.0955 | 1.0000 | 60 |
| combined-validation vs tuned-static-pckp | -0.015 | [-0.045, +0.014] | 0.3034 | 1.0000 | 60 |
| combined-validation vs linear-dfl | -0.015 | [-0.042, +0.012] | 0.2824 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.046 | [-0.002, +0.093] | 0.0635 | 1.0000 | 60 |

## Unseen high budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.864 | 0.086 | 0.842 | 0.863 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| calibrated-pckp | 0.862 | 0.088 | 0.846 | 0.861 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | 0.04 | 194 |
| combined-validation | 0.862 | 0.088 | 0.846 | 0.861 | 0.97 | 0.96 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| static-pckp | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| dro-pairwise-v1 | 0.849 | 0.101 | 0.835 | 0.848 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| listwise-v1+boundary | 0.848 | 0.102 | 0.846 | 0.848 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regret-pairwise-v1 | 0.846 | 0.104 | 0.830 | 0.846 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regret-pairwise-v1-unnormalized | 0.846 | 0.104 | 0.829 | 0.845 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| listwise-v1 | 0.842 | 0.108 | 0.821 | 0.841 | 0.97 | 0.98 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regret-pairwise-v1+boundary | 0.838 | 0.112 | 0.813 | 0.837 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regret-pairwise-v2 | 0.838 | 0.112 | 0.808 | 0.837 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| pairwise-v1 | 0.838 | 0.112 | 0.813 | 0.837 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| listwise-v2 | 0.836 | 0.114 | 0.804 | 0.835 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| dro-listwise-v2 | 0.832 | 0.118 | 0.829 | 0.832 | 0.97 | 0.94 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| linear-dfl | 0.832 | 0.118 | 0.823 | 0.831 | 0.97 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-v1 | 0.826 | 0.124 | 0.822 | 0.826 | 0.98 | 0.91 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-v2 | 0.815 | 0.135 | 0.809 | 0.815 | 0.98 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-pckp | 0.792 | 0.158 | 0.771 | 0.792 | 0.98 | 0.87 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |

Paired comparisons, unseen high budget (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.014 | [-0.051, +0.021] | 0.4779 | 1.0000 | 60 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.018 | [-0.055, +0.018] | 0.3524 | 1.0000 | 60 |
| regret-pairwise-v1 vs linear-dfl | +0.015 | [-0.010, +0.040] | 0.2344 | 1.0000 | 60 |
| regret-pairwise-v1 vs regression-pckp | +0.054 | [+0.010, +0.099] | 0.0190 | 0.4939 | 60 |
| listwise-v1 vs static-pckp | -0.018 | [-0.058, +0.020] | 0.3909 | 1.0000 | 60 |
| listwise-v1 vs tuned-static-pckp | -0.022 | [-0.062, +0.017] | 0.2939 | 1.0000 | 60 |
| listwise-v1 vs linear-dfl | +0.010 | [-0.023, +0.044] | 0.5449 | 1.0000 | 60 |
| listwise-v1 vs regression-pckp | +0.050 | [+0.007, +0.093] | 0.0180 | 0.4859 | 60 |
| dro-pairwise-v1 vs static-pckp | -0.011 | [-0.043, +0.020] | 0.5159 | 1.0000 | 60 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.015 | [-0.048, +0.017] | 0.3774 | 1.0000 | 60 |
| dro-pairwise-v1 vs linear-dfl | +0.017 | [-0.018, +0.052] | 0.3239 | 1.0000 | 60 |
| dro-pairwise-v1 vs regression-pckp | +0.057 | [+0.018, +0.095] | 0.0035 | 0.1050 | 60 |
| regression-v1 vs static-pckp | -0.033 | [-0.066, +0.000] | 0.0515 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | -0.038 | [-0.072, -0.002] | 0.0400 | 0.9198 | 60 |
| regression-v1 vs linear-dfl | -0.005 | [-0.041, +0.032] | 0.7733 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.035 | [-0.008, +0.074] | 0.1185 | 1.0000 | 60 |
| regression-v2 vs static-pckp | -0.045 | [-0.080, -0.010] | 0.0105 | 0.2939 | 60 |
| regression-v2 vs tuned-static-pckp | -0.049 | [-0.085, -0.011] | 0.0085 | 0.2464 | 60 |
| regression-v2 vs linear-dfl | -0.017 | [-0.056, +0.023] | 0.4214 | 1.0000 | 60 |
| regression-v2 vs regression-pckp | +0.023 | [-0.012, +0.057] | 0.2099 | 1.0000 | 60 |
| listwise-v2 vs static-pckp | -0.024 | [-0.058, +0.008] | 0.1455 | 1.0000 | 60 |
| listwise-v2 vs tuned-static-pckp | -0.028 | [-0.063, +0.004] | 0.0880 | 1.0000 | 60 |
| listwise-v2 vs linear-dfl | +0.005 | [-0.027, +0.037] | 0.7668 | 1.0000 | 60 |
| listwise-v2 vs regression-pckp | +0.044 | [+0.003, +0.082] | 0.0355 | 0.8873 | 60 |
| calibrated-pckp vs static-pckp | +0.002 | [-0.021, +0.026] | 0.8778 | 1.0000 | 60 |
| calibrated-pckp vs tuned-static-pckp | -0.002 | [-0.026, +0.024] | 0.8728 | 1.0000 | 60 |
| calibrated-pckp vs linear-dfl | +0.030 | [+0.001, +0.061] | 0.0415 | 0.9198 | 60 |
| calibrated-pckp vs regression-pckp | +0.070 | [+0.032, +0.107] | 0.0005 | 0.0160 | 60 |
| combined-validation vs static-pckp | +0.002 | [-0.020, +0.026] | 0.8823 | 1.0000 | 60 |
| combined-validation vs tuned-static-pckp | -0.002 | [-0.027, +0.024] | 0.8798 | 1.0000 | 60 |
| combined-validation vs linear-dfl | +0.030 | [+0.002, +0.061] | 0.0355 | 0.8873 | 60 |
| combined-validation vs regression-pckp | +0.070 | [+0.032, +0.106] | 0.0005 | 0.0160 | 60 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| linear-dfl | 0.607 | 0.307 | 0.583 | 0.607 | 0.97 | 0.65 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| combined-validation | 0.605 | 0.310 | 0.591 | 0.605 | 0.98 | 0.60 | {'optimal': 56} (max gap 0.0000) | 0.04 | 56 |
| calibrated-pckp | 0.567 | 0.347 | 0.516 | 0.567 | 0.97 | 0.71 | {'optimal': 56} (max gap 0.0000) | 0.07 | 56 |
| regret-pairwise-v2 | 0.560 | 0.354 | 0.553 | 0.560 | 0.98 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| tuned-static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.45 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| listwise-v1+boundary | 0.549 | 0.365 | 0.472 | 0.549 | 0.97 | 0.65 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| dro-pairwise-v1 | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| pairwise-v1 | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regret-pairwise-v1 | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regret-pairwise-v1-unnormalized | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| dro-listwise-v2 | 0.542 | 0.372 | 0.483 | 0.542 | 0.96 | 0.62 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| listwise-v1 | 0.533 | 0.382 | 0.496 | 0.533 | 0.97 | 0.67 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| listwise-v2 | 0.530 | 0.384 | 0.508 | 0.530 | 0.97 | 0.55 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-v2 | 0.518 | 0.396 | 0.419 | 0.518 | 0.98 | 0.53 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regret-pairwise-v1+boundary | 0.514 | 0.400 | 0.514 | 0.514 | 0.97 | 0.68 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-v1 | 0.502 | 0.413 | 0.500 | 0.502 | 0.98 | 0.56 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-pckp | 0.472 | 0.442 | 0.460 | 0.472 | 0.98 | 0.64 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.526
- dro-listwise-v2: 0.560
- dro-pairwise-v1: 0.594
- linear-dfl: 0.559
- listwise-v1: 0.578
- listwise-v1+boundary: 0.574
- listwise-v2: 0.547
- pairwise-v1: 0.587
- regression-pckp: 0.488
- regression-v1: 0.555
- regression-v2: 0.564
- regret-pairwise-v1: 0.588
- regret-pairwise-v1+boundary: 0.575
- regret-pairwise-v1-unnormalized: 0.592
- regret-pairwise-v2: 0.578
- static-pckp: 0.593

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.286
- regression-v1: 0.324
- regression-v2: 0.326

## Degenerate (all-zero) trained models, share of folds

- combined-validation: 0.00
- dro-listwise-v2: 0.00
- dro-pairwise-v1: 0.00
- listwise-v1: 0.00
- listwise-v1+boundary: 0.00
- listwise-v2: 0.00
- pairwise-v1: 0.00
- regression-v1: 0.00
- regression-v2: 0.00
- regret-pairwise-v1: 0.00
- regret-pairwise-v1+boundary: 0.00
- regret-pairwise-v1-unnormalized: 0.00
- regret-pairwise-v2: 0.00

## Mean training seconds per fold (model-call cost: none, synthetic)

- calibrated-pckp: 0.0
- combined-validation: 29.0
- dro-listwise-v2: 14.3
- dro-pairwise-v1: 13.3
- linear-dfl: 10.3
- listwise-v1: 10.5
- listwise-v1+boundary: 8.5
- listwise-v2: 13.2
- pairwise-v1: 15.2
- regression-pckp: 0.5
- regression-v1: 18.4
- regression-v2: 23.5
- regret-pairwise-v1: 14.3
- regret-pairwise-v1+boundary: 12.0
- regret-pairwise-v1-unnormalized: 13.5
- regret-pairwise-v2: 16.4
- static-pckp: 0.0
- tuned-static-pckp: 278.1
