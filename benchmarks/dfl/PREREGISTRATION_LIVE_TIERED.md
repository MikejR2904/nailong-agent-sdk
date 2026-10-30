# Pre-registration: three-level retention with calibrated fidelity (measured)

Evidence kind: **measured** (real model answers, deterministic verifier; `glm-5p3-flash`; the same worlds,
seeds 4100-4102, support/query split and model settings as `PREREGISTRATION_LIVE_FEWSHOT.md`, whose result is
unchanged). The current best method, `adapt-listwise-static`, is kept as the reference.

## What is tested
`memory/tiered_retention.py` plans full / summary / drop jointly under each task's budget. A summary-tier
episode is removed from the retained store (so the model sees its stub) and its extractive summary is added to
the SDK model context through the existing `semantic_summaries` slot; summary tokens count against the budget.
Fidelity is the conservative fidelity mapped through the isotonic calibration fitted on seeds 4300-4302
(`results/tiered_calibration/fidelity_calibration.json`). Methods added: `tiered-static` (the store's static
utilities) and `tiered-adapt-listwise` (the utilities of `adapt-listwise-static`, same support set).
The model-free check on fresh worlds (seeds 4500-4502) found fact survival +0.108 over binary retention under static
utilities (95% CI [+0.021, +0.206]) and no gain under oracle utilities; `triage` was 0.059 below binary there.

## Hypotheses (template-clustered paired bootstrap, 72 query tasks)
- **G1 (guard on the current best, non-inferiority):** the lower 95% bound of
  `tiered-adapt-listwise` minus `adapt-listwise-static` is above -0.05.
- **G2 (superiority, Holm over G2 and G3):** `tiered-adapt-listwise` has higher mean quality than `adapt-listwise-static`.
- **G3:** `tiered-static` has higher mean quality than `static-pckp`.
- Floor: the worst held-out-family quality of `tiered-adapt-listwise` is no more than 0.05 below that of `adapt-listwise-static`.
Descriptive: absolute scores, tokens used, full/summary split, and a control that replaces every episode by its
summary (budget ignored) to compare a real model with the model-free fact-survival proxy.

## Limits
One model, three families, 72 tasks, generated histories. The SDK's own prompt states that a semantic summary
"is derived, not evidence", which may make the model discount facts that appear only in a summary; the harness
instructions are unchanged, so any such effect is part of the result. No stubs are involved: the model call path is the production one.
