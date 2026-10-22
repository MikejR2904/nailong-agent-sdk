### Regime `live`

> MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the context the SDK assembled for the retention set under test, checked by a deterministic verifier against ground truth. The tasks are generated tool-call histories, not production traffic; see the report for what that does and does not support.

5 replicates; per replicate about 66 train / 18 validation / 24 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.756 [0.710, 0.804] | 0.469 [0.383, 0.556] | n/a | 0.842 [0.775, 0.908] | 0.883 [0.842, 0.922] | 0.888 [0.875, 0.900] |
| greedy-pask | 0.748 [0.701, 0.796] | 0.461 [0.372, 0.550] | n/a | 0.808 [0.733, 0.875] | 0.878 [0.836, 0.917] | 0.881 [0.868, 0.893] |
| random-feasible | 0.546 [0.500, 0.595] | 0.119 [0.067, 0.178] | n/a | 0.500 [0.408, 0.592] | 0.586 [0.536, 0.639] | 0.904 [0.893, 0.916] |
| linear-dfl | 0.868 [0.834, 0.897] | 0.636 [0.553, 0.711] | n/a | 0.975 [0.942, 1.000] | 0.914 [0.886, 0.939] | 0.868 [0.850, 0.885] |
| mlp-dfl | 0.903 [0.872, 0.930] | 0.725 [0.642, 0.800] | n/a | 0.967 [0.933, 0.992] | 0.953 [0.928, 0.972] | 0.860 [0.846, 0.873] |
| regression-pckp | 0.905 [0.877, 0.932] | 0.722 [0.642, 0.800] | n/a | 1.000 [1.000, 1.000] | 0.964 [0.944, 0.981] | 0.882 [0.870, 0.895] |
| topk-repair | 0.862 [0.828, 0.894] | 0.628 [0.544, 0.708] | n/a | 0.975 [0.942, 1.000] | 0.914 [0.889, 0.939] | 0.866 [0.848, 0.882] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 120} | 0 | {'n/a': 120} | 2.10 |
| greedy-pask | {'n/a': 120} | 0 | {'n/a': 120} | 3.30 |
| random-feasible | {'n/a': 120} | 0 | {'n/a': 120} | 0.27 |
| linear-dfl | {'optimal': 120} | 0 | {'applied': 120} | 2.84 |
| mlp-dfl | {'optimal': 120} | 0 | {'applied': 120} | 2.96 |
| regression-pckp | {'optimal': 120} | 0 | {'applied': 120} | 2.89 |
| topk-repair | {'n/a': 120} | 0 | {'n/a': 120} | 1.43 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0083 [-0.0222, +0.0028] | [-0.0194, +0.0028] | 0.1770 (0.1770) | 1 / 4 / 115 | 0.3750 (0.3750) |
| random-feasible | -0.2102 [-0.2667, -0.1519] | [-0.2944, -0.1241] | 0.0005 (0.0030) | 14 / 70 / 36 | 0.0000 (0.0000) |
| linear-dfl | +0.1111 [+0.0676, +0.1565] | [+0.0398, +0.1898] | 0.0010 (0.0030) | 38 / 10 / 72 | 0.0001 (0.0001) |
| mlp-dfl | +0.1463 [+0.1019, +0.1926] | [+0.0796, +0.2222] | 0.0005 (0.0030) | 40 / 3 / 77 | 0.0000 (0.0000) |
| regression-pckp | +0.1481 [+0.1074, +0.1907] | [+0.0833, +0.2213] | 0.0005 (0.0030) | 41 / 1 / 78 | 0.0000 (0.0000) |
| topk-repair | +0.1056 [+0.0630, +0.1519] | [+0.0389, +0.1778] | 0.0015 (0.0030) | 37 / 9 / 74 | 0.0000 (0.0001) |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | -0.0352 [-0.0574, -0.0157] | 0.0005 | 0.0020 |
| linear-dfl - regression-pckp | -0.0370 [-0.0602, -0.0167] | 0.0005 | 0.0020 |
| linear-dfl - topk-repair | +0.0056 [-0.0167, +0.0306] | 0.7038 | 1.0000 |
| mlp-dfl - regression-pckp | -0.0019 [-0.0167, +0.0111] | 0.9358 | 1.0000 |

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
| mlp-dfl | 3.96 | 1.000 |
| regression-pckp | 0.11 | nan |

Mean per replicate over all training rounds: 5.94 s of model fitting and 4.14 s of candidate generation and outcome evaluation.

### Pre-registered primary hypotheses

| Cell | Mean difference in Q | Clustered 95% CI | Clustered p | Holm p | Supported |
|---|---|---|---|---|---|
| live/linear-dfl | +0.1111 | [+0.0398, +0.1898] | 0.0010 | 0.0010 | yes |
| live/mlp-dfl | +0.1463 | [+0.0796, +0.2222] | 0.0005 | 0.0010 | yes |
