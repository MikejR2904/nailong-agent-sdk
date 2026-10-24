# Three-level retention, model-free semantic check

> MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer quality. No model was called.

| utilities / variant | family | fact survival | all facts kept | tokens used | episodes represented | full / summary | expected fidelity |
|---|---|---|---|---|---|---|---|
| oracle/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | ledger | 0.948 | 0.843 | 0.88 | 4.8 | 4.8 / 0.0 | 1.000 |
| oracle/binary | triage | 1.000 | 1.000 | 0.89 | 4.0 | 4.0 / 0.0 | 1.000 |
| oracle/binary | all | 0.983 | 0.948 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| oracle/tiered | deploy | 1.000 | 1.000 | 0.97 | 7.0 | 3.5 / 3.6 | 0.969 |
| oracle/tiered | ledger | 1.000 | 1.000 | 0.97 | 9.0 | 3.5 / 5.5 | 0.961 |
| oracle/tiered | triage | 0.923 | 0.769 | 0.98 | 6.7 | 3.6 / 3.1 | 0.959 |
| oracle/tiered | all | 0.974 | 0.923 | 0.97 | 7.6 | 3.5 / 4.1 | 0.963 |
| oracle/tiered-calibrated | deploy | 1.000 | 1.000 | 0.97 | 8.0 | 3.0 / 4.9 | 0.967 |
| oracle/tiered-calibrated | ledger | 1.000 | 1.000 | 0.97 | 9.3 | 3.4 / 5.9 | 0.969 |
| oracle/tiered-calibrated | triage | 0.864 | 0.593 | 0.98 | 7.9 | 3.1 / 4.9 | 0.952 |
| oracle/tiered-calibrated | all | 0.955 | 0.864 | 0.97 | 8.4 | 3.2 / 5.2 | 0.963 |
| static/binary | deploy | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | ledger | 0.617 | 0.176 | 0.89 | 4.9 | 4.9 / 0.0 | 1.000 |
| static/binary | triage | 0.994 | 0.981 | 0.89 | 4.0 | 4.0 / 0.0 | 1.000 |
| static/binary | all | 0.870 | 0.719 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| static/tiered | deploy | 0.994 | 0.981 | 0.96 | 8.5 | 2.8 / 5.7 | 0.857 |
| static/tiered | ledger | 1.000 | 1.000 | 0.96 | 9.4 | 3.5 / 6.0 | 0.921 |
| static/tiered | triage | 0.802 | 0.426 | 0.95 | 8.5 | 2.8 / 5.7 | 0.881 |
| static/tiered | all | 0.932 | 0.802 | 0.96 | 8.8 | 3.0 / 5.8 | 0.886 |
| static/tiered-calibrated | deploy | 1.000 | 1.000 | 0.94 | 8.8 | 2.5 / 6.2 | 0.919 |
| static/tiered-calibrated | ledger | 1.000 | 1.000 | 0.96 | 9.5 | 3.5 / 6.0 | 0.946 |
| static/tiered-calibrated | triage | 0.935 | 0.806 | 0.94 | 8.7 | 2.7 / 6.0 | 0.925 |
| static/tiered-calibrated | all | 0.978 | 0.935 | 0.95 | 9.0 | 2.9 / 6.1 | 0.930 |

## Paired difference, tiered minus binary (template-clustered bootstrap)

| utilities | measure | mean diff | 95% CI | p | clusters |
|---|---|---|---|---|---|
| static / tiered | fact_survival | +0.062 | [-0.045, +0.180] | 0.2724 | 18 |
| static / tiered | all_facts | +0.083 | [-0.176, +0.361] | 0.5364 | 18 |
| static / tiered | represented | +4.460 | [+4.336, +4.586] | 0.0005 | 18 |
| static / tiered-calibrated | fact_survival | +0.108 | [+0.021, +0.206] | 0.0135 | 18 |
| static / tiered-calibrated | all_facts | +0.216 | [+0.025, +0.435] | 0.0270 | 18 |
| static / tiered-calibrated | represented | +4.630 | [+4.515, +4.738] | 0.0005 | 18 |
| oracle / tiered | fact_survival | -0.008 | [-0.035, +0.019] | 0.5824 | 18 |
| oracle / tiered | all_facts | -0.025 | [-0.105, +0.056] | 0.5939 | 18 |
| oracle / tiered | represented | +3.244 | [+2.910, +3.599] | 0.0005 | 18 |
| oracle / tiered-calibrated | fact_survival | -0.028 | [-0.067, +0.010] | 0.1690 | 18 |
| oracle / tiered-calibrated | all_facts | -0.083 | [-0.201, +0.031] | 0.1790 | 18 |
| oracle / tiered-calibrated | represented | +4.065 | [+3.910, +4.238] | 0.0005 | 18 |
