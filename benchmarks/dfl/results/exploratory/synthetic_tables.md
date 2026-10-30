### Regime `aligned`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

3 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.597 [0.509, 0.688] | 0.597 [0.510, 0.683] | 0.265 [0.188, 0.347] | 0.711 [0.583, 0.828] | 0.742 [0.673, 0.812] | 0.951 [0.939, 0.962] |
| greedy-pask | 0.550 [0.464, 0.638] | 0.549 [0.469, 0.634] | 0.312 [0.236, 0.391] | 0.662 [0.534, 0.784] | 0.707 [0.636, 0.776] | 0.913 [0.896, 0.929] |
| random-feasible | 0.425 [0.348, 0.501] | 0.431 [0.356, 0.509] | 0.437 [0.355, 0.516] | 0.488 [0.348, 0.623] | 0.602 [0.531, 0.668] | 0.936 [0.923, 0.949] |
| linear-dfl | 0.657 [0.583, 0.732] | 0.660 [0.590, 0.735] | 0.205 [0.133, 0.277] | 0.777 [0.652, 0.885] | 0.795 [0.736, 0.853] | 0.944 [0.931, 0.958] |
| mlp-dfl | 0.628 [0.544, 0.711] | 0.637 [0.555, 0.719] | 0.234 [0.153, 0.315] | 0.578 [0.429, 0.723] | 0.764 [0.694, 0.830] | 0.945 [0.931, 0.959] |
| regression-pckp | 0.647 [0.567, 0.728] | 0.652 [0.573, 0.732] | 0.215 [0.144, 0.289] | 0.711 [0.578, 0.833] | 0.784 [0.721, 0.846] | 0.950 [0.937, 0.963] |
| topk-repair | 0.640 [0.564, 0.720] | 0.646 [0.572, 0.724] | 0.222 [0.151, 0.293] | 0.733 [0.605, 0.848] | 0.784 [0.728, 0.843] | 0.931 [0.916, 0.944] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 54} | 0 | {'n/a': 54} | 1.18 |
| greedy-pask | {'n/a': 54} | 0 | {'n/a': 54} | 1.75 |
| random-feasible | {'n/a': 54} | 0 | {'n/a': 54} | 0.17 |
| linear-dfl | {'optimal': 54} | 0 | {'applied': 54} | 1.87 |
| mlp-dfl | {'optimal': 54} | 0 | {'applied': 54} | 1.95 |
| regression-pckp | {'optimal': 54} | 0 | {'applied': 54} | 1.85 |
| topk-repair | {'n/a': 54} | 0 | {'n/a': 54} | 0.76 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [95% CI] | Wins / losses / ties | Sign-test p | Holm p |
|---|---|---|---|---|
| greedy-pask | -0.0472 [-0.1046, +0.0067] | 2 / 7 / 45 | 0.1797 | 0.4014 |
| random-feasible | -0.1721 [-0.2764, -0.0615] | 9 / 26 / 19 | 0.0060 | 0.0359 |
| linear-dfl | +0.0599 [-0.0140, +0.1323] | 15 / 5 / 34 | 0.0414 | 0.2069 |
| mlp-dfl | +0.0310 [-0.0411, +0.1055] | 13 / 7 / 34 | 0.2632 | 0.4014 |
| regression-pckp | +0.0500 [-0.0327, +0.1361] | 15 / 6 / 33 | 0.0784 | 0.3134 |
| topk-repair | +0.0425 [-0.0444, +0.1369] | 15 / 7 / 32 | 0.1338 | 0.4014 |

Held-out pairwise preference accuracy (715 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.781 |
| linear-dfl | 0.764 |
| mlp-dfl | 0.768 |
| regression-pckp | 0.750 |

Training cost (mean seconds, all rounds, including the l2 grid for linear models):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 1.72 | 0.695 |
| mlp-dfl | 0.60 | 0.732 |
| regression-pckp | 0.04 | nan |

Ablations (linear Ranking-DFL, mean test Q; full model 0.6572):

| Removed feature group | Test Q |
|---|---|
| task | 0.5219 |
| graph | 0.6420 |
| provenance | 0.6590 |
| dynamics | 0.6613 |
| outcome | 0.6401 |

| Training fraction | Test Q |
|---|---|
| 0.25 | 0.6611 |
| 0.5 | 0.6378 |
| 1.0 | 0.6572 |

### Regime `shifted`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

3 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.555 [0.470, 0.636] | 0.566 [0.482, 0.648] | 0.347 [0.269, 0.424] | 0.652 [0.528, 0.771] | 0.715 [0.647, 0.778] | 0.963 [0.954, 0.971] |
| greedy-pask | 0.577 [0.493, 0.662] | 0.588 [0.504, 0.673] | 0.324 [0.246, 0.403] | 0.639 [0.510, 0.760] | 0.731 [0.667, 0.795] | 0.927 [0.913, 0.941] |
| random-feasible | 0.455 [0.381, 0.536] | 0.459 [0.381, 0.541] | 0.446 [0.363, 0.524] | 0.550 [0.422, 0.672] | 0.637 [0.574, 0.701] | 0.934 [0.919, 0.948] |
| linear-dfl | 0.654 [0.572, 0.734] | 0.668 [0.589, 0.744] | 0.247 [0.173, 0.329] | 0.830 [0.729, 0.924] | 0.795 [0.733, 0.853] | 0.951 [0.940, 0.960] |
| mlp-dfl | 0.670 [0.590, 0.747] | 0.682 [0.605, 0.756] | 0.232 [0.154, 0.314] | 0.801 [0.681, 0.899] | 0.804 [0.741, 0.861] | 0.945 [0.931, 0.957] |
| regression-pckp | 0.665 [0.579, 0.751] | 0.677 [0.592, 0.761] | 0.236 [0.161, 0.320] | 0.764 [0.637, 0.878] | 0.793 [0.722, 0.860] | 0.947 [0.936, 0.957] |
| topk-repair | 0.599 [0.522, 0.674] | 0.609 [0.533, 0.683] | 0.303 [0.225, 0.380] | 0.801 [0.695, 0.894] | 0.753 [0.694, 0.812] | 0.937 [0.924, 0.949] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 54} | 0 | {'n/a': 54} | 1.41 |
| greedy-pask | {'n/a': 54} | 0 | {'n/a': 54} | 1.94 |
| random-feasible | {'n/a': 54} | 0 | {'n/a': 54} | 0.21 |
| linear-dfl | {'optimal': 54} | 0 | {'applied': 54} | 2.29 |
| mlp-dfl | {'optimal': 54} | 0 | {'applied': 54} | 2.41 |
| regression-pckp | {'optimal': 54} | 0 | {'applied': 54} | 2.36 |
| topk-repair | {'n/a': 54} | 0 | {'n/a': 54} | 1.01 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [95% CI] | Wins / losses / ties | Sign-test p | Holm p |
|---|---|---|---|---|
| greedy-pask | +0.0221 [-0.0224, +0.0667] | 4 / 1 / 49 | 0.3750 | 0.7500 |
| random-feasible | -0.0994 [-0.1997, +0.0085] | 9 / 18 / 27 | 0.1221 | 0.3662 |
| linear-dfl | +0.0994 [-0.0056, +0.2076] | 20 / 9 / 25 | 0.0614 | 0.2457 |
| mlp-dfl | +0.1150 [+0.0053, +0.2255] | 21 / 9 / 24 | 0.0428 | 0.2139 |
| regression-pckp | +0.1104 [+0.0057, +0.2130] | 21 / 8 / 25 | 0.0241 | 0.1447 |
| topk-repair | +0.0439 [-0.0618, +0.1531] | 18 / 13 / 23 | 0.4731 | 0.7500 |

Held-out pairwise preference accuracy (678 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.636 |
| linear-dfl | 0.711 |
| mlp-dfl | 0.729 |
| regression-pckp | 0.710 |

Training cost (mean seconds, all rounds, including the l2 grid for linear models):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.69 | 0.831 |
| mlp-dfl | 0.69 | 0.819 |
| regression-pckp | 0.05 | nan |

Ablations (linear Ranking-DFL, mean test Q; full model 0.6541):

| Removed feature group | Test Q |
|---|---|
| task | 0.6188 |
| graph | 0.6099 |
| provenance | 0.6674 |
| dynamics | 0.6446 |
| outcome | 0.6404 |

| Training fraction | Test Q |
|---|---|
| 0.25 | 0.6539 |
| 0.5 | 0.6262 |
| 1.0 | 0.6541 |
