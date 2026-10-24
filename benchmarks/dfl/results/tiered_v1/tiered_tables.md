# Three-level retention, model-free semantic check

> MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer quality. No model was called.

| utilities / variant | family | fact survival | all facts kept | tokens used | episodes represented | full / summary | expected fidelity |
|---|---|---|---|---|---|---|---|
| oracle/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | ledger | 0.938 | 0.815 | 0.88 | 4.7 | 4.7 / 0.0 | 1.000 |
| oracle/binary | triage | 1.000 | 1.000 | 0.88 | 4.0 | 4.0 / 0.0 | 1.000 |
| oracle/binary | all | 0.979 | 0.938 | 0.88 | 4.3 | 4.3 / 0.0 | 1.000 |
| oracle/tiered | deploy | 1.000 | 1.000 | 0.97 | 5.8 | 3.3 / 2.5 | 0.981 |
| oracle/tiered | ledger | 0.972 | 0.917 | 0.97 | 8.2 | 2.5 / 5.6 | 0.961 |
| oracle/tiered | triage | 0.920 | 0.759 | 0.95 | 5.0 | 3.5 / 1.5 | 0.979 |
| oracle/tiered | all | 0.964 | 0.892 | 0.96 | 6.3 | 3.1 / 3.2 | 0.974 |
| static/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | ledger | 0.602 | 0.130 | 0.90 | 4.8 | 4.8 / 0.0 | 1.000 |
| static/binary | triage | 0.991 | 0.972 | 0.88 | 4.0 | 4.0 / 0.0 | 1.000 |
| static/binary | all | 0.864 | 0.701 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| static/tiered | deploy | 0.877 | 0.630 | 0.97 | 8.4 | 0.9 / 7.5 | 0.916 |
| static/tiered | ledger | 0.938 | 0.815 | 0.97 | 9.2 | 1.9 / 7.3 | 0.936 |
| static/tiered | triage | 0.710 | 0.204 | 0.97 | 8.0 | 0.9 / 7.1 | 0.883 |
| static/tiered | all | 0.842 | 0.549 | 0.97 | 8.5 | 1.2 / 7.3 | 0.912 |

## Paired difference, tiered minus binary (template-clustered bootstrap)

| utilities | measure | mean diff | 95% CI | p | clusters |
|---|---|---|---|---|---|
| static | fact_survival | -0.023 | [-0.139, +0.107] | 0.7378 | 18 |
| static | all_facts | -0.151 | [-0.423, +0.151] | 0.3149 | 18 |
| static | represented | +4.231 | [+4.102, +4.373] | 0.0005 | 18 |
| oracle | fact_survival | -0.015 | [-0.039, +0.009] | 0.2414 | 18 |
| oracle | all_facts | -0.046 | [-0.117, +0.028] | 0.2414 | 18 |
| oracle | represented | +2.040 | [+1.596, +2.537] | 0.0005 | 18 |
