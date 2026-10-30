### Regime `aligned`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.649 [0.617, 0.681] | 0.651 [0.620, 0.685] | 0.238 [0.209, 0.267] | 0.672 [0.621, 0.722] | 0.785 [0.761, 0.810] | 0.964 [0.961, 0.967] |
| greedy-pask | 0.627 [0.595, 0.661] | 0.631 [0.598, 0.666] | 0.260 [0.229, 0.291] | 0.666 [0.617, 0.716] | 0.765 [0.738, 0.791] | 0.927 [0.921, 0.933] |
| random-feasible | 0.470 [0.437, 0.501] | 0.473 [0.440, 0.506] | 0.417 [0.385, 0.450] | 0.535 [0.485, 0.587] | 0.635 [0.606, 0.663] | 0.940 [0.935, 0.945] |
| linear-dfl | 0.704 [0.673, 0.735] | 0.707 [0.677, 0.738] | 0.184 [0.155, 0.209] | 0.718 [0.668, 0.765] | 0.827 [0.805, 0.850] | 0.943 [0.936, 0.949] |
| mlp-dfl | 0.677 [0.645, 0.709] | 0.678 [0.646, 0.711] | 0.210 [0.181, 0.238] | 0.718 [0.670, 0.766] | 0.804 [0.780, 0.829] | 0.935 [0.927, 0.942] |
| regression-pckp | 0.673 [0.642, 0.705] | 0.673 [0.642, 0.705] | 0.214 [0.185, 0.243] | 0.705 [0.656, 0.752] | 0.801 [0.777, 0.826] | 0.955 [0.951, 0.960] |
| topk-repair | 0.680 [0.649, 0.712] | 0.683 [0.651, 0.714] | 0.207 [0.178, 0.236] | 0.697 [0.648, 0.745] | 0.807 [0.783, 0.832] | 0.935 [0.928, 0.942] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 350} | 0 | {'n/a': 350} | 1.71 |
| greedy-pask | {'n/a': 350} | 0 | {'n/a': 350} | 2.67 |
| random-feasible | {'n/a': 350} | 0 | {'n/a': 350} | 0.21 |
| linear-dfl | {'optimal': 350} | 0 | {'applied': 350} | 2.53 |
| mlp-dfl | {'optimal': 350} | 0 | {'applied': 350} | 2.69 |
| regression-pckp | {'optimal': 350} | 0 | {'applied': 350} | 2.53 |
| topk-repair | {'n/a': 350} | 0 | {'n/a': 350} | 1.06 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0217 [-0.0451, +0.0020] | [-0.0454, +0.0024] | 0.0710 (0.2129) | 26 / 43 / 281 | 0.0533 (0.1802) |
| random-feasible | -0.1792 [-0.2177, -0.1393] | [-0.2190, -0.1418] | 0.0005 (0.0030) | 35 / 146 / 169 | 0.0000 (0.0000) |
| linear-dfl | +0.0544 [+0.0270, +0.0816] | [+0.0270, +0.0815] | 0.0005 (0.0030) | 70 / 29 / 251 | 0.0000 (0.0002) |
| mlp-dfl | +0.0278 [-0.0005, +0.0579] | [-0.0083, +0.0622] | 0.1385 (0.2769) | 64 / 46 / 240 | 0.1046 (0.1802) |
| regression-pckp | +0.0240 [-0.0064, +0.0550] | [-0.0088, +0.0575] | 0.1615 (0.2769) | 68 / 48 / 234 | 0.0773 (0.1802) |
| topk-repair | +0.0314 [-0.0009, +0.0637] | [-0.0001, +0.0625] | 0.0520 (0.2079) | 72 / 49 / 229 | 0.0451 (0.1802) |

Paired difference in regret against `static-pckp` (negative is better, clustered):

| Method | Mean regret difference [95% CI] |
|---|---|
| greedy-pask | +0.0217 [-0.0023, +0.0455] |
| random-feasible | +0.1792 [+0.1419, +0.2191] |
| linear-dfl | -0.0544 [-0.0814, -0.0270] |
| mlp-dfl | -0.0278 [-0.0622, +0.0084] |
| regression-pckp | -0.0240 [-0.0574, +0.0089] |
| topk-repair | -0.0314 [-0.0625, +0.0002] |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0267 [-0.0054, +0.0613] | 0.1045 | 0.2089 |
| linear-dfl - regression-pckp | +0.0304 [+0.0011, +0.0614] | 0.0435 | 0.1305 |
| linear-dfl - topk-repair | +0.0230 [+0.0043, +0.0410] | 0.0215 | 0.0860 |
| mlp-dfl - regression-pckp | +0.0038 [-0.0358, +0.0422] | 0.8428 | 0.8428 |

Held-out pairwise preference accuracy (5403 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.720 |
| linear-dfl | 0.757 |
| mlp-dfl | 0.737 |
| regression-pckp | 0.739 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.76 | 0.755 |
| mlp-dfl | 3.99 | 0.765 |
| regression-pckp | 0.05 | nan |

Mean per replicate over all training rounds: 8.49 s of model fitting and 2.68 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL; mean test Q, full model 0.7034; difference from the full model with a 95% bootstrap interval over 20 replicates):

| Removed feature group | Test Q | Difference from full |
|---|---|---|
| task | 0.4973 | -0.2062 [-0.2413, -0.1670] |
| graph | 0.6932 | -0.0103 [-0.0251, +0.0048] |
| provenance | 0.6890 | -0.0144 [-0.0285, +0.0003] |
| dynamics | 0.7054 | +0.0020 [-0.0167, +0.0179] |
| outcome | 0.6976 | -0.0058 [-0.0214, +0.0069] |

| Training fraction | Test Q | Difference from full |
|---|---|---|
| 0.25 | 0.6597 | -0.0437 [-0.0774, -0.0116] |
| 0.5 | 0.6745 | -0.0289 [-0.0460, -0.0111] |
| 1.0 | 0.7034 | +0.0000 [+0.0000, +0.0000] |

### Regime `shifted`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.526 [0.493, 0.560] | 0.528 [0.494, 0.564] | 0.381 [0.350, 0.414] | 0.526 [0.479, 0.572] | 0.683 [0.654, 0.712] | 0.965 [0.962, 0.969] |
| greedy-pask | 0.540 [0.505, 0.574] | 0.540 [0.505, 0.575] | 0.368 [0.336, 0.400] | 0.548 [0.498, 0.594] | 0.694 [0.665, 0.723] | 0.930 [0.924, 0.935] |
| random-feasible | 0.520 [0.486, 0.552] | 0.521 [0.487, 0.554] | 0.387 [0.356, 0.420] | 0.552 [0.505, 0.599] | 0.678 [0.649, 0.705] | 0.938 [0.933, 0.943] |
| linear-dfl | 0.735 [0.705, 0.762] | 0.735 [0.704, 0.762] | 0.172 [0.146, 0.200] | 0.833 [0.800, 0.867] | 0.848 [0.826, 0.869] | 0.936 [0.929, 0.941] |
| mlp-dfl | 0.723 [0.692, 0.753] | 0.723 [0.691, 0.753] | 0.184 [0.157, 0.212] | 0.813 [0.773, 0.847] | 0.838 [0.813, 0.860] | 0.942 [0.936, 0.947] |
| regression-pckp | 0.728 [0.695, 0.758] | 0.730 [0.698, 0.760] | 0.180 [0.152, 0.209] | 0.780 [0.741, 0.817] | 0.841 [0.817, 0.864] | 0.945 [0.940, 0.950] |
| topk-repair | 0.715 [0.685, 0.745] | 0.716 [0.685, 0.745] | 0.192 [0.165, 0.220] | 0.827 [0.792, 0.861] | 0.834 [0.810, 0.856] | 0.930 [0.924, 0.936] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 353} | 0 | {'n/a': 353} | 1.58 |
| greedy-pask | {'n/a': 353} | 0 | {'n/a': 353} | 2.45 |
| random-feasible | {'n/a': 353} | 0 | {'n/a': 353} | 0.20 |
| linear-dfl | {'optimal': 353} | 0 | {'applied': 353} | 2.38 |
| mlp-dfl | {'optimal': 353} | 0 | {'applied': 353} | 2.52 |
| regression-pckp | {'optimal': 353} | 0 | {'applied': 353} | 2.32 |
| topk-repair | {'n/a': 353} | 0 | {'n/a': 353} | 1.02 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | +0.0137 [-0.0098, +0.0365] | [-0.0091, +0.0386] | 0.2434 (0.4869) | 44 / 37 / 272 | 0.5052 (1.0000) |
| random-feasible | -0.0060 [-0.0433, +0.0325] | [-0.0421, +0.0315] | 0.7563 (0.7563) | 92 / 90 / 171 | 0.9409 (1.0000) |
| linear-dfl | +0.2089 [+0.1699, +0.2485] | [+0.1731, +0.2467] | 0.0005 (0.0030) | 161 / 34 / 158 | 0.0000 (0.0000) |
| mlp-dfl | +0.1974 [+0.1613, +0.2337] | [+0.1623, +0.2327] | 0.0005 (0.0030) | 150 / 27 / 176 | 0.0000 (0.0000) |
| regression-pckp | +0.2017 [+0.1638, +0.2365] | [+0.1646, +0.2409] | 0.0005 (0.0030) | 154 / 28 / 171 | 0.0000 (0.0000) |
| topk-repair | +0.1895 [+0.1492, +0.2291] | [+0.1515, +0.2299] | 0.0005 (0.0030) | 153 / 43 / 157 | 0.0000 (0.0000) |

Paired difference in regret against `static-pckp` (negative is better, clustered):

| Method | Mean regret difference [95% CI] |
|---|---|
| greedy-pask | -0.0137 [-0.0384, +0.0091] |
| random-feasible | +0.0060 [-0.0314, +0.0423] |
| linear-dfl | -0.2089 [-0.2466, -0.1729] |
| mlp-dfl | -0.1974 [-0.2326, -0.1622] |
| regression-pckp | -0.2017 [-0.2407, -0.1645] |
| topk-repair | -0.1895 [-0.2299, -0.1515] |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0115 [-0.0079, +0.0320] | 0.2554 | 0.7663 |
| linear-dfl - regression-pckp | +0.0072 [-0.0159, +0.0307] | 0.5439 | 1.0000 |
| linear-dfl - topk-repair | +0.0195 [+0.0031, +0.0365] | 0.0180 | 0.0720 |
| mlp-dfl - regression-pckp | -0.0043 [-0.0268, +0.0172] | 0.7088 | 1.0000 |

Held-out pairwise preference accuracy (5117 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.508 |
| linear-dfl | 0.764 |
| mlp-dfl | 0.753 |
| regression-pckp | 0.754 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.91 | 0.807 |
| mlp-dfl | 4.36 | 0.812 |
| regression-pckp | 0.04 | nan |

Mean per replicate over all training rounds: 9.12 s of model fitting and 2.63 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL; mean test Q, full model 0.7344; difference from the full model with a 95% bootstrap interval over 20 replicates):

| Removed feature group | Test Q | Difference from full |
|---|---|---|
| task | 0.6636 | -0.0707 [-0.1004, -0.0424] |
| graph | 0.7319 | -0.0025 [-0.0183, +0.0134] |
| provenance | 0.7393 | +0.0050 [-0.0138, +0.0235] |
| dynamics | 0.7353 | +0.0009 [-0.0144, +0.0169] |
| outcome | 0.7283 | -0.0060 [-0.0236, +0.0108] |

| Training fraction | Test Q | Difference from full |
|---|---|---|
| 0.25 | 0.6722 | -0.0622 [-0.0890, -0.0358] |
| 0.5 | 0.7135 | -0.0208 [-0.0410, +0.0007] |
| 1.0 | 0.7344 | +0.0000 [+0.0000, +0.0000] |

### Regime `null`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 54 train / 12 validation / 17 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.559 [0.526, 0.591] | 0.557 [0.522, 0.590] | 0.330 [0.299, 0.362] | 0.630 [0.582, 0.677] | 0.713 [0.686, 0.739] | 0.962 [0.958, 0.966] |
| greedy-pask | 0.551 [0.519, 0.582] | 0.550 [0.516, 0.582] | 0.338 [0.307, 0.369] | 0.616 [0.563, 0.665] | 0.712 [0.685, 0.736] | 0.928 [0.922, 0.934] |
| random-feasible | 0.457 [0.425, 0.489] | 0.455 [0.424, 0.488] | 0.432 [0.399, 0.465] | 0.490 [0.436, 0.542] | 0.623 [0.595, 0.652] | 0.945 [0.940, 0.949] |
| linear-dfl | 0.541 [0.511, 0.573] | 0.536 [0.504, 0.569] | 0.348 [0.316, 0.378] | 0.605 [0.555, 0.657] | 0.699 [0.673, 0.725] | 0.959 [0.954, 0.963] |
| mlp-dfl | 0.537 [0.504, 0.570] | 0.533 [0.500, 0.568] | 0.352 [0.321, 0.385] | 0.628 [0.581, 0.679] | 0.682 [0.652, 0.712] | 0.919 [0.899, 0.937] |
| regression-pckp | 0.527 [0.494, 0.559] | 0.525 [0.491, 0.558] | 0.362 [0.330, 0.395] | 0.617 [0.564, 0.667] | 0.684 [0.657, 0.711] | 0.961 [0.956, 0.965] |
| topk-repair | 0.474 [0.442, 0.507] | 0.473 [0.440, 0.507] | 0.415 [0.382, 0.448] | 0.494 [0.441, 0.547] | 0.635 [0.607, 0.664] | 0.943 [0.937, 0.947] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 351} | 0 | {'n/a': 351} | 1.88 |
| greedy-pask | {'n/a': 351} | 0 | {'n/a': 351} | 2.97 |
| random-feasible | {'n/a': 351} | 0 | {'n/a': 351} | 0.24 |
| linear-dfl | {'optimal': 351} | 0 | {'applied': 351} | 2.99 |
| mlp-dfl | {'optimal': 351} | 0 | {'applied': 351} | 3.04 |
| regression-pckp | {'optimal': 351} | 0 | {'applied': 351} | 2.90 |
| topk-repair | {'n/a': 351} | 0 | {'n/a': 351} | 1.24 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0076 [-0.0307, +0.0154] | [-0.0329, +0.0185] | 0.5614 (0.9523) | 38 / 37 / 276 | 1.0000 (1.0000) |
| random-feasible | -0.1020 [-0.1392, -0.0596] | [-0.1436, -0.0612] | 0.0005 (0.0030) | 58 / 115 / 178 | 0.0000 (0.0001) |
| linear-dfl | -0.0177 [-0.0483, +0.0145] | [-0.0533, +0.0170] | 0.3174 (0.9523) | 60 / 62 / 229 | 0.9279 (1.0000) |
| mlp-dfl | -0.0220 [-0.0540, +0.0096] | [-0.0719, +0.0227] | 0.3534 (0.9523) | 56 / 65 / 230 | 0.4672 (1.0000) |
| regression-pckp | -0.0320 [-0.0651, +0.0001] | [-0.0662, +0.0015] | 0.0620 (0.2479) | 57 / 78 / 216 | 0.0848 (0.3393) |
| topk-repair | -0.0846 [-0.1204, -0.0471] | [-0.1204, -0.0456] | 0.0005 (0.0030) | 62 / 112 / 177 | 0.0002 (0.0009) |

Paired difference in regret against `static-pckp` (negative is better, clustered):

| Method | Mean regret difference [95% CI] |
|---|---|
| greedy-pask | +0.0076 [-0.0185, +0.0329] |
| random-feasible | +0.1020 [+0.0612, +0.1436] |
| linear-dfl | +0.0177 [-0.0170, +0.0533] |
| mlp-dfl | +0.0220 [-0.0227, +0.0720] |
| regression-pckp | +0.0320 [-0.0013, +0.0662] |
| topk-repair | +0.0846 [+0.0456, +0.1205] |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0042 [-0.0309, +0.0433] | 0.8533 | 1.0000 |
| linear-dfl - regression-pckp | +0.0143 [-0.0217, +0.0514] | 0.4394 | 1.0000 |
| linear-dfl - topk-repair | +0.0669 [+0.0371, +0.0976] | 0.0005 | 0.0020 |
| mlp-dfl - regression-pckp | +0.0100 [-0.0351, +0.0537] | 0.6438 | 1.0000 |

Held-out pairwise preference accuracy (4818 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.589 |
| linear-dfl | 0.599 |
| mlp-dfl | 0.581 |
| regression-pckp | 0.605 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 1.15 | 0.613 |
| mlp-dfl | 3.35 | 0.657 |
| regression-pckp | 0.05 | nan |

Mean per replicate over all training rounds: 7.41 s of model fitting and 3.25 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL; mean test Q, full model 0.5397; difference from the full model with a 95% bootstrap interval over 20 replicates):

| Removed feature group | Test Q | Difference from full |
|---|---|---|
| task | 0.5430 | +0.0033 [-0.0153, +0.0210] |
| graph | 0.5541 | +0.0144 [-0.0079, +0.0392] |
| provenance | 0.5301 | -0.0096 [-0.0293, +0.0080] |
| dynamics | 0.5443 | +0.0046 [-0.0250, +0.0324] |
| outcome | 0.5492 | +0.0095 [-0.0016, +0.0216] |

| Training fraction | Test Q | Difference from full |
|---|---|---|
| 0.25 | 0.4977 | -0.0420 [-0.0707, -0.0121] |
| 0.5 | 0.5110 | -0.0287 [-0.0471, -0.0107] |
| 1.0 | 0.5397 | +0.0000 [+0.0000, +0.0000] |

### Regime `additive`

> SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. They check that the solver and the learning pipeline behave as the mathematics says; they are not downstream agent performance and must not be quoted as such.

20 replicates; per replicate about 54 train / 12 validation / 18 test instances (template-disjoint). Intervals are 95% bootstrap over test instances pooled across replicates.

| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | Failure-critical recall | Evidence coverage | Tokens / budget |
|---|---|---|---|---|---|---|
| static-pckp | 0.674 [0.649, 0.698] | 0.677 [0.652, 0.701] | 0.194 [0.172, 0.217] | 0.622 [0.560, 0.684] | 0.700 [0.673, 0.726] | 0.960 [0.956, 0.963] |
| greedy-pask | 0.657 [0.631, 0.682] | 0.662 [0.635, 0.687] | 0.211 [0.188, 0.234] | 0.632 [0.570, 0.693] | 0.682 [0.653, 0.709] | 0.924 [0.918, 0.930] |
| random-feasible | 0.529 [0.501, 0.558] | 0.524 [0.496, 0.553] | 0.339 [0.312, 0.366] | 0.521 [0.460, 0.580] | 0.542 [0.512, 0.573] | 0.941 [0.935, 0.946] |
| linear-dfl | 0.714 [0.691, 0.737] | 0.714 [0.690, 0.737] | 0.154 [0.134, 0.177] | 0.682 [0.622, 0.742] | 0.744 [0.718, 0.768] | 0.934 [0.926, 0.941] |
| mlp-dfl | 0.670 [0.642, 0.697] | 0.671 [0.642, 0.700] | 0.198 [0.173, 0.225] | 0.655 [0.592, 0.709] | 0.696 [0.665, 0.725] | 0.900 [0.882, 0.917] |
| regression-pckp | 0.696 [0.671, 0.720] | 0.696 [0.671, 0.721] | 0.172 [0.150, 0.195] | 0.702 [0.646, 0.755] | 0.724 [0.697, 0.750] | 0.955 [0.950, 0.959] |
| topk-repair | 0.703 [0.678, 0.726] | 0.704 [0.679, 0.727] | 0.165 [0.143, 0.189] | 0.680 [0.624, 0.738] | 0.731 [0.705, 0.757] | 0.928 [0.920, 0.935] |

Solver certificates, learned-policy status and latency (mean ms per selection):

| Method | Solver status | Non-zero gaps | Learned status | Latency ms |
|---|---|---|---|---|
| static-pckp | {'optimal': 352} | 0 | {'n/a': 352} | 1.65 |
| greedy-pask | {'n/a': 352} | 0 | {'n/a': 352} | 2.54 |
| random-feasible | {'n/a': 352} | 0 | {'n/a': 352} | 0.20 |
| linear-dfl | {'optimal': 352} | 0 | {'applied': 352} | 2.45 |
| mlp-dfl | {'optimal': 352} | 0 | {'applied': 352} | 2.58 |
| regression-pckp | {'optimal': 352} | 0 | {'applied': 352} | 2.45 |
| topk-repair | {'n/a': 352} | 0 | {'n/a': 352} | 1.03 |

Paired difference in Q against `static-pckp` (same test instances):

| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p (Holm) | Wins / losses / ties | Sign-test p (Holm) |
|---|---|---|---|---|---|
| greedy-pask | -0.0166 [-0.0335, +0.0004] | [-0.0339, +0.0017] | 0.0775 (0.2324) | 28 / 43 / 281 | 0.0959 (0.1918) |
| random-feasible | -0.1448 [-0.1758, -0.1133] | [-0.1715, -0.1150] | 0.0005 (0.0030) | 40 / 160 / 152 | 0.0000 (0.0000) |
| linear-dfl | +0.0405 [+0.0163, +0.0638] | [+0.0192, +0.0629] | 0.0015 (0.0075) | 83 / 44 / 225 | 0.0007 (0.0034) |
| mlp-dfl | -0.0033 [-0.0322, +0.0248] | [-0.0524, +0.0390] | 0.9408 (0.9408) | 77 / 61 / 214 | 0.2015 (0.2015) |
| regression-pckp | +0.0224 [-0.0026, +0.0484] | [-0.0044, +0.0473] | 0.0915 (0.2324) | 76 / 51 / 225 | 0.0328 (0.0984) |
| topk-repair | +0.0292 [+0.0044, +0.0531] | [+0.0066, +0.0529] | 0.0110 (0.0440) | 85 / 56 / 211 | 0.0181 (0.0722) |

Paired difference in regret against `static-pckp` (negative is better, clustered):

| Method | Mean regret difference [95% CI] |
|---|---|
| greedy-pask | +0.0166 [-0.0017, +0.0339] |
| random-feasible | +0.1448 [+0.1151, +0.1716] |
| linear-dfl | -0.0405 [-0.0629, -0.0191] |
| mlp-dfl | +0.0033 [-0.0388, +0.0526] |
| regression-pckp | -0.0224 [-0.0473, +0.0044] |
| topk-repair | -0.0292 [-0.0529, -0.0066] |

Paired difference in Q between learned methods (template-clustered, Holm over these):

| Comparison | Mean difference [95% CI] | Clustered p | Holm p |
|---|---|---|---|
| linear-dfl - mlp-dfl | +0.0438 [+0.0037, +0.0906] | 0.0295 | 0.1180 |
| linear-dfl - regression-pckp | +0.0181 [-0.0052, +0.0409] | 0.1295 | 0.3884 |
| linear-dfl - topk-repair | +0.0113 [-0.0065, +0.0287] | 0.2194 | 0.4389 |
| mlp-dfl - regression-pckp | -0.0257 [-0.0780, +0.0203] | 0.2934 | 0.4389 |

Held-out pairwise preference accuracy (4095 significance-filtered pairs pooled; 0.5 is chance):

| Scorer | Accuracy |
|---|---|
| static-pckp | 0.727 |
| linear-dfl | 0.774 |
| mlp-dfl | 0.749 |
| regression-pckp | 0.759 |

Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):

| Model | Seconds | Validation pair accuracy |
|---|---|---|
| linear-dfl | 0.78 | 0.800 |
| mlp-dfl | 3.86 | 0.818 |
| regression-pckp | 0.05 | nan |

Mean per replicate over all training rounds: 7.92 s of model fitting and 2.59 s of candidate generation and outcome evaluation.

Ablations (linear Ranking-DFL; mean test Q, full model 0.7141; difference from the full model with a 95% bootstrap interval over 20 replicates):

| Removed feature group | Test Q | Difference from full |
|---|---|---|
| task | 0.5372 | -0.1770 [-0.2060, -0.1493] |
| graph | 0.7235 | +0.0093 [-0.0004, +0.0196] |
| provenance | 0.7232 | +0.0091 [-0.0049, +0.0227] |
| dynamics | 0.7313 | +0.0172 [+0.0058, +0.0289] |
| outcome | 0.7194 | +0.0053 [-0.0035, +0.0148] |

| Training fraction | Test Q | Difference from full |
|---|---|---|
| 0.25 | 0.6753 | -0.0389 [-0.0626, -0.0163] |
| 0.5 | 0.7002 | -0.0139 [-0.0317, +0.0038] |
| 1.0 | 0.7141 | +0.0000 [+0.0000, +0.0000] |

### Pre-registered primary hypotheses

| Cell | Mean difference in Q | Clustered 95% CI | Clustered p | Holm p | Supported |
|---|---|---|---|---|---|
| shifted/linear-dfl | +0.2089 | [+0.1731, +0.2467] | 0.0005 | 0.0020 | yes |
| shifted/mlp-dfl | +0.1974 | [+0.1623, +0.2327] | 0.0005 | 0.0020 | yes |
| aligned/linear-dfl | +0.0544 | [+0.0270, +0.0815] | 0.0005 | 0.0020 | yes |
| aligned/mlp-dfl | +0.0278 | [-0.0083, +0.0622] | 0.1385 | 0.1385 | no |
