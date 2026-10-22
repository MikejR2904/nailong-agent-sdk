### Regime `live`

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

5 replicates; per replicate about 66 train / 18 validation / 24 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.756 [0.710, 0.804] | 0.469 [0.383, 0.556] | n/a | 0.842 [0.775, 0.908] | 0.883 [0.842, 0.922] | 0.888 [0.875, 0.900] |
| greedy-pask | 0.748 [0.701, 0.796] | 0.461 [0.372, 0.550] | n/a | 0.808 [0.733, 0.875] | 0.878 [0.836, 0.917] | 0.881 [0.868, 0.893] |
| random-feasible | 0.546 [0.500, 0.595] | 0.119 [0.067, 0.178] | n/a | 0.500 [0.408, 0.592] | 0.586 [0.536, 0.639] | 0.904 [0.893, 0.916] |
| linear-dfl | 0.875 [0.842, 0.905] | 0.653 [0.567, 0.733] | n/a | 1.000 [1.000, 1.000] | 0.933 [0.906, 0.956] | 0.886 [0.873, 0.898] |
| mlp-dfl | 0.895 [0.865, 0.924] | 0.706 [0.622, 0.783] | n/a | 1.000 [1.000, 1.000] | 0.958 [0.936, 0.978] | 0.883 [0.870, 0.896] |
| regression-pckp | 0.905 [0.877, 0.932] | 0.722 [0.642, 0.800] | n/a | 1.000 [1.000, 1.000] | 0.964 [0.944, 0.981] | 0.884 [0.871, 0.896] |
| topk-repair | 0.851 [0.816, 0.884] | 0.603 [0.519, 0.686] | n/a | 1.000 [1.000, 1.000] | 0.897 [0.867, 0.925] | 0.853 [0.831, 0.872] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 120} | 0 | {'n/a': 120} | 1.97 |
| greedy-pask | {'n/a': 120} | 0 | {'n/a': 120} | 3.09 |
| random-feasible | {'n/a': 120} | 0 | {'n/a': 120} | 0.25 |
| linear-dfl | {'optimal': 120} | 0 | {'applied': 120} | 2.82 |
| mlp-dfl | {'optimal': 120} | 0 | {'applied': 120} | 2.83 |
| regression-pckp | {'optimal': 120} | 0 | {'applied': 120} | 2.69 |
| topk-repair | {'n/a': 120} | 0 | {'n/a': 120} | 1.35 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0083 [-0.0222, +0.0028] | [-0.0194, +0.0028] | 0.1770 (0.1770) | 1 / 4 / 115 | 0.3750 (0.3750) |
| random-feasible | -0.2102 [-0.2667, -0.1519] | [-0.2944, -0.1241] | 0.0005 (0.0030) | 14 / 70 / 36 | 0.0000 (0.0000) |
| linear-dfl | +0.1185 [+0.0787, +0.1611] | [+0.0528, +0.1935] | 0.0005 (0.0030) | 38 / 6 / 76 | 0.0000 (0.0000) |
| mlp-dfl | +0.1389 [+0.0972, +0.1815] | [+0.0741, +0.2111] | 0.0005 (0.0030) | 39 / 2 / 79 | 0.0000 (0.0000) |
| regression-pckp | +0.1481 [+0.1074, +0.1907] | [+0.0833, +0.2213] | 0.0005 (0.0030) | 41 / 1 / 78 | 0.0000 (0.0000) |
| topk-repair | +0.0944 [+0.0528, +0.1389] | [+0.0343, +0.1593] | 0.0020 (0.0040) | 36 / 10 / 74 | 0.0002 (0.0003) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | -0.0204 [-0.0370, -0.0065] | 0.0015 | 0.0045 |
| linear-dfl - regression-pckp | -0.0296 [-0.0491, -0.0120] | 0.0005 | 0.0020 |
| linear-dfl - topk-repair | +0.0241 [+0.0019, +0.0509] | 0.0295 | 0.0590 |
| mlp-dfl - regression-pckp | -0.0093 [-0.0222, +0.0000] | 0.2409 | 0.2409 |

Held-out pairwise preference accuracy (45 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.764 |
| linear-dfl | 1.000 |
| mlp-dfl | 1.000 |
| regression-pckp | 1.000 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.11 | 1.000 |
| mlp-dfl | 4.03 | 1.000 |
| regression-pckp | 0.11 | nan |

Mean per replicate over all training rounds: 5.98 s of model fitting and 9.15 s of candidate generation and outcome evaluation.

### Regime `held-out family `ledger``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.460 [0.401, 0.522] | 0.028 [0.000, 0.074] | n/a | 0.361 [0.194, 0.528] | 0.630 [0.556, 0.713] | 0.903 [0.883, 0.922] |
| greedy-pask | 0.435 [0.367, 0.503] | 0.037 [0.000, 0.093] | n/a | 0.333 [0.167, 0.500] | 0.602 [0.519, 0.685] | 0.894 [0.876, 0.912] |
| random-feasible | 0.522 [0.435, 0.605] | 0.093 [0.009, 0.194] | n/a | 0.417 [0.250, 0.583] | 0.602 [0.509, 0.685] | 0.907 [0.890, 0.926] |
| linear-dfl | 0.515 [0.438, 0.602] | 0.185 [0.074, 0.315] | n/a | 1.000 [1.000, 1.000] | 0.657 [0.574, 0.750] | 0.891 [0.870, 0.912] |
| mlp-dfl | 0.426 [0.324, 0.528] | 0.167 [0.056, 0.306] | n/a | 0.583 [0.417, 0.750] | 0.546 [0.463, 0.639] | 0.909 [0.889, 0.929] |
| regression-pckp | 0.812 [0.753, 0.867] | 0.463 [0.315, 0.611] | n/a | 1.000 [1.000, 1.000] | 0.889 [0.833, 0.935] | 0.888 [0.869, 0.908] |
| topk-repair | 0.515 [0.438, 0.602] | 0.185 [0.074, 0.315] | n/a | 1.000 [1.000, 1.000] | 0.657 [0.574, 0.750] | 0.885 [0.865, 0.907] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 1.85 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 3.51 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.28 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.75 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.94 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 2.97 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.42 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0247 [-0.0586, +0.0031] | [-0.0648, +0.0062] | 0.2114 (0.4229) | 1 / 4 / 31 | 0.3750 (0.8588) |
| random-feasible | +0.0617 [-0.0370, +0.1543] | [+0.0000, +0.1451] | 0.0450 (0.1350) | 14 / 8 / 14 | 0.2863 (0.8588) |
| linear-dfl | +0.0556 [-0.0031, +0.1111] | [+0.0154, +0.0957] | 0.0055 (0.0275) | 13 / 5 / 18 | 0.0963 (0.4813) |
| mlp-dfl | -0.0340 [-0.1019, +0.0309] | [-0.0864, +0.0185] | 0.2539 (0.4229) | 7 / 12 / 17 | 0.3593 (0.8588) |
| regression-pckp | +0.3519 [+0.2778, +0.4198] | [+0.2963, +0.4043] | 0.0005 (0.0030) | 29 / 0 / 7 | 0.0000 (0.0000) |
| topk-repair | +0.0556 [-0.0031, +0.1111] | [+0.0154, +0.0957] | 0.0055 (0.0275) | 13 / 5 / 18 | 0.0963 (0.4813) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0895 [+0.0648, +0.1173] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | -0.2963 [-0.3673, -0.2253] | 0.0005 | 0.0020 |
| linear-dfl - topk-repair | +0.0000 [+0.0000, +0.0000] | 1.0000 | 1.0000 |
| mlp-dfl - regression-pckp | -0.3858 [-0.4722, -0.3025] | 0.0005 | 0.0020 |

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
| linear-dfl | 0.10 | 1.000 |
| mlp-dfl | 1.72 | 1.000 |
| regression-pckp | 0.10 | nan |

Mean per replicate over all training rounds: 2.89 s of model fitting and 20.90 s of candidate generation and outcome evaluation.

### Regime `held-out family `deploy``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.899 [0.876, 0.922] |
| greedy-pask | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.898 [0.876, 0.921] |
| random-feasible | 0.617 [0.537, 0.704] | 0.222 [0.083, 0.361] | n/a | 0.528 [0.361, 0.694] | 0.620 [0.537, 0.704] | 0.905 [0.882, 0.926] |
| linear-dfl | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.894 [0.871, 0.916] |
| mlp-dfl | 0.907 [0.861, 0.954] | 0.722 [0.583, 0.861] | n/a | 0.722 [0.583, 0.861] | 0.907 [0.861, 0.954] | 0.905 [0.884, 0.925] |
| regression-pckp | 0.944 [0.907, 0.981] | 0.833 [0.722, 0.944] | n/a | 0.833 [0.722, 0.944] | 0.944 [0.907, 0.981] | 0.903 [0.881, 0.925] |
| topk-repair | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.863 [0.837, 0.891] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 2.00 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 3.13 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.27 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.77 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 3.02 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 2.85 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.37 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| random-feasible | -0.3827 [-0.4630, -0.2963] | [-0.4722, -0.3056] | 0.0005 (0.0030) | 0 / 28 / 8 | 0.0000 (0.0000) |
| linear-dfl | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| mlp-dfl | -0.0926 [-0.1389, -0.0463] | [-0.1296, -0.0463] | 0.0005 (0.0030) | 0 / 10 / 26 | 0.0020 (0.0098) |
| regression-pckp | -0.0556 [-0.0926, -0.0185] | [-0.1019, -0.0185] | 0.0035 (0.0140) | 0 / 6 / 30 | 0.0312 (0.1250) |
| topk-repair | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0926 [+0.0463, +0.1296] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | +0.0556 [+0.0185, +0.1019] | 0.0035 | 0.0105 |
| linear-dfl - topk-repair | +0.0000 [+0.0000, +0.0000] | 1.0000 | 1.0000 |
| mlp-dfl - regression-pckp | -0.0370 [-0.0556, -0.0185] | 0.0050 | 0.0105 |

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
| mlp-dfl | 1.19 | 1.000 |
| regression-pckp | 0.10 | nan |

Mean per replicate over all training rounds: 1.96 s of model fitting and 27.85 s of candidate generation and outcome evaluation.

### Regime `held-out family `triage``

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

1 replicates; per replicate about 60 train / 12 validation / 36 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.815 [0.759, 0.870] | 0.444 [0.278, 0.611] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.881 [0.857, 0.905] |
| greedy-pask | 0.815 [0.759, 0.870] | 0.444 [0.278, 0.611] | n/a | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.879 [0.856, 0.903] |
| random-feasible | 0.519 [0.444, 0.583] | 0.028 [0.000, 0.083] | n/a | 0.250 [0.111, 0.389] | 0.583 [0.519, 0.648] | 0.894 [0.874, 0.916] |
| linear-dfl | 0.843 [0.787, 0.898] | 0.528 [0.361, 0.694] | n/a | 1.000 [1.000, 1.000] | 0.972 [0.935, 1.000] | 0.875 [0.853, 0.899] |
| mlp-dfl | 0.880 [0.833, 0.926] | 0.639 [0.500, 0.778] | n/a | 1.000 [1.000, 1.000] | 0.972 [0.935, 1.000] | 0.876 [0.855, 0.900] |
| regression-pckp | 0.889 [0.833, 0.935] | 0.667 [0.500, 0.806] | n/a | 0.917 [0.833, 1.000] | 0.944 [0.898, 0.981] | 0.883 [0.862, 0.906] |
| topk-repair | 0.667 [0.639, 0.694] | 0.028 [0.000, 0.083] | n/a | 1.000 [1.000, 1.000] | 0.667 [0.639, 0.694] | 0.520 [0.483, 0.561] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 36} | 0 | {'n/a': 36} | 1.79 |
| greedy-pask | {'n/a': 36} | 0 | {'n/a': 36} | 2.75 |
| random-feasible | {'n/a': 36} | 0 | {'n/a': 36} | 0.23 |
| linear-dfl | {'optimal': 36} | 0 | {'applied': 36} | 3.03 |
| mlp-dfl | {'optimal': 36} | 0 | {'applied': 36} | 2.85 |
| regression-pckp | {'optimal': 36} | 0 | {'applied': 36} | 2.52 |
| topk-repair | {'n/a': 36} | 0 | {'n/a': 36} | 1.19 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | +0.0000 [+0.0000, +0.0000] | [+0.0000, +0.0000] | 1.0000 (1.0000) | 0 / 0 / 36 | 1.0000 (1.0000) |
| random-feasible | -0.2963 [-0.3796, -0.2222] | [-0.3889, -0.2037] | 0.0005 (0.0030) | 0 / 26 / 10 | 0.0000 (0.0000) |
| linear-dfl | +0.0278 [-0.0185, +0.0741] | [-0.0185, +0.0648] | 0.2764 (0.5529) | 5 / 2 / 29 | 0.4531 (0.9062) |
| mlp-dfl | +0.0648 [+0.0093, +0.1204] | [+0.0370, +0.0926] | 0.0005 (0.0030) | 9 / 2 / 25 | 0.0654 (0.2295) |
| regression-pckp | +0.0741 [+0.0185, +0.1389] | [+0.0278, +0.1204] | 0.0035 (0.0105) | 11 / 3 / 22 | 0.0574 (0.2295) |
| topk-repair | -0.1481 [-0.2130, -0.0926] | [-0.1852, -0.1204] | 0.0005 (0.0030) | 0 / 15 / 21 | 0.0001 (0.0003) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | -0.0370 [-0.0556, -0.0185] | 0.0035 | 0.0105 |
| linear-dfl - regression-pckp | -0.0463 [-0.0741, -0.0185] | 0.0035 | 0.0105 |
| linear-dfl - topk-repair | +0.1759 [+0.1667, +0.1944] | 0.0005 | 0.0020 |
| mlp-dfl - regression-pckp | -0.0093 [-0.0463, +0.0370] | 0.8193 | 0.8193 |

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
| linear-dfl | 0.09 | 1.000 |
| mlp-dfl | 1.77 | 1.000 |
| regression-pckp | 0.10 | nan |

Mean per replicate over all training rounds: 2.81 s of model fitting and 32.45 s of candidate generation and outcome evaluation.

### Pre-registered primary hypotheses

| Cell | Mean difference in Q | Clustered 95% CI | Clustered p | Holm p | Supported |
|---|---|---|---|---|---|
| live/linear-dfl | +0.1185 | [+0.0528, +0.1935] | 0.0005 | 0.0010 | yes |
| live/mlp-dfl | +0.1389 | [+0.0741, +0.2111] | 0.0005 | 0.0010 | yes |
