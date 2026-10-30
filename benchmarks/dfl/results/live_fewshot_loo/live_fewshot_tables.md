# Live few-shot adaptation

> MEASURED EVIDENCE: outcomes are real model answers scored by a deterministic verifier on generated tool-call histories.

## Held-out family quality (verifier score, query tasks)

| method | ledger | deploy | triage | mean | worst family |
|---|---|---|---|---|---|
| adapt-listwise-static | 0.824 | 1.000 | 0.889 | 0.904 | 0.824 |
| adapt-listwise-static-v3 | 0.796 | 1.000 | 0.889 | 0.895 | 0.796 |
| adapt-regression-static | 0.690 | 1.000 | 0.806 | 0.832 | 0.690 |
| adapt-loo-listwise-static-v3 | 0.495 | 1.000 | 0.889 | 0.795 | 0.495 |
| adapt-loo-listwise-static-v1 | 0.472 | 1.000 | 0.889 | 0.787 | 0.472 |
| static-pckp | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |
| tuned-static-support | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |

## Change against static on the same task

| method | mean change | worst-family change | negative-transfer | deviation rate |
|---|---|---|---|---|
| adapt-listwise-static | +0.157 | +0.000 | 0.04 | 0.56 |
| adapt-listwise-static-v3 | +0.148 | +0.000 | 0.06 | 0.56 |
| adapt-loo-listwise-static-v1 | +0.040 | +0.000 | 0.04 | 0.29 |
| adapt-loo-listwise-static-v3 | +0.048 | +0.000 | 0.04 | 0.33 |
| adapt-regression-static | +0.085 | +0.000 | 0.00 | 0.26 |
| tuned-static-support | +0.000 | +0.000 | 0.00 | 0.00 |

## Retention scorecard (leave-one-out criticality on the query tasks)

| method | quality | evidence coverage | critical recall | critical missed / total | distraction load | token use |
|---|---|---|---|---|---|---|
| adapt-listwise-static | 0.904 | 0.944 | 0.932 | 23 / 262 | 0.213 | 0.89 |
| adapt-listwise-static-v3 | 0.895 | 0.944 | 0.921 | 27 / 262 | 0.229 | 0.89 |
| adapt-regression-static | 0.832 | 0.954 | 0.895 | 37 / 262 | 0.251 | 0.88 |
| adapt-loo-listwise-static-v3 | 0.795 | 0.880 | 0.878 | 45 / 262 | 0.274 | 0.89 |
| adapt-loo-listwise-static-v1 | 0.787 | 0.870 | 0.863 | 49 / 262 | 0.288 | 0.89 |
| static-pckp | 0.747 | 0.875 | 0.839 | 59 / 262 | 0.320 | 0.89 |
| tuned-static-support | 0.747 | 0.875 | 0.839 | 59 / 262 | 0.320 | 0.89 |

## Paired comparisons (template-clustered bootstrap)

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| adapt-listwise-static vs tuned-static-support | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0070 | 12 |
| adapt-listwise-static vs static-pckp | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0070 | 12 |
| adapt-listwise-static-v3 vs tuned-static-support | +0.148 | [+0.060, +0.247] | 0.0005 | 0.0070 | 12 |
| adapt-listwise-static-v3 vs static-pckp | +0.148 | [+0.060, +0.247] | 0.0005 | 0.0070 | 12 |
| adapt-listwise-static-v3 vs adapt-listwise-static | -0.009 | [-0.023, +0.000] | 0.2249 | 0.2249 | 12 |
| adapt-loo-listwise-static-v1 vs tuned-static-support | +0.040 | [+0.009, +0.079] | 0.0010 | 0.0070 | 12 |
| adapt-loo-listwise-static-v1 vs static-pckp | +0.040 | [+0.009, +0.079] | 0.0010 | 0.0070 | 12 |
| adapt-loo-listwise-static-v1 vs adapt-listwise-static | -0.117 | [-0.218, -0.026] | 0.0150 | 0.0750 | 12 |
| adapt-loo-listwise-static-v3 vs tuned-static-support | +0.048 | [+0.015, +0.083] | 0.0005 | 0.0070 | 12 |
| adapt-loo-listwise-static-v3 vs static-pckp | +0.048 | [+0.015, +0.083] | 0.0005 | 0.0070 | 12 |
| adapt-loo-listwise-static-v3 vs adapt-listwise-static | -0.110 | [-0.201, -0.026] | 0.0150 | 0.0750 | 12 |
| adapt-regression-static vs tuned-static-support | +0.085 | [+0.020, +0.156] | 0.0150 | 0.0750 | 12 |
| adapt-regression-static vs static-pckp | +0.085 | [+0.020, +0.156] | 0.0150 | 0.0750 | 12 |
| adapt-regression-static vs adapt-listwise-static | -0.073 | [-0.114, -0.034] | 0.0005 | 0.0070 | 12 |

## Measurement validity (relaxed budget; positive and negative controls)

- ledger: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
- deploy: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
- triage: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
