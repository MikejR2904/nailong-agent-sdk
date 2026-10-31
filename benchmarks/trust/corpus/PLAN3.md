# Corpus comparison, third round: a harder natural profile, fault-free controls, unchecked lineage

Written before the derived test split exists. Nothing here changes PLAN2.md, whose natural test
evaluation is registered separately and is reported as registered.

## What is added

* **Derived profile** (seeds 3000+): the same three-agent pipeline on a table of six months by five
  regions with 5-digit amounts, where agent B also writes the margin (largest total minus second
  largest) and the integer average, and agent C carries the margin into the summary. Natural
  failures now occur at B as well as at A (dev: 13 at A, 5 at B, 18 of 18 runs failed).
* **Fault-free controls**: runs whose final answer is correct, given the same "the output is wrong"
  prompt (LLM methods) or `assume_fault` (SDK). A step named is a false attribution. Reported for the
  methods that can decline: step-by-step and every SDK arm. All-at-once, binary search and FALAT
  always name a step.
* **`trace_unchecked`** (library, opt-in): lists the writers of the artifacts the failing run read as
  `unchecked` candidates, earliest first, never primary. Evaluated as `sdk-structural+trace`, whose
  only added value is top-3.
* **A better model check for `sdk-llmcheck`**: the model works the sums row by row, three votes by
  majority, and a missing required line counts as a defect. Developed on the dev splits only.

## Dev findings that shape the test

1. On both natural profiles the Who&When methods and FALAT locate the fault on nearly every dev case
   with 1 to 10 calls: the faulty numbers and the source table are both in the trace, so a model can
   re-add them. The derived profile did not change that. These are findings, not defects of the
   baselines.
2. `sdk-structural` and `sdk-integrity` name the writer of the wrong summary (node C), not the origin;
   they score 0 at step level on natural errors by design, because nothing checks the intermediate
   files. `sdk-oracle` is an upper bound using the pipeline's validator.
3. On fault-free dev controls step-by-step names a step on 12 of 14 runs; every SDK arm names none.

## Later dev additions

* **Same-run ambiguity in `attribute_lineage`**: when a read matches several writes that all come from
  one agent run (an identical rewrite), the trace continues through that run's latest write instead of
  stopping as ambiguous. Found on natural dev case s1005.
* **Planted-derived profile** (dev seeds 3201-3202, test seeds 3301-3304, test not built): hidden read
  and write faults on the derived pipeline. Dev result (36 failed runs): `sdk-integrity` 36/36 steps,
  the Who&When methods and FALAT 3-9/36, `sdk-llmcheck+integrity` 29/29 of the runs it answered.
  Seven runs of every model-based method errored with HTTP 412 (the provider account was suspended
  for its monthly spending limit), so those arms are not complete on dev and were not re-run.

## Pre-registered analysis for the derived test split (not yet built)

Cases: 12 seeds (3101-3112) times three pipeline models, kept when the run finished and its final
answer is wrong. Report every method of `evaluate.py`. Primary comparison: step-level accuracy of
`sdk-llmcheck+integrity:glm-5p3-flash` against the best non-ground-truth LLM method by derived dev
step accuracy, paired difference with a 95% bootstrap interval and exact McNemar. Controls: false
attribution rate with a Wilson interval, on the controls of the natural test split. Exclusions are
counted. No change to the methods after the derived test corpus is built.

## Procedure (when run)

1. `python -m corpus.build --set derived --split test --workers 4`
2. `python -m corpus.evaluate --dataset derived --split test --out results/derived_eval_test.jsonl
   --report reports/corpus_eval_derived_test.md --controls --token-cap 12000000`, once.
3. Report with deviations. The natural test evaluation of PLAN2.md is run separately and reported
   first; both are run once.
