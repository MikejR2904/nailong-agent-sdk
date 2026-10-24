# Three-level retention, model-free semantic check

> MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer quality. No model was called.

| utilities / variant | family | fact survival | all facts kept | tokens used | episodes represented | full / summary | expected fidelity |
|---|---|---|---|---|---|---|---|
| oracle/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | ledger | 0.938 | 0.815 | 0.88 | 4.7 | 4.7 / 0.0 | 1.000 |
| oracle/binary | triage | 1.000 | 1.000 | 0.88 | 4.0 | 4.0 / 0.0 | 1.000 |
| oracle/binary | all | 0.979 | 0.938 | 0.88 | 4.3 | 4.3 / 0.0 | 1.000 |
| oracle/tiered | deploy | 1.000 | 1.000 | 0.97 | 7.9 | 3.0 / 4.9 | 0.969 |
| oracle/tiered | ledger | 0.997 | 0.991 | 0.96 | 9.1 | 3.4 / 5.7 | 0.970 |
| oracle/tiered | triage | 0.923 | 0.769 | 0.97 | 7.5 | 3.1 / 4.3 | 0.968 |
| oracle/tiered | all | 0.973 | 0.920 | 0.97 | 8.1 | 3.2 / 5.0 | 0.969 |
| static/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | ledger | 0.602 | 0.130 | 0.90 | 4.8 | 4.8 / 0.0 | 1.000 |
| static/binary | triage | 0.991 | 0.972 | 0.88 | 4.0 | 4.0 / 0.0 | 1.000 |
| static/binary | all | 0.864 | 0.701 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| static/tiered | deploy | 1.000 | 1.000 | 0.94 | 8.8 | 2.5 / 6.3 | 0.920 |
| static/tiered | ledger | 0.994 | 0.981 | 0.95 | 9.3 | 3.3 / 6.0 | 0.936 |
| static/tiered | triage | 0.796 | 0.417 | 0.95 | 8.5 | 2.7 / 5.8 | 0.914 |
| static/tiered | all | 0.930 | 0.799 | 0.95 | 8.9 | 2.8 / 6.0 | 0.923 |

## Paired difference, tiered minus binary (template-clustered bootstrap)

| utilities | measure | mean diff | 95% CI | p | clusters |
|---|---|---|---|---|---|
| static | fact_survival | +0.066 | [-0.041, +0.186] | 0.2479 | 18 |
| static | all_facts | +0.099 | [-0.154, +0.380] | 0.4719 | 18 |
| static | represented | +4.565 | [+4.454, +4.670] | 0.0005 | 18 |
| oracle | fact_survival | -0.006 | [-0.033, +0.022] | 0.7153 | 18 |
| oracle | all_facts | -0.019 | [-0.099, +0.065] | 0.7153 | 18 |
| oracle | represented | +3.867 | [+3.657, +4.096] | 0.0005 | 18 |
