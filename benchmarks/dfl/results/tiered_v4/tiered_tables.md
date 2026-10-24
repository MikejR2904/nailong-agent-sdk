# Three-level retention, model-free semantic check

> MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer quality. No model was called.

| utilities / variant | family | fact survival | all facts kept | tokens used | episodes represented | full / summary | expected fidelity |
|---|---|---|---|---|---|---|---|
| oracle/binary | deploy | 1.000 | 1.000 | 0.88 | 4.2 | 4.2 / 0.0 | 1.000 |
| oracle/binary | ledger | 0.948 | 0.843 | 0.89 | 4.6 | 4.6 / 0.0 | 1.000 |
| oracle/binary | triage | 1.000 | 1.000 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| oracle/binary | all | 0.983 | 0.948 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| oracle/tiered | deploy | 1.000 | 1.000 | 0.97 | 7.2 | 3.5 / 3.7 | 0.964 |
| oracle/tiered | ledger | 1.000 | 1.000 | 0.97 | 8.8 | 3.4 / 5.4 | 0.939 |
| oracle/tiered | triage | 0.985 | 0.954 | 0.98 | 7.0 | 3.6 / 3.4 | 0.949 |
| oracle/tiered | all | 0.995 | 0.985 | 0.97 | 7.7 | 3.5 / 4.2 | 0.951 |
| oracle/tiered-calibrated | deploy | 1.000 | 1.000 | 0.97 | 7.9 | 3.2 / 4.8 | 0.968 |
| oracle/tiered-calibrated | ledger | 1.000 | 1.000 | 0.96 | 9.1 | 3.3 / 5.8 | 0.952 |
| oracle/tiered-calibrated | triage | 0.926 | 0.778 | 0.97 | 8.1 | 3.1 / 5.0 | 0.951 |
| oracle/tiered-calibrated | all | 0.975 | 0.926 | 0.97 | 8.4 | 3.2 / 5.2 | 0.957 |
| static/binary | deploy | 1.000 | 1.000 | 0.88 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | ledger | 0.617 | 0.148 | 0.90 | 4.7 | 4.7 / 0.0 | 1.000 |
| static/binary | triage | 0.997 | 0.991 | 0.89 | 4.1 | 4.1 / 0.0 | 1.000 |
| static/binary | all | 0.871 | 0.713 | 0.89 | 4.3 | 4.3 / 0.0 | 1.000 |
| static/tiered | deploy | 1.000 | 1.000 | 0.96 | 8.5 | 2.8 / 5.7 | 0.856 |
| static/tiered | ledger | 0.991 | 0.972 | 0.96 | 9.2 | 3.2 / 6.0 | 0.914 |
| static/tiered | triage | 0.821 | 0.481 | 0.95 | 8.6 | 2.8 / 5.8 | 0.874 |
| static/tiered | all | 0.937 | 0.818 | 0.96 | 8.8 | 3.0 / 5.9 | 0.881 |
| static/tiered-calibrated | deploy | 1.000 | 1.000 | 0.94 | 8.7 | 2.7 / 6.1 | 0.923 |
| static/tiered-calibrated | ledger | 0.997 | 0.991 | 0.96 | 9.3 | 3.3 / 6.0 | 0.943 |
| static/tiered-calibrated | triage | 0.935 | 0.806 | 0.94 | 8.8 | 2.8 / 6.0 | 0.924 |
| static/tiered-calibrated | all | 0.977 | 0.932 | 0.95 | 8.9 | 2.9 / 6.0 | 0.930 |

## Paired difference, tiered minus binary (template-clustered bootstrap)

| utilities | measure | mean diff | 95% CI | p | clusters |
|---|---|---|---|---|---|
| static / tiered | fact_survival | +0.066 | [-0.034, +0.177] | 0.2214 | 18 |
| static / tiered | all_facts | +0.105 | [-0.136, +0.373] | 0.4169 | 18 |
| static / tiered | represented | +4.515 | [+4.429, +4.608] | 0.0005 | 18 |
| static / tiered-calibrated | fact_survival | +0.106 | [+0.020, +0.204] | 0.0145 | 18 |
| static / tiered-calibrated | all_facts | +0.219 | [+0.025, +0.444] | 0.0300 | 18 |
| static / tiered-calibrated | represented | +4.642 | [+4.556, +4.722] | 0.0005 | 18 |
| oracle / tiered | fact_survival | +0.012 | [-0.004, +0.031] | 0.1765 | 18 |
| oracle / tiered | all_facts | +0.037 | [-0.012, +0.093] | 0.1765 | 18 |
| oracle / tiered | represented | +3.377 | [+3.077, +3.694] | 0.0005 | 18 |
| oracle / tiered-calibrated | fact_survival | -0.007 | [-0.035, +0.021] | 0.6573 | 18 |
| oracle / tiered-calibrated | all_facts | -0.022 | [-0.105, +0.062] | 0.6648 | 18 |
| oracle / tiered-calibrated | represented | +4.086 | [+3.941, +4.247] | 0.0005 | 18 |
