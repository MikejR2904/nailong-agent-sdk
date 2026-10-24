# Pre-registration: measured few-shot adaptation (`dfl_bench.live.fewshot_run`)

Evidence kind: **measured** (real model answers scored by the deterministic verifier). Model
`glm-5p3-flash` through Fireworks, same settings as the earlier live runs. Earlier registrations are unchanged.

## Design
For each of the three live families held out in turn (worlds seeded 4100, 4101, 4102, so static
outcomes already in the ledger are reused), 2 of 6 templates (12 tasks) are the **support set**; the
other 4 templates (24 tasks) are the **query set**, scored with 3 trials each. Methods:
`static-pckp`; `tuned-static-support` (20 random weight draws plus the default, tuned on the support
set by measured quality); `adapt-listwise-static` and `adapt-regression-static` (a linear utility
trained on the measured outcomes of candidate selections for the support tasks, regularised towards
the static policy, with the regularisation chosen by leave-one-template-out measured quality and the
static prior returned unless some strength beats it). No source-family model is used. The adapted
methods consume fewer outcome evaluations than the tuned baseline's search.

Origin of the candidate: `adapt-listwise-static` was the best unregistered variant of the synthetic
few-shot round (`docs/dfl/09`, round 4): +0.026 vs static there, interval against the support-tuned
baseline including zero. The synthetic result is not evidence for this registration.

## Hypotheses (Holm over the two primary tests, template-clustered paired bootstrap, 72 query tasks)
- **L1**: `adapt-listwise-static` has higher mean measured quality than `tuned-static-support`.
- **L2**: `adapt-listwise-static` has higher mean measured quality than `static-pckp`.

Supported only if Holm p < 0.05 and the 95% interval excludes zero. Required alongside, descriptive:
the worst held-out-family quality of `adapt-listwise-static` must not fall more than 0.05 below
static's ("quality on the unseen family remains high"); absolute scores, negative-transfer rate and
deviation rate are reported; measurement-validity controls (all evidence retained vs fact-bearing
episodes removed) are reported per family.

## Limits
One model, generated histories, three families, 72 query tasks: small, and one run. A non-significant
result means "not shown", not "equal".
