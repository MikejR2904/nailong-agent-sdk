# Generalization results: confirmatory

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `6c2c038e3e4f75fb286a98ae99ba5fdfdba4c497fcce0cf33582d2ef015a952a`. Seeds: [3000, 3001, 3002]. Fold results: 30.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| static-pckp | 0.643 | 0.276 | 0.578 | 0.588 | 0.98 | 0.66 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| calibrated-pckp | 0.633 | 0.286 | 0.553 | 0.583 | 0.98 | 0.68 | {'optimal': 360} (max gap 0.0000) | 0.11 | 360 |
| tuned-static-pckp | 0.644 | 0.275 | 0.497 | 0.568 | 0.98 | 0.66 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| listwise-v2 | 0.615 | 0.304 | 0.513 | 0.534 | 0.98 | 0.70 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| pairwise-v1 | 0.592 | 0.327 | 0.517 | 0.530 | 0.98 | 0.73 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| dro-pairwise-v1 | 0.597 | 0.322 | 0.510 | 0.530 | 0.98 | 0.75 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| dro-listwise-v2 | 0.599 | 0.320 | 0.486 | 0.525 | 0.98 | 0.70 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regression-v1 | 0.605 | 0.314 | 0.506 | 0.525 | 0.98 | 0.73 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| listwise-v1 | 0.597 | 0.322 | 0.490 | 0.522 | 0.98 | 0.70 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regret-pairwise-v1 | 0.595 | 0.324 | 0.506 | 0.519 | 0.98 | 0.74 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regret-pairwise-v2 | 0.596 | 0.324 | 0.398 | 0.511 | 0.97 | 0.72 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| listwise-v1+boundary | 0.582 | 0.337 | 0.465 | 0.498 | 0.97 | 0.71 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regret-pairwise-v1-unnormalized | 0.575 | 0.344 | 0.448 | 0.496 | 0.97 | 0.72 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regret-pairwise-v1+boundary | 0.576 | 0.344 | 0.435 | 0.487 | 0.98 | 0.72 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| combined-validation | 0.595 | 0.324 | 0.435 | 0.481 | 0.98 | 0.70 | {'optimal': 360} (max gap 0.0000) | 0.05 | 360 |
| regression-v2 | 0.564 | 0.355 | 0.436 | 0.458 | 0.98 | 0.71 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| linear-dfl | 0.549 | 0.370 | 0.287 | 0.413 | 0.96 | 0.76 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |
| regression-pckp | 0.500 | 0.419 | 0.339 | 0.371 | 0.98 | 0.66 | {'optimal': 360} (max gap 0.0000) | n/a | 360 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.048 | [-0.073, -0.024] | 0.0005 | 0.0160 | 90 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.049 | [-0.079, -0.020] | 0.0030 | 0.0420 | 90 |
| regret-pairwise-v1 vs linear-dfl | +0.046 | [+0.012, +0.080] | 0.0065 | 0.0780 | 90 |
| regret-pairwise-v1 vs regression-pckp | +0.095 | [+0.056, +0.133] | 0.0005 | 0.0160 | 90 |
| listwise-v1 vs static-pckp | -0.046 | [-0.073, -0.019] | 0.0015 | 0.0255 | 90 |
| listwise-v1 vs tuned-static-pckp | -0.047 | [-0.081, -0.011] | 0.0105 | 0.0945 | 90 |
| listwise-v1 vs linear-dfl | +0.048 | [+0.012, +0.084] | 0.0105 | 0.0945 | 90 |
| listwise-v1 vs regression-pckp | +0.097 | [+0.054, +0.138] | 0.0005 | 0.0160 | 90 |
| dro-pairwise-v1 vs static-pckp | -0.046 | [-0.075, -0.018] | 0.0025 | 0.0375 | 90 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.046 | [-0.081, -0.013] | 0.0050 | 0.0650 | 90 |
| dro-pairwise-v1 vs linear-dfl | +0.048 | [+0.019, +0.079] | 0.0020 | 0.0320 | 90 |
| dro-pairwise-v1 vs regression-pckp | +0.097 | [+0.060, +0.135] | 0.0005 | 0.0160 | 90 |
| regression-v1 vs static-pckp | -0.038 | [-0.071, -0.005] | 0.0245 | 0.1715 | 90 |
| regression-v1 vs tuned-static-pckp | -0.038 | [-0.075, +0.000] | 0.0505 | 0.2524 | 90 |
| regression-v1 vs linear-dfl | +0.056 | [+0.016, +0.100] | 0.0075 | 0.0825 | 90 |
| regression-v1 vs regression-pckp | +0.105 | [+0.073, +0.138] | 0.0005 | 0.0160 | 90 |
| regression-v2 vs static-pckp | -0.079 | [-0.115, -0.044] | 0.0005 | 0.0160 | 90 |
| regression-v2 vs tuned-static-pckp | -0.080 | [-0.120, -0.042] | 0.0005 | 0.0160 | 90 |
| regression-v2 vs linear-dfl | +0.015 | [-0.024, +0.054] | 0.4394 | 0.8683 | 90 |
| regression-v2 vs regression-pckp | +0.064 | [+0.031, +0.095] | 0.0010 | 0.0180 | 90 |
| listwise-v2 vs static-pckp | -0.028 | [-0.053, -0.001] | 0.0410 | 0.2459 | 90 |
| listwise-v2 vs tuned-static-pckp | -0.029 | [-0.059, +0.003] | 0.0770 | 0.3079 | 90 |
| listwise-v2 vs linear-dfl | +0.066 | [+0.033, +0.100] | 0.0005 | 0.0160 | 90 |
| listwise-v2 vs regression-pckp | +0.115 | [+0.075, +0.153] | 0.0005 | 0.0160 | 90 |
| calibrated-pckp vs static-pckp | -0.010 | [-0.028, +0.008] | 0.2894 | 0.8683 | 90 |
| calibrated-pckp vs tuned-static-pckp | -0.010 | [-0.034, +0.012] | 0.3579 | 0.8683 | 90 |
| calibrated-pckp vs linear-dfl | +0.084 | [+0.052, +0.117] | 0.0005 | 0.0160 | 90 |
| calibrated-pckp vs regression-pckp | +0.133 | [+0.096, +0.168] | 0.0005 | 0.0160 | 90 |
| combined-validation vs static-pckp | -0.048 | [-0.073, -0.024] | 0.0005 | 0.0160 | 90 |
| combined-validation vs tuned-static-pckp | -0.049 | [-0.075, -0.024] | 0.0005 | 0.0160 | 90 |
| combined-validation vs linear-dfl | +0.046 | [+0.012, +0.079] | 0.0075 | 0.0825 | 90 |
| combined-validation vs regression-pckp | +0.095 | [+0.056, +0.133] | 0.0005 | 0.0160 | 90 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| calibrated-pckp | 0.674 | 0.645 | 0.620 | 0.665 | 0.596 | 0.600 |
| combined-validation | 0.629 | 0.594 | 0.560 | 0.647 | 0.556 | 0.582 |
| dro-listwise-v2 | 0.659 | 0.589 | 0.575 | 0.630 | 0.551 | 0.591 |
| dro-pairwise-v1 | 0.615 | 0.574 | 0.545 | 0.619 | 0.655 | 0.577 |
| linear-dfl | 0.608 | 0.581 | 0.619 | 0.481 | 0.480 | 0.525 |
| listwise-v1 | 0.567 | 0.609 | 0.528 | 0.682 | 0.599 | 0.599 |
| listwise-v1+boundary | 0.610 | 0.551 | 0.566 | 0.654 | 0.561 | 0.550 |
| listwise-v2 | 0.636 | 0.655 | 0.582 | 0.632 | 0.585 | 0.601 |
| pairwise-v1 | 0.574 | 0.568 | 0.549 | 0.619 | 0.656 | 0.587 |
| regression-pckp | 0.551 | 0.449 | 0.565 | 0.436 | 0.488 | 0.513 |
| regression-v1 | 0.562 | 0.610 | 0.565 | 0.597 | 0.700 | 0.598 |
| regression-v2 | 0.595 | 0.477 | 0.558 | 0.533 | 0.634 | 0.590 |
| regret-pairwise-v1 | 0.574 | 0.579 | 0.535 | 0.639 | 0.653 | 0.589 |
| regret-pairwise-v1+boundary | 0.591 | 0.559 | 0.515 | 0.624 | 0.595 | 0.570 |
| regret-pairwise-v1-unnormalized | 0.569 | 0.559 | 0.535 | 0.627 | 0.587 | 0.571 |
| regret-pairwise-v2 | 0.633 | 0.637 | 0.583 | 0.635 | 0.514 | 0.572 |
| static-pckp | 0.654 | 0.690 | 0.614 | 0.672 | 0.623 | 0.606 |
| tuned-static-pckp | 0.685 | 0.721 | 0.609 | 0.673 | 0.598 | 0.578 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.698 | 0.249 | 0.678 | 0.698 | 0.99 | 0.68 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| dro-listwise-v2 | 0.691 | 0.256 | 0.659 | 0.691 | 0.99 | 0.68 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| static-pckp | 0.681 | 0.266 | 0.664 | 0.681 | 0.99 | 0.68 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v1+boundary | 0.680 | 0.267 | 0.648 | 0.680 | 0.99 | 0.70 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v1 | 0.671 | 0.276 | 0.605 | 0.671 | 0.99 | 0.72 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| listwise-v2 | 0.668 | 0.279 | 0.625 | 0.668 | 0.99 | 0.66 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| calibrated-pckp | 0.664 | 0.283 | 0.629 | 0.664 | 0.99 | 0.75 | {'optimal': 120} (max gap 0.0000) | 0.07 | 120 |
| combined-validation | 0.661 | 0.286 | 0.622 | 0.661 | 0.99 | 0.76 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| pairwise-v1 | 0.658 | 0.289 | 0.629 | 0.658 | 0.99 | 0.72 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| linear-dfl | 0.653 | 0.294 | 0.633 | 0.653 | 0.99 | 0.70 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v2 | 0.647 | 0.299 | 0.622 | 0.647 | 0.99 | 0.71 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1+boundary | 0.640 | 0.307 | 0.582 | 0.640 | 0.99 | 0.74 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1-unnormalized | 0.636 | 0.311 | 0.609 | 0.636 | 0.99 | 0.78 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-v1 | 0.635 | 0.312 | 0.614 | 0.635 | 0.99 | 0.75 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regret-pairwise-v1 | 0.633 | 0.313 | 0.609 | 0.633 | 0.99 | 0.76 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| dro-pairwise-v1 | 0.619 | 0.328 | 0.565 | 0.619 | 0.99 | 0.79 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-v2 | 0.606 | 0.341 | 0.533 | 0.606 | 0.99 | 0.71 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |
| regression-pckp | 0.592 | 0.354 | 0.513 | 0.592 | 0.99 | 0.68 | {'optimal': 120} (max gap 0.0000) | n/a | 120 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.047 | [-0.109, +0.016] | 0.1540 | 1.0000 | 30 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.064 | [-0.129, -0.004] | 0.0350 | 0.9098 | 30 |
| regret-pairwise-v1 vs linear-dfl | -0.020 | [-0.085, +0.043] | 0.5729 | 1.0000 | 30 |
| regret-pairwise-v1 vs regression-pckp | +0.041 | [-0.027, +0.108] | 0.2404 | 1.0000 | 30 |
| listwise-v1 vs static-pckp | -0.010 | [-0.079, +0.059] | 0.7953 | 1.0000 | 30 |
| listwise-v1 vs tuned-static-pckp | -0.027 | [-0.095, +0.040] | 0.4304 | 1.0000 | 30 |
| listwise-v1 vs linear-dfl | +0.018 | [-0.059, +0.093] | 0.6483 | 1.0000 | 30 |
| listwise-v1 vs regression-pckp | +0.079 | [+0.015, +0.147] | 0.0110 | 0.3299 | 30 |
| dro-pairwise-v1 vs static-pckp | -0.062 | [-0.127, +0.007] | 0.0750 | 1.0000 | 30 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.079 | [-0.152, -0.006] | 0.0300 | 0.8098 | 30 |
| dro-pairwise-v1 vs linear-dfl | -0.034 | [-0.091, +0.023] | 0.2284 | 1.0000 | 30 |
| dro-pairwise-v1 vs regression-pckp | +0.027 | [-0.042, +0.098] | 0.4529 | 1.0000 | 30 |
| regression-v1 vs static-pckp | -0.046 | [-0.103, +0.006] | 0.0875 | 1.0000 | 30 |
| regression-v1 vs tuned-static-pckp | -0.063 | [-0.118, -0.011] | 0.0155 | 0.4494 | 30 |
| regression-v1 vs linear-dfl | -0.018 | [-0.103, +0.064] | 0.6713 | 1.0000 | 30 |
| regression-v1 vs regression-pckp | +0.043 | [-0.025, +0.111] | 0.2259 | 1.0000 | 30 |
| regression-v2 vs static-pckp | -0.075 | [-0.137, -0.017] | 0.0100 | 0.3099 | 30 |
| regression-v2 vs tuned-static-pckp | -0.092 | [-0.151, -0.033] | 0.0025 | 0.0800 | 30 |
| regression-v2 vs linear-dfl | -0.047 | [-0.114, +0.019] | 0.1635 | 1.0000 | 30 |
| regression-v2 vs regression-pckp | +0.014 | [-0.030, +0.059] | 0.5694 | 1.0000 | 30 |
| listwise-v2 vs static-pckp | -0.013 | [-0.062, +0.038] | 0.5944 | 1.0000 | 30 |
| listwise-v2 vs tuned-static-pckp | -0.030 | [-0.080, +0.019] | 0.2409 | 1.0000 | 30 |
| listwise-v2 vs linear-dfl | +0.015 | [-0.057, +0.087] | 0.7158 | 1.0000 | 30 |
| listwise-v2 vs regression-pckp | +0.076 | [+0.011, +0.141] | 0.0190 | 0.5319 | 30 |
| calibrated-pckp vs static-pckp | -0.017 | [-0.072, +0.032] | 0.5199 | 1.0000 | 30 |
| calibrated-pckp vs tuned-static-pckp | -0.034 | [-0.093, +0.020] | 0.2389 | 1.0000 | 30 |
| calibrated-pckp vs linear-dfl | +0.011 | [-0.065, +0.083] | 0.7883 | 1.0000 | 30 |
| calibrated-pckp vs regression-pckp | +0.072 | [+0.004, +0.139] | 0.0370 | 0.9248 | 30 |
| combined-validation vs static-pckp | -0.020 | [-0.090, +0.049] | 0.5844 | 1.0000 | 30 |
| combined-validation vs tuned-static-pckp | -0.037 | [-0.096, +0.012] | 0.1650 | 1.0000 | 30 |
| combined-validation vs linear-dfl | +0.007 | [-0.073, +0.085] | 0.8388 | 1.0000 | 30 |
| combined-validation vs regression-pckp | +0.068 | [+0.002, +0.134] | 0.0420 | 1.0000 | 30 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| static-pckp | 0.348 | 0.380 | 0.341 | 0.348 | 0.96 | 0.37 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| calibrated-pckp | 0.346 | 0.382 | 0.339 | 0.346 | 0.96 | 0.39 | {'optimal': 345} (max gap 0.0000) | 0.37 | 345 |
| tuned-static-pckp | 0.343 | 0.384 | 0.320 | 0.343 | 0.96 | 0.37 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| combined-validation | 0.332 | 0.396 | 0.320 | 0.332 | 0.96 | 0.39 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regret-pairwise-v1 | 0.329 | 0.398 | 0.317 | 0.329 | 0.96 | 0.45 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regret-pairwise-v1-unnormalized | 0.329 | 0.398 | 0.317 | 0.329 | 0.96 | 0.45 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regression-v1 | 0.325 | 0.402 | 0.283 | 0.325 | 0.96 | 0.50 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regression-v2 | 0.324 | 0.403 | 0.312 | 0.324 | 0.96 | 0.45 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regret-pairwise-v1+boundary | 0.321 | 0.406 | 0.302 | 0.321 | 0.95 | 0.45 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| pairwise-v1 | 0.321 | 0.407 | 0.298 | 0.321 | 0.96 | 0.42 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| dro-listwise-v2 | 0.319 | 0.408 | 0.309 | 0.319 | 0.96 | 0.37 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| listwise-v2 | 0.319 | 0.408 | 0.311 | 0.319 | 0.96 | 0.38 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regret-pairwise-v2 | 0.316 | 0.411 | 0.301 | 0.316 | 0.96 | 0.41 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| dro-pairwise-v1 | 0.311 | 0.416 | 0.302 | 0.311 | 0.95 | 0.41 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| listwise-v1+boundary | 0.309 | 0.418 | 0.306 | 0.309 | 0.96 | 0.38 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| listwise-v1 | 0.306 | 0.421 | 0.280 | 0.306 | 0.95 | 0.38 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| linear-dfl | 0.301 | 0.426 | 0.280 | 0.301 | 0.96 | 0.43 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |
| regression-pckp | 0.282 | 0.446 | 0.243 | 0.281 | 0.97 | 0.42 | {'optimal': 345} (max gap 0.0000) | n/a | 345 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | -0.018 | [-0.044, +0.006] | 0.1515 | 1.0000 | 90 |
| regret-pairwise-v1 vs tuned-static-pckp | -0.014 | [-0.037, +0.009] | 0.2354 | 1.0000 | 90 |
| regret-pairwise-v1 vs linear-dfl | +0.028 | [+0.002, +0.054] | 0.0285 | 0.5699 | 90 |
| regret-pairwise-v1 vs regression-pckp | +0.048 | [+0.017, +0.079] | 0.0040 | 0.1040 | 90 |
| listwise-v1 vs static-pckp | -0.042 | [-0.069, -0.015] | 0.0025 | 0.0725 | 90 |
| listwise-v1 vs tuned-static-pckp | -0.037 | [-0.062, -0.012] | 0.0050 | 0.1200 | 90 |
| listwise-v1 vs linear-dfl | +0.005 | [-0.018, +0.029] | 0.6868 | 1.0000 | 90 |
| listwise-v1 vs regression-pckp | +0.025 | [-0.009, +0.060] | 0.1680 | 1.0000 | 90 |
| dro-pairwise-v1 vs static-pckp | -0.036 | [-0.058, -0.015] | 0.0010 | 0.0310 | 90 |
| dro-pairwise-v1 vs tuned-static-pckp | -0.032 | [-0.057, -0.006] | 0.0170 | 0.3739 | 90 |
| dro-pairwise-v1 vs linear-dfl | +0.010 | [-0.014, +0.035] | 0.4154 | 1.0000 | 90 |
| dro-pairwise-v1 vs regression-pckp | +0.030 | [-0.001, +0.060] | 0.0570 | 1.0000 | 90 |
| regression-v1 vs static-pckp | -0.022 | [-0.051, +0.007] | 0.1215 | 1.0000 | 90 |
| regression-v1 vs tuned-static-pckp | -0.018 | [-0.044, +0.010] | 0.1880 | 1.0000 | 90 |
| regression-v1 vs linear-dfl | +0.024 | [-0.005, +0.055] | 0.1100 | 1.0000 | 90 |
| regression-v1 vs regression-pckp | +0.044 | [+0.015, +0.073] | 0.0030 | 0.0840 | 90 |
| regression-v2 vs static-pckp | -0.023 | [-0.052, +0.006] | 0.1125 | 1.0000 | 90 |
| regression-v2 vs tuned-static-pckp | -0.019 | [-0.044, +0.006] | 0.1485 | 1.0000 | 90 |
| regression-v2 vs linear-dfl | +0.023 | [-0.005, +0.051] | 0.1045 | 1.0000 | 90 |
| regression-v2 vs regression-pckp | +0.043 | [+0.014, +0.072] | 0.0035 | 0.0945 | 90 |
| listwise-v2 vs static-pckp | -0.029 | [-0.053, -0.005] | 0.0175 | 0.3739 | 90 |
| listwise-v2 vs tuned-static-pckp | -0.024 | [-0.050, +0.001] | 0.0615 | 1.0000 | 90 |
| listwise-v2 vs linear-dfl | +0.018 | [-0.008, +0.043] | 0.1745 | 1.0000 | 90 |
| listwise-v2 vs regression-pckp | +0.038 | [-0.001, +0.076] | 0.0560 | 1.0000 | 90 |
| calibrated-pckp vs static-pckp | -0.002 | [-0.013, +0.009] | 0.7578 | 1.0000 | 90 |
| calibrated-pckp vs tuned-static-pckp | +0.002 | [-0.016, +0.023] | 0.8083 | 1.0000 | 90 |
| calibrated-pckp vs linear-dfl | +0.045 | [+0.020, +0.071] | 0.0010 | 0.0310 | 90 |
| calibrated-pckp vs regression-pckp | +0.064 | [+0.033, +0.097] | 0.0005 | 0.0160 | 90 |
| combined-validation vs static-pckp | -0.016 | [-0.038, +0.005] | 0.1365 | 1.0000 | 90 |
| combined-validation vs tuned-static-pckp | -0.011 | [-0.031, +0.008] | 0.2364 | 1.0000 | 90 |
| combined-validation vs linear-dfl | +0.031 | [+0.007, +0.056] | 0.0085 | 0.1955 | 90 |
| combined-validation vs regression-pckp | +0.050 | [+0.017, +0.083] | 0.0045 | 0.1125 | 90 |

## Unseen high budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| regression-v2 | 0.861 | 0.087 | 0.834 | 0.862 | 0.98 | 0.97 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regret-pairwise-v2 | 0.853 | 0.095 | 0.822 | 0.853 | 0.97 | 0.93 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regret-pairwise-v1+boundary | 0.849 | 0.099 | 0.827 | 0.850 | 0.97 | 0.94 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| dro-pairwise-v1 | 0.845 | 0.104 | 0.817 | 0.845 | 0.97 | 0.92 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regret-pairwise-v1 | 0.843 | 0.105 | 0.814 | 0.844 | 0.97 | 0.95 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regret-pairwise-v1-unnormalized | 0.843 | 0.105 | 0.814 | 0.844 | 0.97 | 0.95 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| calibrated-pckp | 0.843 | 0.105 | 0.838 | 0.843 | 0.97 | 0.90 | {'optimal': 295} (max gap 0.0000) | 0.03 | 295 |
| combined-validation | 0.843 | 0.105 | 0.826 | 0.843 | 0.97 | 0.89 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| static-pckp | 0.843 | 0.106 | 0.823 | 0.842 | 0.97 | 0.89 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regression-v1 | 0.839 | 0.109 | 0.836 | 0.840 | 0.97 | 0.97 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| listwise-v2 | 0.839 | 0.109 | 0.777 | 0.839 | 0.97 | 0.87 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| tuned-static-pckp | 0.839 | 0.110 | 0.807 | 0.838 | 0.97 | 0.87 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| pairwise-v1 | 0.836 | 0.112 | 0.800 | 0.837 | 0.97 | 0.92 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| dro-listwise-v2 | 0.829 | 0.120 | 0.767 | 0.829 | 0.97 | 0.89 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| listwise-v1 | 0.823 | 0.125 | 0.787 | 0.823 | 0.97 | 0.87 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| linear-dfl | 0.820 | 0.128 | 0.808 | 0.820 | 0.96 | 0.91 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| listwise-v1+boundary | 0.815 | 0.134 | 0.763 | 0.814 | 0.97 | 0.88 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |
| regression-pckp | 0.797 | 0.151 | 0.781 | 0.797 | 0.97 | 0.93 | {'optimal': 295} (max gap 0.0000) | n/a | 295 |

Paired comparisons, unseen high budget (template-clustered bootstrap, Holm over 32 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regret-pairwise-v1 vs static-pckp | +0.001 | [-0.027, +0.031] | 0.9478 | 1.0000 | 90 |
| regret-pairwise-v1 vs tuned-static-pckp | +0.005 | [-0.024, +0.036] | 0.7428 | 1.0000 | 90 |
| regret-pairwise-v1 vs linear-dfl | +0.023 | [-0.004, +0.051] | 0.0950 | 1.0000 | 90 |
| regret-pairwise-v1 vs regression-pckp | +0.047 | [+0.015, +0.077] | 0.0065 | 0.1820 | 90 |
| listwise-v1 vs static-pckp | -0.020 | [-0.049, +0.010] | 0.2224 | 1.0000 | 90 |
| listwise-v1 vs tuned-static-pckp | -0.016 | [-0.045, +0.015] | 0.3264 | 1.0000 | 90 |
| listwise-v1 vs linear-dfl | +0.003 | [-0.024, +0.030] | 0.8443 | 1.0000 | 90 |
| listwise-v1 vs regression-pckp | +0.026 | [-0.006, +0.060] | 0.1080 | 1.0000 | 90 |
| dro-pairwise-v1 vs static-pckp | +0.002 | [-0.026, +0.030] | 0.8893 | 1.0000 | 90 |
| dro-pairwise-v1 vs tuned-static-pckp | +0.006 | [-0.023, +0.034] | 0.6728 | 1.0000 | 90 |
| dro-pairwise-v1 vs linear-dfl | +0.024 | [-0.004, +0.052] | 0.0930 | 1.0000 | 90 |
| dro-pairwise-v1 vs regression-pckp | +0.048 | [+0.016, +0.078] | 0.0015 | 0.0450 | 90 |
| regression-v1 vs static-pckp | -0.003 | [-0.038, +0.033] | 0.8718 | 1.0000 | 90 |
| regression-v1 vs tuned-static-pckp | +0.001 | [-0.034, +0.036] | 0.9508 | 1.0000 | 90 |
| regression-v1 vs linear-dfl | +0.019 | [-0.013, +0.053] | 0.2589 | 1.0000 | 90 |
| regression-v1 vs regression-pckp | +0.042 | [+0.017, +0.068] | 0.0005 | 0.0160 | 90 |
| regression-v2 vs static-pckp | +0.019 | [-0.013, +0.049] | 0.2304 | 1.0000 | 90 |
| regression-v2 vs tuned-static-pckp | +0.023 | [-0.010, +0.055] | 0.1575 | 1.0000 | 90 |
| regression-v2 vs linear-dfl | +0.041 | [+0.012, +0.070] | 0.0095 | 0.2469 | 90 |
| regression-v2 vs regression-pckp | +0.064 | [+0.037, +0.092] | 0.0005 | 0.0160 | 90 |
| listwise-v2 vs static-pckp | -0.004 | [-0.033, +0.025] | 0.7868 | 1.0000 | 90 |
| listwise-v2 vs tuned-static-pckp | +0.000 | [-0.030, +0.029] | 0.9993 | 1.0000 | 90 |
| listwise-v2 vs linear-dfl | +0.018 | [-0.009, +0.046] | 0.1970 | 1.0000 | 90 |
| listwise-v2 vs regression-pckp | +0.042 | [+0.013, +0.072] | 0.0050 | 0.1450 | 90 |
| calibrated-pckp vs static-pckp | +0.000 | [-0.017, +0.018] | 0.9558 | 1.0000 | 90 |
| calibrated-pckp vs tuned-static-pckp | +0.004 | [-0.013, +0.023] | 0.6303 | 1.0000 | 90 |
| calibrated-pckp vs linear-dfl | +0.023 | [-0.005, +0.049] | 0.1025 | 1.0000 | 90 |
| calibrated-pckp vs regression-pckp | +0.046 | [+0.010, +0.080] | 0.0115 | 0.2874 | 90 |
| combined-validation vs static-pckp | +0.001 | [-0.018, +0.019] | 0.8978 | 1.0000 | 90 |
| combined-validation vs tuned-static-pckp | +0.005 | [-0.016, +0.025] | 0.6193 | 1.0000 | 90 |
| combined-validation vs linear-dfl | +0.023 | [-0.007, +0.052] | 0.1285 | 1.0000 | 90 |
| combined-validation vs regression-pckp | +0.046 | [+0.014, +0.080] | 0.0070 | 0.1890 | 90 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.659 | 0.251 | 0.571 | 0.659 | 0.97 | 0.72 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| calibrated-pckp | 0.646 | 0.264 | 0.595 | 0.646 | 0.97 | 0.71 | {'optimal': 84} (max gap 0.0000) | 0.12 | 84 |
| static-pckp | 0.642 | 0.267 | 0.616 | 0.642 | 0.97 | 0.72 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regret-pairwise-v2 | 0.632 | 0.278 | 0.590 | 0.632 | 0.98 | 0.75 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regret-pairwise-v1+boundary | 0.621 | 0.289 | 0.561 | 0.621 | 0.97 | 0.77 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| listwise-v1+boundary | 0.605 | 0.305 | 0.575 | 0.605 | 0.98 | 0.71 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| combined-validation | 0.604 | 0.306 | 0.571 | 0.604 | 0.97 | 0.68 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regret-pairwise-v1 | 0.599 | 0.311 | 0.556 | 0.599 | 0.98 | 0.77 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regret-pairwise-v1-unnormalized | 0.599 | 0.311 | 0.556 | 0.599 | 0.98 | 0.77 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| dro-pairwise-v1 | 0.592 | 0.318 | 0.550 | 0.592 | 0.97 | 0.74 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| pairwise-v1 | 0.584 | 0.325 | 0.556 | 0.584 | 0.97 | 0.74 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regression-v2 | 0.581 | 0.329 | 0.579 | 0.581 | 0.98 | 0.73 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| listwise-v1 | 0.580 | 0.330 | 0.556 | 0.580 | 0.97 | 0.65 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| linear-dfl | 0.578 | 0.332 | 0.562 | 0.578 | 0.97 | 0.74 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| dro-listwise-v2 | 0.572 | 0.338 | 0.523 | 0.572 | 0.97 | 0.67 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regression-v1 | 0.566 | 0.344 | 0.541 | 0.566 | 0.98 | 0.74 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| listwise-v2 | 0.563 | 0.347 | 0.507 | 0.563 | 0.97 | 0.66 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |
| regression-pckp | 0.523 | 0.387 | 0.505 | 0.523 | 0.98 | 0.70 | {'optimal': 84} (max gap 0.0000) | n/a | 84 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.602
- dro-listwise-v2: 0.609
- dro-pairwise-v1: 0.621
- linear-dfl: 0.600
- listwise-v1: 0.608
- listwise-v1+boundary: 0.608
- listwise-v2: 0.620
- pairwise-v1: 0.623
- regression-pckp: 0.541
- regression-v1: 0.615
- regression-v2: 0.615
- regret-pairwise-v1: 0.628
- regret-pairwise-v1+boundary: 0.620
- regret-pairwise-v1-unnormalized: 0.620
- regret-pairwise-v2: 0.620
- static-pckp: 0.634

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.447
- regression-v1: 0.350
- regression-v2: 0.333

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
- combined-validation: 32.2
- dro-listwise-v2: 5.9
- dro-pairwise-v1: 5.9
- linear-dfl: 2.2
- listwise-v1: 5.0
- listwise-v1+boundary: 4.9
- listwise-v2: 5.7
- pairwise-v1: 6.7
- regression-pckp: 0.2
- regression-v1: 7.4
- regression-v2: 11.1
- regret-pairwise-v1: 6.1
- regret-pairwise-v1+boundary: 6.6
- regret-pairwise-v1-unnormalized: 6.0
- regret-pairwise-v2: 6.7
- static-pckp: 0.0
- tuned-static-pckp: 148.2
