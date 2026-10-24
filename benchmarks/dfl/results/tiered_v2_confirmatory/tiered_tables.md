# Three-level retention, model-free semantic check

> MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer quality. No model was called.

| utilities / variant | family | fact survival | all facts kept | tokens used | episodes represented | full / summary | expected fidelity |
|---|---|---|---|---|---|---|---|
| oracle/binary | deploy | 1.000 | 1.000 | 0.88 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | ledger | 0.938 | 0.815 | 0.89 | 4.8 | 4.8 / 0.0 | 1.000 |
| oracle/binary | triage | 1.000 | 1.000 | 0.88 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | all | 0.979 | 0.938 | 0.88 | 4.3 | 4.3 / 0.0 | 1.000 |
| oracle/tiered | deploy | 1.000 | 1.000 | 0.97 | 7.8 | 3.0 / 4.7 | 0.969 |
| oracle/tiered | ledger | 0.997 | 0.991 | 0.97 | 9.2 | 3.4 / 5.8 | 0.964 |
| oracle/tiered | triage | 0.920 | 0.769 | 0.97 | 7.8 | 3.2 / 4.6 | 0.968 |
| oracle/tiered | all | 0.972 | 0.920 | 0.97 | 8.2 | 3.2 / 5.0 | 0.967 |
| static/binary | deploy | 1.000 | 1.000 | 0.88 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | ledger | 0.605 | 0.176 | 0.90 | 4.9 | 4.9 / 0.0 | 1.000 |
| static/binary | triage | 0.997 | 0.991 | 0.88 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | all | 0.867 | 0.722 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| static/tiered | deploy | 1.000 | 1.000 | 0.95 | 8.7 | 2.5 / 6.1 | 0.921 |
| static/tiered | ledger | 0.997 | 0.991 | 0.96 | 9.4 | 3.5 / 6.0 | 0.932 |
| static/tiered | triage | 0.818 | 0.472 | 0.95 | 8.7 | 2.8 / 5.9 | 0.919 |
| static/tiered | all | 0.938 | 0.821 | 0.96 | 8.9 | 2.9 / 6.0 | 0.924 |

## Paired difference, tiered minus binary (template-clustered bootstrap)

| utilities | measure | mean diff | 95% CI | p | clusters |
|---|---|---|---|---|---|
| static | fact_survival | +0.071 | [-0.036, +0.189] | 0.2109 | 18 |
| static | all_facts | +0.099 | [-0.151, +0.361] | 0.4554 | 18 |
| static | represented | +4.599 | [+4.525, +4.673] | 0.0005 | 18 |
| oracle | fact_survival | -0.007 | [-0.035, +0.021] | 0.6383 | 18 |
| oracle | all_facts | -0.019 | [-0.099, +0.062] | 0.7153 | 18 |
| oracle | represented | +3.914 | [+3.704, +4.117] | 0.0005 | 18 |
