### Regime `held-out family `ledger``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.460 [0.401, 0.522] | 0.028 [0.000, 0.074] | n/a | 0.361 [0.194, 0.528] | 0.630 [0.556, 0.713] | 0.903 [0.883, 0.922] |
| greedy-pask | 0.435 [0.367, 0.503] | 0.037 [0.000, 0.093] | n/a | 0.333 [0.167, 0.500] | 0.602 [0.519, 0.685] | 0.894 [0.876, 0.912] |
| random-feasible | 0.522 [0.435, 0.605] | 0.093 [0.009, 0.194] | n/a | 0.417 [0.250, 0.583] | 0.602 [0.509, 0.685] | 0.907 [0.890, 0.926] |
| linear-dfl | 0.515 [0.438, 0.602] | 0.185 [0.074, 0.315] | n/a | 1.000 [1.000, 1.000] | 0.657 [0.574, 0.750] | 0.885 [0.865, 0.907] |
| mlp-dfl | 0.426 [0.324, 0.528] | 0.167 [0.056, 0.306] | n/a | 0.583 [0.417, 0.750] | 0.546 [0.463, 0.639] | 0.909 [0.889, 0.929] |
| regression-pckp | 0.802 [0.747, 0.858] | 0.435 [0.287, 0.583] | n/a | 1.000 [1.000, 1.000] | 0.880 [0.824, 0.935] | 0.887 [0.868, 0.907] |
| topk-repair | 0.515 [0.438, 0.602] | 0.185 [0.074, 0.315] | n/a | 1.000 [1.000, 1.000] | 0.657 [0.574, 0.750] | 0.885 [0.865, 0.907] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 1.92 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 3.64 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.30 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 3.04 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 3.12 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 3.01 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.49 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0247 [-0.0586, +0.0031] | [-0.0648, +0.0062] | 0.2114 (0.4229) | 1 / 4 / 31 | 0.3750 (0.8588) |
| random-feasible | +0.0617 [-0.0370, +0.1543] | [+0.0000, +0.1451] | 0.0450 (0.1350) | 14 / 8 / 14 | 0.2863 (0.8588) |
| linear-dfl | +0.0556 [-0.0031, +0.1111] | [+0.0154, +0.0957] | 0.0055 (0.0275) | 13 / 5 / 18 | 0.0963 (0.4813) |
| mlp-dfl | -0.0340 [-0.1019, +0.0309] | [-0.0864, +0.0185] | 0.2539 (0.4229) | 7 / 12 / 17 | 0.3593 (0.8588) |
| regression-pckp | +0.3426 [+0.2716, +0.4105] | [+0.2932, +0.3889] | 0.0005 (0.0030) | 29 / 0 / 7 | 0.0000 (0.0000) |
| topk-repair | +0.0556 [-0.0031, +0.1111] | [+0.0154, +0.0957] | 0.0055 (0.0275) | 13 / 5 / 18 | 0.0963 (0.4813) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0895 [+0.0648, +0.1173] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | -0.2870 [-0.3488, -0.2253] | 0.0005 | 0.0020 |
| linear-dfl - topk-repair | +0.0000 [+0.0000, +0.0000] | 1.0000 | 1.0000 |
| mlp-dfl - regression-pckp | -0.3765 [-0.4537, -0.2994] | 0.0005 | 0.0020 |

Held-out pairwise preference accuracy (27 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.389 |
| linear-dfl | 0.815 |
| mlp-dfl | 0.704 |
| regression-pckp | 0.963 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.11 | 1.000 |
| mlp-dfl | 1.85 | 1.000 |
| regression-pckp | 0.11 | nan |

Mean per replicate over all training rounds: 3.05 s of model fitting and 1503.91 s of candidate generation and outcome evaluation.

### Regime `held-out family `deploy``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.899 [0.876, 0.922] |
| greedy-pask | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.898 [0.876, 0.921] |
| random-feasible | 0.617 [0.537, 0.704] | 0.222 [0.083, 0.361] | n/a | 0.528 [0.361, 0.694] | 0.620 [0.537, 0.704] | 0.905 [0.882, 0.926] |
| linear-dfl | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.863 [0.837, 0.891] |
| mlp-dfl | 0.907 [0.861, 0.954] | 0.722 [0.583, 0.861] | n/a | 0.722 [0.583, 0.861] | 0.907 [0.861, 0.954] | 0.874 [0.848, 0.900] |
| regression-pckp | 0.954 [0.917, 0.991] | 0.861 [0.750, 0.972] | n/a | 0.861 [0.750, 0.972] | 0.954 [0.917, 0.991] | 0.904 [0.881, 0.925] |
| topk-repair | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.863 [0.837, 0.891] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 1.98 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 3.18 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.27 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.65 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.84 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 2.69 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.32 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| random-feasible | -0.3827 [-0.4630, -0.2963] | [-0.4722, -0.3056] | 0.0005 (0.0030) | 0 / 28 / 8 | 0.0000 (0.0000) |
| linear-dfl | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| mlp-dfl | -0.0926 [-0.1389, -0.0463] | [-0.1296, -0.0463] | 0.0005 (0.0030) | 0 / 10 / 26 | 0.0020 (0.0098) |
| regression-pckp | -0.0463 [-0.0833, -0.0093] | [-0.1019, -0.0093] | 0.0360 (0.1440) | 0 / 5 / 31 | 0.0625 (0.2500) |
| topk-repair | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0926 [+0.0463, +0.1296] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | +0.0463 [+0.0093, +0.1019] | 0.0360 | 0.0720 |
| linear-dfl - topk-repair | +0.0000 [+0.0000, +0.0000] | 1.0000 | 1.0000 |
| mlp-dfl - regression-pckp | -0.0463 [-0.0741, -0.0185] | 0.0050 | 0.0150 |

Held-out pairwise preference accuracy (24 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 1.000 |
| linear-dfl | 1.000 |
| mlp-dfl | 1.000 |
| regression-pckp | 1.000 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.07 | 1.000 |
| mlp-dfl | 1.26 | 1.000 |
| regression-pckp | 0.11 | nan |

Mean per replicate over all training rounds: 2.07 s of model fitting and 1292.63 s of candidate generation and outcome evaluation.

### Regime `held-out family `triage``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.815 [0.759, 0.870] | 0.444 [0.278, 0.611] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.881 [0.857, 0.905] |
| greedy-pask | 0.815 [0.759, 0.870] | 0.444 [0.278, 0.611] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.879 [0.856, 0.903] |
| random-feasible | 0.519 [0.444, 0.583] | 0.028 [0.000, 0.083] | n/a | 0.250 [0.111, 0.389] | 0.583 [0.519, 0.648] | 0.894 [0.874, 0.916] |
| linear-dfl | 0.454 [0.398, 0.509] | 0.000 [0.000, 0.000] | n/a | 1.000 [1.000, 1.000] | 0.454 [0.398, 0.509] | 0.362 [0.316, 0.407] |
| mlp-dfl | 0.759 [0.694, 0.815] | 0.333 [0.194, 0.500] | n/a | 1.000 [1.000, 1.000] | 0.759 [0.694, 0.815] | 0.634 [0.567, 0.699] |
| regression-pckp | 0.889 [0.833, 0.935] | 0.667 [0.500, 0.806] | n/a | 0.917 [0.833, 1.000] | 0.944 [0.898, 0.981] | 0.883 [0.862, 0.906] |
| topk-repair | 0.454 [0.398, 0.509] | 0.000 [0.000, 0.000] | n/a | 1.000 [1.000, 1.000] | 0.454 [0.398, 0.509] | 0.362 [0.316, 0.407] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 1.83 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 2.84 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.23 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.34 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.56 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 2.52 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.19 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| random-feasible | -0.2963 [-0.3796, -0.2222] | [-0.3889, -0.2037] | 0.0005 (0.0030) | 0 / 26 / 10 | 0.0000 (0.0000) |
| linear-dfl | -0.3611 [-0.4444, -0.2778] | [-0.4259, -0.2870] | 0.0005 (0.0030) | 0 / 26 / 10 | 0.0000 (0.0000) |
| mlp-dfl | -0.0556 [-0.1204, +0.0093] | [-0.1296, +0.0185] | 0.2000 (0.3999) | 2 / 7 / 27 | 0.1797 (0.3594) |
| regression-pckp | +0.0741 [+0.0185, +0.1389] | [+0.0278, +0.1204] | 0.0035 (0.0105) | 11 / 3 / 22 | 0.0574 (0.1721) |
| topk-repair | -0.3611 [-0.4444, -0.2778] | [-0.4259, -0.2870] | 0.0005 (0.0030) | 0 / 26 / 10 | 0.0000 (0.0000) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | -0.3056 [-0.3611, -0.2500] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | -0.4352 [-0.5185, -0.3519] | 0.0005 | 0.0020 |
| linear-dfl - topk-repair | +0.0000 [+0.0000, +0.0000] | 1.0000 | 1.0000 |
| mlp-dfl - regression-pckp | -0.1296 [-0.2222, -0.0463] | 0.0005 | 0.0020 |

Held-out pairwise preference accuracy (11 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 1.000 |
| linear-dfl | 1.000 |
| mlp-dfl | 1.000 |
| regression-pckp | 1.000 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.11 | 1.000 |
| mlp-dfl | 1.83 | 1.000 |
| regression-pckp | 0.10 | nan |

Mean per replicate over all training rounds: 2.95 s of model fitting and 1379.58 s of candidate generation and outcome evaluation.
