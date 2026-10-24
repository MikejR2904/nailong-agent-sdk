# Pre-registration: rarity features and leave-one-out supervision (measured)

Evidence kind: **measured** (real model answers, deterministic verifier; `glm-5p3-flash`, same settings
and the same worlds, seeds 4100-4102, as `PREREGISTRATION_LIVE_FEWSHOT.md`, whose result is unchanged).

## What is new
1. **Feature schema v3** (`utility_features_v2.py`): v2 plus six rarity features (mean and maximum inverse
   document frequency of an episode's terms, share of its identifiers that no other episode contains,
   identifier density, distinctiveness, failure-with-sole-source). None depends on access counts.
2. **Leave-one-out criticality** (`generalize/loo.py`): from the full history (budget ignored), each
   episode and its dependents are removed in turn and the measured drop in verifier score is its
   criticality. On the support tasks it supervises training: pairs "critical episode > neutral episode"
   (drop >= 0.2 vs < 0.05) are added to the listwise objective, still anchored to the static prior.
3. **Retention scorecard** (evaluation only): criticality is also measured on the query tasks and used
   to report critical recall (drop-weighted share of critical episodes retained), critical episodes
   missed, and distraction load (share of retained tokens in zero-criticality episodes). Never used for training.

## Methods (all with the same 12 support tasks and 24 query tasks per family)
`static-pckp`, `tuned-static-support`, `adapt-listwise-static` and `adapt-regression-static`
(unchanged; their outcomes come from the earlier ledger), and three ablations of the listwise method:
`adapt-listwise-static-v3` (features only), `adapt-loo-listwise-static-v1` (LOO only) and
**`adapt-loo-listwise-static-v3`** (both; the candidate).

## Hypotheses (Holm over two, template-clustered paired bootstrap, 72 query tasks)
- **M1**: the candidate has higher mean measured quality than `adapt-listwise-static`.
- **M2**: the candidate has higher mean measured quality than `static-pckp`.
Supported only if Holm p < 0.05 and the 95% interval excludes zero. Also required: the candidate's
worst held-out-family quality is no more than 0.05 below static's. Everything else (ablations,
scorecard, whether critical recall tracks quality) is descriptive. The earlier result sets a high bar:
`adapt-listwise-static` already scores 0.904 on average, so M1 can only be supported by a gain on
`ledger`/`triage`; `deploy` is at the ceiling.

## Limits
One model, three families, 72 tasks, generated histories, one run. Criticality is measured from the full
history and so captures irreplaceability given everything else present, not interactions under a budget.
The runner prints Holm values over all comparisons; the registered Holm is recomputed over M1 and M2.
