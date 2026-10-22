### Latency micro-benchmark (descriptive)

`compact()` wall time on 6 instances of about 12 episodes, 30 timed repeats each after warm-up; store construction is excluded. Model weights are random: only cost is measured, not quality.

| Method | Median ms | p10 | p90 |
|---|---|---|---|
| static-pckp | 1.55 | 0.80 | 3.27 |
| greedy-pask | 2.48 | 0.94 | 3.09 |
| linear-dfl | 2.44 | 1.56 | 3.91 |
| mlp-dfl | 2.00 | 1.79 | 2.43 |

Predictor alone (feature extraction plus scoring):

| Model | Median ms | p10 | p90 |
|---|---|---|---|
| linear-dfl | 0.58 | 0.50 | 0.68 |
| mlp-dfl | 0.85 | 0.78 | 1.02 |
