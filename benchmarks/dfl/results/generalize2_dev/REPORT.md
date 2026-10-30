# Generalization results: dev3

> SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a closed-form quality model; nothing here measures a real agent.

Protocol file SHA-256: `6c2c038e3e4f75fb286a98ae99ba5fdfdba4c497fcce0cf33582d2ef015a952a`. Seeds: [0, 1]. Fold results: 20.

## Leave-one-family-out (primary)

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.597 | 0.335 | 0.463 | 0.498 | 0.98 | 0.57 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-regression-v1 | 0.569 | 0.363 | 0.447 | 0.455 | 0.98 | 0.56 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-regression-v2 | 0.592 | 0.340 | 0.447 | 0.455 | 0.98 | 0.58 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| static-pckp | 0.592 | 0.340 | 0.447 | 0.455 | 0.98 | 0.58 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-listwise-v2 | 0.589 | 0.344 | 0.447 | 0.454 | 0.98 | 0.59 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-regret-pairwise-v1 | 0.587 | 0.345 | 0.436 | 0.441 | 0.98 | 0.60 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-selected | 0.590 | 0.342 | 0.436 | 0.441 | 0.98 | 0.61 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-dro-pairwise-v1 | 0.584 | 0.348 | 0.420 | 0.434 | 0.98 | 0.60 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| shrunk-listwise-v1 | 0.572 | 0.361 | 0.406 | 0.427 | 0.98 | 0.60 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| pairwise-v1 | 0.557 | 0.376 | 0.394 | 0.412 | 0.98 | 0.64 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| linear-dfl | 0.512 | 0.421 | 0.379 | 0.404 | 0.96 | 0.65 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-v1 | 0.529 | 0.403 | 0.360 | 0.391 | 0.98 | 0.59 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| combined-validation | 0.530 | 0.403 | 0.360 | 0.383 | 0.98 | 0.60 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |
| regression-pckp | 0.432 | 0.500 | 0.272 | 0.280 | 0.98 | 0.53 | {'optimal': 240} (max gap 0.0000) | n/a | 240 |

Paired comparisons, leave-one-family-out (primary) (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.063 | [-0.107, -0.018] | 0.0050 | 0.0900 | 60 |
| regression-v1 vs tuned-static-pckp | -0.068 | [-0.107, -0.028] | 0.0005 | 0.0180 | 60 |
| regression-v1 vs linear-dfl | +0.018 | [-0.026, +0.065] | 0.4404 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.097 | [+0.054, +0.139] | 0.0005 | 0.0180 | 60 |
| combined-validation vs static-pckp | -0.062 | [-0.104, -0.023] | 0.0010 | 0.0250 | 60 |
| combined-validation vs tuned-static-pckp | -0.067 | [-0.108, -0.026] | 0.0015 | 0.0300 | 60 |
| combined-validation vs linear-dfl | +0.018 | [-0.023, +0.057] | 0.3754 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.098 | [+0.056, +0.140] | 0.0005 | 0.0180 | 60 |
| shrunk-regression-v1 vs static-pckp | -0.023 | [-0.041, -0.009] | 0.0010 | 0.0250 | 60 |
| shrunk-regression-v1 vs tuned-static-pckp | -0.028 | [-0.053, -0.004] | 0.0210 | 0.3149 | 60 |
| shrunk-regression-v1 vs linear-dfl | +0.057 | [+0.013, +0.099] | 0.0100 | 0.1600 | 60 |
| shrunk-regression-v1 vs regression-pckp | +0.137 | [+0.084, +0.188] | 0.0005 | 0.0180 | 60 |
| shrunk-listwise-v1 vs static-pckp | -0.020 | [-0.041, -0.003] | 0.0255 | 0.3569 | 60 |
| shrunk-listwise-v1 vs tuned-static-pckp | -0.025 | [-0.053, +0.001] | 0.0600 | 0.7798 | 60 |
| shrunk-listwise-v1 vs linear-dfl | +0.060 | [+0.019, +0.099] | 0.0060 | 0.1020 | 60 |
| shrunk-listwise-v1 vs regression-pckp | +0.140 | [+0.089, +0.189] | 0.0005 | 0.0180 | 60 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.005 | [-0.023, +0.012] | 0.5714 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | -0.010 | [-0.037, +0.016] | 0.4754 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.076 | [+0.032, +0.117] | 0.0015 | 0.0300 | 60 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.155 | [+0.104, +0.206] | 0.0005 | 0.0180 | 60 |
| shrunk-dro-pairwise-v1 vs static-pckp | -0.008 | [-0.023, +0.006] | 0.2689 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | -0.013 | [-0.039, +0.012] | 0.3204 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.073 | [+0.030, +0.112] | 0.0010 | 0.0250 | 60 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.152 | [+0.102, +0.203] | 0.0005 | 0.0180 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.005 | [-0.026, +0.017] | 0.6818 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.080 | [+0.037, +0.123] | 0.0005 | 0.0180 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.160 | [+0.109, +0.209] | 0.0005 | 0.0180 | 60 |
| shrunk-listwise-v2 vs static-pckp | -0.004 | [-0.009, +0.001] | 0.1415 | 1.0000 | 60 |
| shrunk-listwise-v2 vs tuned-static-pckp | -0.009 | [-0.031, +0.014] | 0.4584 | 1.0000 | 60 |
| shrunk-listwise-v2 vs linear-dfl | +0.077 | [+0.035, +0.119] | 0.0010 | 0.0250 | 60 |
| shrunk-listwise-v2 vs regression-pckp | +0.156 | [+0.106, +0.206] | 0.0005 | 0.0180 | 60 |
| shrunk-selected vs static-pckp | -0.002 | [-0.022, +0.016] | 0.8398 | 1.0000 | 60 |
| shrunk-selected vs tuned-static-pckp | -0.007 | [-0.036, +0.020] | 0.6213 | 1.0000 | 60 |
| shrunk-selected vs linear-dfl | +0.078 | [+0.036, +0.118] | 0.0010 | 0.0250 | 60 |
| shrunk-selected vs regression-pckp | +0.158 | [+0.107, +0.208] | 0.0005 | 0.0180 | 60 |

Held-out family quality by method:

| method | additive-deep | aligned-long | aligned-short | shifted-long | shifted-short | uninformative |
|---|---|---|---|---|---|---|
| combined-validation | 0.555 | 0.566 | 0.541 | 0.412 | 0.489 | 0.618 |
| linear-dfl | 0.609 | 0.493 | 0.477 | 0.404 | 0.571 | 0.518 |
| pairwise-v1 | 0.624 | 0.582 | 0.534 | 0.412 | 0.529 | 0.660 |
| regression-pckp | 0.402 | 0.366 | 0.483 | 0.363 | 0.470 | 0.510 |
| regression-v1 | 0.496 | 0.471 | 0.551 | 0.593 | 0.522 | 0.544 |
| shrunk-dro-pairwise-v1 | 0.724 | 0.578 | 0.645 | 0.471 | 0.434 | 0.655 |
| shrunk-listwise-v1 | 0.732 | 0.571 | 0.645 | 0.469 | 0.427 | 0.588 |
| shrunk-listwise-v2 | 0.716 | 0.571 | 0.645 | 0.502 | 0.454 | 0.642 |
| shrunk-regression-v1 | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.504 |
| shrunk-regression-v2 | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.642 |
| shrunk-regret-pairwise-v1 | 0.728 | 0.603 | 0.645 | 0.465 | 0.441 | 0.642 |
| shrunk-selected | 0.728 | 0.603 | 0.645 | 0.469 | 0.441 | 0.655 |
| static-pckp | 0.716 | 0.571 | 0.653 | 0.517 | 0.455 | 0.642 |
| tuned-static-pckp | 0.672 | 0.601 | 0.643 | 0.554 | 0.498 | 0.615 |

## Held-out long histories

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| regression-v1 | 0.603 | 0.347 | 0.561 | 0.603 | 0.99 | 0.68 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| combined-validation | 0.590 | 0.360 | 0.587 | 0.590 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-dro-pairwise-v1 | 0.573 | 0.377 | 0.572 | 0.573 | 0.99 | 0.57 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-listwise-v2 | 0.563 | 0.387 | 0.533 | 0.563 | 0.99 | 0.62 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-listwise-v1 | 0.559 | 0.391 | 0.533 | 0.559 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-selected | 0.559 | 0.391 | 0.533 | 0.559 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| pairwise-v1 | 0.558 | 0.392 | 0.529 | 0.558 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| linear-dfl | 0.555 | 0.395 | 0.538 | 0.555 | 0.99 | 0.53 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-regression-v1 | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-regression-v2 | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| shrunk-regret-pairwise-v1 | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| tuned-static-pckp | 0.544 | 0.406 | 0.533 | 0.544 | 0.99 | 0.56 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |
| regression-pckp | 0.502 | 0.448 | 0.450 | 0.502 | 0.99 | 0.60 | {'optimal': 80} (max gap 0.0000) | n/a | 80 |

Paired comparisons, held-out long histories (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs tuned-static-pckp | +0.059 | [-0.012, +0.130] | 0.1000 | 1.0000 | 20 |
| regression-v1 vs linear-dfl | +0.048 | [-0.038, +0.136] | 0.2844 | 1.0000 | 20 |
| regression-v1 vs regression-pckp | +0.101 | [+0.008, +0.194] | 0.0305 | 1.0000 | 20 |
| combined-validation vs static-pckp | +0.046 | [+0.009, +0.087] | 0.0140 | 0.5039 | 20 |
| combined-validation vs tuned-static-pckp | +0.046 | [+0.009, +0.087] | 0.0140 | 0.5039 | 20 |
| combined-validation vs linear-dfl | +0.036 | [-0.006, +0.078] | 0.1025 | 1.0000 | 20 |
| combined-validation vs regression-pckp | +0.088 | [+0.012, +0.162] | 0.0230 | 0.7818 | 20 |
| shrunk-regression-v1 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v1 vs tuned-static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v1 vs linear-dfl | -0.011 | [-0.069, +0.045] | 0.7068 | 1.0000 | 20 |
| shrunk-regression-v1 vs regression-pckp | +0.042 | [-0.043, +0.129] | 0.3539 | 1.0000 | 20 |
| shrunk-listwise-v1 vs static-pckp | +0.015 | [-0.019, +0.052] | 0.4089 | 1.0000 | 20 |
| shrunk-listwise-v1 vs tuned-static-pckp | +0.015 | [-0.019, +0.052] | 0.4089 | 1.0000 | 20 |
| shrunk-listwise-v1 vs linear-dfl | +0.004 | [-0.042, +0.047] | 0.8638 | 1.0000 | 20 |
| shrunk-listwise-v1 vs regression-pckp | +0.057 | [-0.026, +0.138] | 0.1750 | 1.0000 | 20 |
| shrunk-regret-pairwise-v1 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regret-pairwise-v1 vs linear-dfl | -0.011 | [-0.069, +0.045] | 0.7068 | 1.0000 | 20 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.042 | [-0.043, +0.129] | 0.3539 | 1.0000 | 20 |
| shrunk-dro-pairwise-v1 vs static-pckp | +0.029 | [-0.006, +0.065] | 0.1020 | 1.0000 | 20 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | +0.029 | [-0.006, +0.065] | 0.1020 | 1.0000 | 20 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.019 | [-0.029, +0.062] | 0.4584 | 1.0000 | 20 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.071 | [-0.012, +0.151] | 0.0970 | 1.0000 | 20 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 20 |
| shrunk-regression-v2 vs linear-dfl | -0.011 | [-0.069, +0.045] | 0.7068 | 1.0000 | 20 |
| shrunk-regression-v2 vs regression-pckp | +0.042 | [-0.043, +0.129] | 0.3539 | 1.0000 | 20 |
| shrunk-listwise-v2 vs static-pckp | +0.019 | [-0.006, +0.051] | 0.1910 | 1.0000 | 20 |
| shrunk-listwise-v2 vs tuned-static-pckp | +0.019 | [-0.006, +0.051] | 0.1910 | 1.0000 | 20 |
| shrunk-listwise-v2 vs linear-dfl | +0.008 | [-0.036, +0.052] | 0.7243 | 1.0000 | 20 |
| shrunk-listwise-v2 vs regression-pckp | +0.061 | [-0.022, +0.142] | 0.1560 | 1.0000 | 20 |
| shrunk-selected vs static-pckp | +0.015 | [-0.019, +0.052] | 0.4089 | 1.0000 | 20 |
| shrunk-selected vs tuned-static-pckp | +0.015 | [-0.019, +0.052] | 0.4089 | 1.0000 | 20 |
| shrunk-selected vs linear-dfl | +0.004 | [-0.042, +0.047] | 0.8638 | 1.0000 | 20 |
| shrunk-selected vs regression-pckp | +0.057 | [-0.026, +0.138] | 0.1750 | 1.0000 | 20 |

## Unseen low budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-listwise-v1 | 0.378 | 0.359 | 0.372 | 0.378 | 0.96 | 0.47 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| combined-validation | 0.375 | 0.362 | 0.372 | 0.375 | 0.96 | 0.47 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-dro-pairwise-v1 | 0.372 | 0.365 | 0.365 | 0.372 | 0.96 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-selected | 0.372 | 0.365 | 0.365 | 0.372 | 0.96 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-v1 | 0.360 | 0.378 | 0.348 | 0.360 | 0.97 | 0.46 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-regression-v1 | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-regression-v2 | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| static-pckp | 0.355 | 0.382 | 0.331 | 0.355 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| pairwise-v1 | 0.354 | 0.383 | 0.343 | 0.354 | 0.96 | 0.54 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-listwise-v2 | 0.354 | 0.383 | 0.331 | 0.354 | 0.96 | 0.43 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| shrunk-regret-pairwise-v1 | 0.354 | 0.384 | 0.331 | 0.353 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| linear-dfl | 0.352 | 0.385 | 0.343 | 0.352 | 0.95 | 0.47 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| tuned-static-pckp | 0.352 | 0.386 | 0.331 | 0.351 | 0.96 | 0.42 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |
| regression-pckp | 0.291 | 0.446 | 0.291 | 0.291 | 0.97 | 0.35 | {'optimal': 229} (max gap 0.0000) | n/a | 229 |

Paired comparisons, unseen low budget (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | +0.005 | [-0.031, +0.040] | 0.7788 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | +0.008 | [-0.028, +0.044] | 0.6693 | 1.0000 | 60 |
| regression-v1 vs linear-dfl | +0.008 | [-0.027, +0.042] | 0.6473 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.069 | [+0.028, +0.109] | 0.0010 | 0.0350 | 60 |
| combined-validation vs static-pckp | +0.020 | [+0.007, +0.036] | 0.0005 | 0.0180 | 60 |
| combined-validation vs tuned-static-pckp | +0.024 | [+0.000, +0.047] | 0.0495 | 1.0000 | 60 |
| combined-validation vs linear-dfl | +0.023 | [-0.003, +0.050] | 0.0795 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.084 | [+0.038, +0.129] | 0.0015 | 0.0480 | 60 |
| shrunk-regression-v1 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v1 vs tuned-static-pckp | +0.003 | [-0.015, +0.022] | 0.7778 | 1.0000 | 60 |
| shrunk-regression-v1 vs linear-dfl | +0.003 | [-0.021, +0.028] | 0.8173 | 1.0000 | 60 |
| shrunk-regression-v1 vs regression-pckp | +0.064 | [+0.017, +0.110] | 0.0085 | 0.2379 | 60 |
| shrunk-listwise-v1 vs static-pckp | +0.023 | [+0.005, +0.041] | 0.0080 | 0.2319 | 60 |
| shrunk-listwise-v1 vs tuned-static-pckp | +0.026 | [+0.002, +0.052] | 0.0365 | 0.8748 | 60 |
| shrunk-listwise-v1 vs linear-dfl | +0.026 | [-0.000, +0.054] | 0.0560 | 1.0000 | 60 |
| shrunk-listwise-v1 vs regression-pckp | +0.087 | [+0.038, +0.133] | 0.0015 | 0.0480 | 60 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.001 | [-0.009, +0.007] | 0.6773 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | +0.002 | [-0.015, +0.019] | 0.8708 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.002 | [-0.022, +0.025] | 0.8963 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.063 | [+0.017, +0.107] | 0.0075 | 0.2249 | 60 |
| shrunk-dro-pairwise-v1 vs static-pckp | +0.017 | [+0.001, +0.034] | 0.0350 | 0.8748 | 60 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | +0.020 | [-0.004, +0.045] | 0.1050 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.020 | [-0.004, +0.045] | 0.1205 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.081 | [+0.037, +0.123] | 0.0010 | 0.0350 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | +0.003 | [-0.015, +0.022] | 0.7778 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.003 | [-0.021, +0.028] | 0.8173 | 1.0000 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.064 | [+0.017, +0.110] | 0.0085 | 0.2379 | 60 |
| shrunk-listwise-v2 vs static-pckp | -0.001 | [-0.014, +0.011] | 0.9213 | 1.0000 | 60 |
| shrunk-listwise-v2 vs tuned-static-pckp | +0.003 | [-0.014, +0.019] | 0.7813 | 1.0000 | 60 |
| shrunk-listwise-v2 vs linear-dfl | +0.002 | [-0.022, +0.027] | 0.8598 | 1.0000 | 60 |
| shrunk-listwise-v2 vs regression-pckp | +0.063 | [+0.014, +0.111] | 0.0130 | 0.3379 | 60 |
| shrunk-selected vs static-pckp | +0.017 | [+0.001, +0.034] | 0.0350 | 0.8748 | 60 |
| shrunk-selected vs tuned-static-pckp | +0.020 | [-0.004, +0.045] | 0.1050 | 1.0000 | 60 |
| shrunk-selected vs linear-dfl | +0.020 | [-0.004, +0.045] | 0.1205 | 1.0000 | 60 |
| shrunk-selected vs regression-pckp | +0.081 | [+0.037, +0.123] | 0.0010 | 0.0350 | 60 |

## Unseen high budget

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| tuned-static-pckp | 0.864 | 0.086 | 0.842 | 0.863 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-regression-v1 | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-regression-v2 | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| static-pckp | 0.860 | 0.090 | 0.842 | 0.859 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-listwise-v2 | 0.851 | 0.099 | 0.842 | 0.850 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-regret-pairwise-v1 | 0.847 | 0.103 | 0.842 | 0.847 | 0.97 | 0.92 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| combined-validation | 0.844 | 0.106 | 0.811 | 0.843 | 0.97 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-dro-pairwise-v1 | 0.839 | 0.111 | 0.803 | 0.837 | 0.98 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-selected | 0.839 | 0.111 | 0.803 | 0.837 | 0.98 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| pairwise-v1 | 0.838 | 0.112 | 0.813 | 0.837 | 0.97 | 0.97 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| linear-dfl | 0.832 | 0.118 | 0.823 | 0.831 | 0.97 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| shrunk-listwise-v1 | 0.828 | 0.122 | 0.811 | 0.828 | 0.98 | 0.93 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-v1 | 0.826 | 0.124 | 0.822 | 0.826 | 0.98 | 0.91 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |
| regression-pckp | 0.792 | 0.158 | 0.771 | 0.792 | 0.98 | 0.87 | {'optimal': 194} (max gap 0.0000) | n/a | 194 |

Paired comparisons, unseen high budget (template-clustered bootstrap, Holm over 60 comparisons):

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| regression-v1 vs static-pckp | -0.033 | [-0.066, +0.000] | 0.0515 | 1.0000 | 60 |
| regression-v1 vs tuned-static-pckp | -0.038 | [-0.072, -0.002] | 0.0400 | 1.0000 | 60 |
| regression-v1 vs linear-dfl | -0.005 | [-0.041, +0.032] | 0.7733 | 1.0000 | 60 |
| regression-v1 vs regression-pckp | +0.035 | [-0.008, +0.074] | 0.1185 | 1.0000 | 60 |
| combined-validation vs static-pckp | -0.015 | [-0.042, +0.009] | 0.2519 | 1.0000 | 60 |
| combined-validation vs tuned-static-pckp | -0.020 | [-0.048, +0.008] | 0.1625 | 1.0000 | 60 |
| combined-validation vs linear-dfl | +0.013 | [-0.022, +0.048] | 0.4549 | 1.0000 | 60 |
| combined-validation vs regression-pckp | +0.053 | [+0.008, +0.095] | 0.0235 | 0.7358 | 60 |
| shrunk-regression-v1 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v1 vs tuned-static-pckp | -0.004 | [-0.016, +0.006] | 0.5749 | 1.0000 | 60 |
| shrunk-regression-v1 vs linear-dfl | +0.028 | [-0.007, +0.067] | 0.1260 | 1.0000 | 60 |
| shrunk-regression-v1 vs regression-pckp | +0.068 | [+0.027, +0.108] | 0.0015 | 0.0540 | 60 |
| shrunk-listwise-v1 vs static-pckp | -0.031 | [-0.063, -0.001] | 0.0390 | 1.0000 | 60 |
| shrunk-listwise-v1 vs tuned-static-pckp | -0.036 | [-0.068, -0.004] | 0.0230 | 0.7358 | 60 |
| shrunk-listwise-v1 vs linear-dfl | -0.003 | [-0.038, +0.031] | 0.8563 | 1.0000 | 60 |
| shrunk-listwise-v1 vs regression-pckp | +0.037 | [-0.011, +0.081] | 0.1265 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs static-pckp | -0.012 | [-0.027, +0.001] | 0.0655 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs tuned-static-pckp | -0.017 | [-0.033, -0.002] | 0.0320 | 0.9598 | 60 |
| shrunk-regret-pairwise-v1 vs linear-dfl | +0.016 | [-0.022, +0.056] | 0.4259 | 1.0000 | 60 |
| shrunk-regret-pairwise-v1 vs regression-pckp | +0.056 | [+0.013, +0.097] | 0.0095 | 0.3134 | 60 |
| shrunk-dro-pairwise-v1 vs static-pckp | -0.021 | [-0.051, +0.007] | 0.1360 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs tuned-static-pckp | -0.025 | [-0.055, +0.003] | 0.0775 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs linear-dfl | +0.007 | [-0.028, +0.041] | 0.6848 | 1.0000 | 60 |
| shrunk-dro-pairwise-v1 vs regression-pckp | +0.047 | [+0.000, +0.093] | 0.0500 | 1.0000 | 60 |
| shrunk-regression-v2 vs static-pckp | +0.000 | [+0.000, +0.000] | 1.0000 | 1.0000 | 60 |
| shrunk-regression-v2 vs tuned-static-pckp | -0.004 | [-0.016, +0.006] | 0.5749 | 1.0000 | 60 |
| shrunk-regression-v2 vs linear-dfl | +0.028 | [-0.007, +0.067] | 0.1260 | 1.0000 | 60 |
| shrunk-regression-v2 vs regression-pckp | +0.068 | [+0.027, +0.108] | 0.0015 | 0.0540 | 60 |
| shrunk-listwise-v2 vs static-pckp | -0.009 | [-0.028, +0.007] | 0.2869 | 1.0000 | 60 |
| shrunk-listwise-v2 vs tuned-static-pckp | -0.013 | [-0.033, +0.004] | 0.1495 | 1.0000 | 60 |
| shrunk-listwise-v2 vs linear-dfl | +0.019 | [-0.017, +0.059] | 0.3379 | 1.0000 | 60 |
| shrunk-listwise-v2 vs regression-pckp | +0.059 | [+0.014, +0.100] | 0.0065 | 0.2209 | 60 |
| shrunk-selected vs static-pckp | -0.021 | [-0.051, +0.007] | 0.1360 | 1.0000 | 60 |
| shrunk-selected vs tuned-static-pckp | -0.025 | [-0.055, +0.003] | 0.0775 | 1.0000 | 60 |
| shrunk-selected vs linear-dfl | +0.007 | [-0.028, +0.041] | 0.6848 | 1.0000 | 60 |
| shrunk-selected vs regression-pckp | +0.047 | [+0.000, +0.093] | 0.0500 | 1.0000 | 60 |

## Unseen templates, same families

| method | mean quality | mean regret | worst fold | worst fold/seed | token use | failure recall | solver status | fallback | n |
|---|---|---|---|---|---|---|---|---|---|
| shrunk-regret-pairwise-v1 | 0.614 | 0.300 | 0.608 | 0.614 | 0.98 | 0.61 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| linear-dfl | 0.607 | 0.307 | 0.583 | 0.607 | 0.97 | 0.65 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| combined-validation | 0.600 | 0.314 | 0.591 | 0.600 | 0.98 | 0.57 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-listwise-v1 | 0.588 | 0.326 | 0.561 | 0.588 | 0.98 | 0.58 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-selected | 0.588 | 0.326 | 0.561 | 0.588 | 0.98 | 0.58 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-listwise-v2 | 0.585 | 0.329 | 0.561 | 0.585 | 0.98 | 0.60 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-dro-pairwise-v1 | 0.577 | 0.337 | 0.534 | 0.577 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-regression-v1 | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| shrunk-regression-v2 | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.49 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| tuned-static-pckp | 0.557 | 0.357 | 0.524 | 0.557 | 0.98 | 0.45 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| pairwise-v1 | 0.547 | 0.367 | 0.514 | 0.547 | 0.97 | 0.66 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-v1 | 0.502 | 0.413 | 0.500 | 0.502 | 0.98 | 0.56 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |
| regression-pckp | 0.472 | 0.442 | 0.460 | 0.472 | 0.98 | 0.64 | {'optimal': 56} (max gap 0.0000) | n/a | 56 |

## Pairwise ranking accuracy on held-out candidate pools

- combined-validation: 0.548
- linear-dfl: 0.559
- pairwise-v1: 0.587
- regression-pckp: 0.488
- regression-v1: 0.555
- shrunk-dro-pairwise-v1: 0.593
- shrunk-listwise-v1: 0.597
- shrunk-listwise-v2: 0.597
- shrunk-regression-v1: 0.586
- shrunk-regression-v2: 0.593
- shrunk-regret-pairwise-v1: 0.596
- shrunk-selected: 0.596
- static-pckp: 0.593

## Set-utility calibration error (mean |U(S) - outcome|, regression methods)

- combined-validation: 0.302
- regression-v1: 0.324
- shrunk-regression-v1: 4.790
- shrunk-regression-v2: 4.960

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

- combined-validation: 48.5
- linear-dfl: 5.4
- pairwise-v1: 8.7
- regression-pckp: 0.3
- regression-v1: 10.4
- shrunk-dro-pairwise-v1: 62.8
- shrunk-listwise-v1: 94.5
- shrunk-listwise-v2: 86.9
- shrunk-regression-v1: 91.7
- shrunk-regression-v2: 126.9
- shrunk-regret-pairwise-v1: 143.2
- shrunk-selected: 98.1
- static-pckp: 0.0
- tuned-static-pckp: 216.9
