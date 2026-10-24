# Live few-shot adaptation

> MEASURED EVIDENCE: outcomes are real model answers scored by a deterministic verifier on generated tool-call histories.

## Held-out family quality (verifier score, query tasks)

| method | ledger | deploy | triage | mean | worst family |
|---|---|---|---|---|---|
| adapt-listwise-static | 0.824 | 1.000 | 0.889 | 0.904 | 0.824 |
| adapt-regression-static | 0.690 | 1.000 | 0.806 | 0.832 | 0.690 |
| static-pckp | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |
| tuned-static-support | 0.435 | 1.000 | 0.806 | 0.747 | 0.435 |

## Change against static on the same task

| method | mean change | worst-family change | negative-transfer | deviation rate |
|---|---|---|---|---|
| adapt-listwise-static | +0.157 | +0.000 | 0.04 | 0.56 |
| adapt-regression-static | +0.085 | +0.000 | 0.00 | 0.26 |
| tuned-static-support | +0.000 | +0.000 | 0.00 | 0.00 |

## Paired comparisons (template-clustered bootstrap)

| comparison | mean diff | 95% CI | p | Holm p | clusters |
|---|---|---|---|---|---|
| adapt-listwise-static vs tuned-static-support | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0020 | 12 |
| adapt-listwise-static vs static-pckp | +0.157 | [+0.062, +0.261] | 0.0005 | 0.0020 | 12 |
| adapt-regression-static vs tuned-static-support | +0.085 | [+0.020, +0.156] | 0.0150 | 0.0300 | 12 |
| adapt-regression-static vs static-pckp | +0.085 | [+0.020, +0.156] | 0.0150 | 0.0300 | 12 |

## Measurement validity (relaxed budget; positive and negative controls)

- ledger: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
- deploy: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
- triage: {'all_evidence_mean': 1.0, 'no_fact_evidence_mean': 0.0, 'tasks': 24}
