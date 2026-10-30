# Pre-registration 4: few-shot adaptation to an unseen family

Synthetic oracle only; no model calls. Earlier registrations and results stand (zero-shot learned
methods did not beat static).

## Design (`generalize/fewshot.py`, plan `fewshot`)
For each `lofo:<family>` fold, the held-out family is split by template into a **support set** (2
templates, verified outcomes available) and a **query set** (the remaining 4 of 6 templates; 16
instances). Every method may use the support outcomes; all are scored on the query set only. Source
families supply the training and validation data as before.

Methods: `static-pckp`; `tuned-static-pckp` (tuned on source families); **`tuned-static-support`**
(the five static weights tuned on the support set: 41 candidate policies x 8 instances x 16 trials of
outcome evaluation); `adapt-{regression,listwise}-{static,source}`: a linear utility trained on the
support outcomes of the family's candidate selections (about 320 outcome evaluations), regularised
towards a prior (the static policy's weights, or a model trained on the source families), with the
regularisation strength chosen by leave-one-template-out decision quality on the support set and the
prior itself returned if no strength beats it. The non-learning comparator therefore consumes more outcome
evaluations than the adapted models.

Development (`results/fewshot_dev`, seeds 0-1, 192 query instances): `adapt-listwise-source`
0.604 vs `tuned-static-support` 0.585 (+0.019, CI [-0.025, +0.062]); worst-family change worse than
static. This direction is why the hypotheses below are registered; it is not evidence.

## Confirmatory run
Seeds **6000-6004**, six `lofo:` folds per seed, 6 templates x 4 instances per family, 2 support
templates. Not run before this file was written.

## Hypotheses (Holm over two; paired by instance, template-clustered bootstrap, pooled query instances)
- **F1**: `adapt-listwise-source` has higher mean quality than `tuned-static-support`.
- **F2**: `adapt-listwise-source` has higher mean quality than `tuned-static-pckp`.
Supported only if Holm p < 0.05 and the 95% interval excludes zero. Reported descriptively: every
other adapted variant, deviation rate, negative-transfer rate, worst-family change.
