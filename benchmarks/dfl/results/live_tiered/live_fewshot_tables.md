# Live few-shot adaptation

> MEASURED EVIDENCE: outcomes are real model answers scored by a deterministic verifier on generated tool-call histories.

## Held-out family quality (verifier score, query tasks)

| method | ledger | deploy | triage | mean | worst family |
|---|---|---|---|---|---|
| adapt-listwise-static | 0.824 | 1.000 | 0.889 | 0.904 | 0.824 |
| tiered-adapt-listwise | 0.894 | 0.907 | 0.759 | 0.853 | 0.759 |
| adapt-regression-static | 0.690 | 1.000 | 0.806 | 0.832 | 0.690 |
| tiered-static | 0.856 | 0.907 | 0.704 | 0.823 | 0.704 |
| static-pckp | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |
| tuned-static-support | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |

## Change against static on the same task

| method | mean change | worst-family change | negative-transfer | deviation rate |
|---|---|---|---|---|
| adapt-listwise-static | +0.157 | +0.000 | 0.04 | 0.56 |
| adapt-regression-static | +0.085 | +0.000 | 0.00 | 0.26 |
| tiered-adapt-listwise | +0.106 | -0.093 | 0.19 | 1.00 |
| tiered-static | +0.076 | -0.102 | 0.24 | 1.00 |
| tuned-static-support | +0.000 | +0.000 | 0.00 | 0.00 |

## Paired comparisons (template-clustered bootstrap)

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| adapt-listwise-static vs tuned-static-support | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0055 | 12 |
| adapt-listwise-static vs static-pckp | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0055 | 12 |
| adapt-regression-static vs tuned-static-support | +0.085 | [+0.020, +0.156] | 0.0150 | 0.1200 | 12 |
| adapt-regression-static vs static-pckp | +0.085 | [+0.020, +0.156] | 0.0150 | 0.1200 | 12 |
| adapt-regression-static vs adapt-listwise-static | -0.073 | [-0.114, -0.034] | 0.0005 | 0.0055 | 12 |
| tiered-adapt-listwise vs tuned-static-support | +0.106 | [-0.029, +0.255] | 0.1340 | 0.6323 | 12 |
| tiered-adapt-listwise vs static-pckp | +0.106 | [-0.029, +0.255] | 0.1340 | 0.6323 | 12 |
| tiered-adapt-listwise vs adapt-listwise-static | -0.051 | [-0.113, +0.019] | 0.1265 | 0.6323 | 12 |
| tiered-static vs tuned-static-support | +0.076 | [-0.060, +0.224] | 0.3274 | 0.6548 | 12 |
| tiered-static vs static-pckp | +0.076 | [-0.060, +0.224] | 0.3274 | 0.6548 | 12 |
| tiered-static vs adapt-listwise-static | -0.082 | [-0.148, -0.008] | 0.0330 | 0.1980 | 12 |

## Measurement validity (relaxed budget; positive and negative controls)

- ledger: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24, 'all_summaries_live_score': 0.8472222222222222, 'all_summaries_fact_survival_proxy': 1.0}
- deploy: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24, 'all_summaries_live_score': 0.75, 'all_summaries_fact_survival_proxy': 1.0}
- triage: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24, 'all_summaries_live_score': 0.3194444444444444, 'all_summaries_fact_survival_proxy': 0.5416666666666666}
