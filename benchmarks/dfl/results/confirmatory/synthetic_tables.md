### Regime `aligned`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 53 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.622 [0.590, 0.654] | 0.622 [0.590, 0.656] | 0.253 [0.224, 0.280] | 0.702 [0.650, 0.752] | 0.763 [0.737, 0.789] | 0.963 [0.959, 0.967] |
| greedy-pask | 0.587 [0.555, 0.620] | 0.586 [0.553, 0.619] | 0.287 [0.258, 0.316] | 0.679 [0.626, 0.728] | 0.735 [0.709, 0.762] | 0.932 [0.926, 0.937] |
| random-feasible | 0.478 [0.445, 0.511] | 0.476 [0.442, 0.511] | 0.396 [0.364, 0.430] | 0.510 [0.455, 0.565] | 0.643 [0.612, 0.672] | 0.945 [0.941, 0.950] |
| linear-dfl | 0.666 [0.635, 0.699] | 0.664 [0.630, 0.698] | 0.209 [0.179, 0.237] | 0.690 [0.637, 0.737] | 0.798 [0.773, 0.823] | 0.948 [0.942, 0.953] |
| mlp-dfl | 0.637 [0.606, 0.667] | 0.636 [0.604, 0.667] | 0.238 [0.209, 0.267] | 0.698 [0.647, 0.746] | 0.774 [0.747, 0.797] | 0.942 [0.936, 0.948] |
| regression-pckp | 0.652 [0.620, 0.682] | 0.650 [0.617, 0.683] | 0.223 [0.195, 0.253] | 0.652 [0.601, 0.701] | 0.788 [0.764, 0.811] | 0.952 [0.947, 0.956] |
| topk-repair | 0.680 [0.651, 0.711] | 0.680 [0.650, 0.711] | 0.194 [0.166, 0.222] | 0.675 [0.620, 0.722] | 0.812 [0.790, 0.834] | 0.939 [0.933, 0.944] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 353} | 0 | {'n/a': 353} | 1.42 |
| greedy-pask | {'n/a': 353} | 0 | {'n/a': 353} | 1.81 |
| random-feasible | {'n/a': 353} | 0 | {'n/a': 353} | 0.19 |
| linear-dfl | {'optimal': 353} | 0 | {'applied': 353} | 2.06 |
| mlp-dfl | {'optimal': 353} | 0 | {'applied': 353} | 2.15 |
| regression-pckp | {'optimal': 353} | 0 | {'applied': 353} | 2.05 |
| topk-repair | {'n/a': 353} | 0 | {'n/a': 353} | 0.85 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [95% CI] | Wins / losses / ties | Sign-test p | Holm p |
|---|---|---|---|---|
| greedy-pask | -0.0342 [-0.0553, -0.0147] | 21 / 47 / 285 | 0.0022 | 0.0109 |
| random-feasible | -0.1432 [-0.1824, -0.1073] | 45 / 138 / 170 | 0.0000 | 0.0000 |
| linear-dfl | +0.0442 [+0.0134, +0.0743] | 67 / 39 / 247 | 0.0084 | 0.0253 |
| mlp-dfl | +0.0151 [-0.0151, +0.0458] | 59 / 52 / 242 | 0.5692 | 0.5692 |
| regression-pckp | +0.0300 [-0.0001, +0.0597] | 62 / 48 / 243 | 0.2150 | 0.4300 |
| topk-repair | +0.0585 [+0.0270, +0.0906] | 81 / 47 / 225 | 0.0034 | 0.0135 |

Held-out pairwise preference accuracy (5112 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.720 |
| linear-dfl | 0.764 |
| mlp-dfl | 0.733 |
| regression-pckp | 0.740 |

Training cost, mean seconds for the final-round fit (includes the l2 grid for the linear model):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.74 | 0.775 |
| mlp-dfl | 0.77 | 0.769 |
| regression-pckp | 0.05 | nan |

Mean per replicate over all training rounds: 2.53 s of model fitting and 2.25 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL, mean test Q; full model 0.6672):

| Removed feature group | Test Q |
|---|---|
| task | 0.5069 |
| graph | 0.6623 |
| provenance | 0.6715 |
| dynamics | 0.6633 |
| outcome | 0.6758 |

| Training fraction | Test Q |
|---|---|
| 0.25 | 0.6295 |
| 0.5 | 0.6609 |
| 1.0 | 0.6672 |

### Regime `shifted`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.530 [0.497, 0.562] | 0.537 [0.503, 0.570] | 0.376 [0.343, 0.408] | 0.544 [0.496, 0.590] | 0.685 [0.656, 0.713] | 0.961 [0.957, 0.965] |
| greedy-pask | 0.519 [0.487, 0.551] | 0.528 [0.496, 0.560] | 0.386 [0.353, 0.418] | 0.524 [0.478, 0.572] | 0.677 [0.649, 0.704] | 0.921 [0.915, 0.927] |
| random-feasible | 0.516 [0.485, 0.550] | 0.521 [0.490, 0.555] | 0.389 [0.356, 0.422] | 0.617 [0.567, 0.665] | 0.679 [0.653, 0.707] | 0.940 [0.935, 0.945] |
| linear-dfl | 0.713 [0.682, 0.744] | 0.717 [0.685, 0.749] | 0.192 [0.164, 0.221] | 0.804 [0.764, 0.841] | 0.835 [0.811, 0.857] | 0.933 [0.926, 0.939] |
| mlp-dfl | 0.693 [0.662, 0.723] | 0.698 [0.666, 0.729] | 0.213 [0.183, 0.243] | 0.793 [0.753, 0.832] | 0.818 [0.794, 0.841] | 0.937 [0.931, 0.944] |
| regression-pckp | 0.687 [0.654, 0.717] | 0.692 [0.658, 0.722] | 0.219 [0.190, 0.249] | 0.781 [0.742, 0.820] | 0.816 [0.791, 0.837] | 0.949 [0.944, 0.953] |
| topk-repair | 0.694 [0.662, 0.725] | 0.698 [0.664, 0.729] | 0.211 [0.182, 0.241] | 0.808 [0.769, 0.843] | 0.822 [0.797, 0.844] | 0.928 [0.921, 0.935] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 352} | 0 | {'n/a': 352} | 1.46 |
| greedy-pask | {'n/a': 352} | 0 | {'n/a': 352} | 1.81 |
| random-feasible | {'n/a': 352} | 0 | {'n/a': 352} | 0.19 |
| linear-dfl | {'optimal': 352} | 0 | {'applied': 352} | 2.05 |
| mlp-dfl | {'optimal': 352} | 0 | {'applied': 352} | 2.15 |
| regression-pckp | {'optimal': 352} | 0 | {'applied': 352} | 2.07 |
| topk-repair | {'n/a': 352} | 0 | {'n/a': 352} | 0.84 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [95% CI] | Wins / losses / ties | Sign-test p | Holm p |
|---|---|---|---|---|
| greedy-pask | -0.0109 [-0.0384, +0.0130] | 35 / 41 / 276 | 0.5666 | 1.0000 |
| random-feasible | -0.0138 [-0.0538, +0.0263] | 89 / 91 / 172 | 0.9406 | 1.0000 |
| linear-dfl | +0.1831 [+0.1451, +0.2212] | 149 / 35 / 168 | 0.0000 | 0.0000 |
| mlp-dfl | +0.1629 [+0.1267, +0.2000] | 139 / 32 / 181 | 0.0000 | 0.0000 |
| regression-pckp | +0.1570 [+0.1179, +0.1953] | 143 / 41 / 168 | 0.0000 | 0.0000 |
| topk-repair | +0.1642 [+0.1252, +0.2034] | 148 / 45 / 159 | 0.0000 | 0.0000 |

Held-out pairwise preference accuracy (5351 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.509 |
| linear-dfl | 0.739 |
| mlp-dfl | 0.719 |
| regression-pckp | 0.732 |

Training cost, mean seconds for the final-round fit (includes the l2 grid for the linear model):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.72 | 0.765 |
| mlp-dfl | 0.72 | 0.754 |
| regression-pckp | 0.04 | nan |

Mean per replicate over all training rounds: 2.33 s of model fitting and 2.22 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL, mean test Q; full model 0.7122):

| Removed feature group | Test Q |
|---|---|
| task | 0.6385 |
| graph | 0.6934 |
| provenance | 0.7146 |
| dynamics | 0.7107 |
| outcome | 0.7032 |

| Training fraction | Test Q |
|---|---|
| 0.25 | 0.6766 |
| 0.5 | 0.6972 |
| 1.0 | 0.7122 |
