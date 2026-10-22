| Other model | Method | Tasks | Score (primary model) | Score (other model) | Mean abs. disagreement | Difference vs static, other model [clustered 95% CI] |
|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | static-pckp | 120 | 0.756 | 0.731 | 0.056 | - |
| deepseek-v4p1-flash | greedy-pask | 120 | 0.748 | 0.722 | 0.050 | -0.008 [-0.029, +0.010] |
| deepseek-v4p1-flash | random-feasible | 120 | 0.546 | 0.510 | 0.047 | -0.220 [-0.309, -0.130] |
| deepseek-v4p1-flash | linear-dfl | 120 | 0.868 | 0.842 | 0.044 | +0.111 [+0.036, +0.197] |
| deepseek-v4p1-flash | mlp-dfl | 120 | 0.903 | 0.889 | 0.034 | +0.158 [+0.090, +0.236] |
| deepseek-v4p1-flash | regression-pckp | 120 | 0.905 | 0.882 | 0.046 | +0.152 [+0.078, +0.236] |
| deepseek-v4p1-flash | topk-repair | 120 | 0.862 | 0.831 | 0.044 | +0.101 [+0.034, +0.173] |
