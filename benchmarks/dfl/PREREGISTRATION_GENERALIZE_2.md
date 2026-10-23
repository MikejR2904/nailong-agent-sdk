# Pre-registration 2: shrinkage towards the static policy (`dfl-generalize-1`, plan `shrink`)

Synthetic oracle only; no model calls. Amends nothing in `PREREGISTRATION_GENERALIZE.md`, whose
confirmatory result (seeds 3000-3002) stands: no learned method beat static.

## Why this round exists
The first confirmatory run showed learned models below static on held-out families. This round
tests one change chosen on development seeds 0-1 (`results/generalize2_dev`): regularise a linear
model towards the static policy (which is a point of the linear model class,
`static_prior_weights`) and pick the shrinkage by leave-one-family-out decision quality on the
training families, returning the static prior itself unless the learned model beats static on those
inner folds. Development outcome: `shrunk-selected` 0.590 vs static 0.592 (parity, no gain).

## Confirmatory run
Seeds **4000-4002** (not used before), 5 templates x 4 instances per family, all ten folds, plan `shrink`.
Methods: `static-pckp`, `tuned-static-pckp`, `linear-dfl`, `regression-pckp`, `pairwise-v1`,
`regression-v1`, six `shrunk-*` variants, `shrunk-selected` (best inner-LOFO quality among them),
`combined-validation`.

## Hypotheses (Holm over two, template-clustered paired bootstrap on pooled `lofo:` instances)
- **Q1**: `shrunk-selected` has higher mean quality than `static-pckp`.
- **Q2**: `shrunk-selected` has higher mean quality than `tuned-static-pckp`.
Supported only if Holm p < 0.05 and the 95% interval excludes zero. A non-inferiority statement
(difference above -0.02) is reported descriptively, not as a test. Development suggests Q1 and Q2 will
not be supported; they are registered anyway.
