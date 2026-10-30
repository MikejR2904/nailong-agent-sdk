# Corpus comparison, second round: more baselines, natural errors, more metrics

Written after the natural dev split was evaluated and before the natural test split exists.
Nothing below may be changed after the test corpus is built; a change is a deviation and is reported.

## What is added to the first round (PLAN.md)

* Who&When's three automated methods, ported from the paper: all-at-once, step-by-step, binary search,
  each with and without the correct final answer (`whowhen.py`).
* FALAT (arXiv 2606.00765) on the same input (the numbered trace read from the audit ledger, the task
  description, the correct final answer), reimplemented from the published description (`falat.py`).
  Its prompts and its window, K and re-search parameters are mine; scores may differ from the authors'.
* Natural errors: pipelines run by a model with no planted fault; a case counts when the run finished
  and its final answer is wrong. The label is the latest `write_draft` of the first output file (in
  pipeline order) whose content is defective against the source table (`natural_label`).
* Metrics: agent, step, ±1, ±2 steps, top-3, answered rate, accuracy when answered, calls, tokens and
  seconds per case, repeat stability, paired McNemar and bootstrap differences, breakdowns by model,
  node and trace length.

## Known limits, stated before the test

1. Every natural dev failure is an arithmetic error in node A. The B-level profile (seeds 2000+) did
   not produce failures at B. The first-step baseline therefore gets the agent right by construction
   on natural errors, and the label always names a write, never a read.
2. The label is a rule applied to the final files, not a human annotation. It names the writer of the
   first defective output, which is where the error first becomes durable, not necessarily where the
   model "decided" it.
3. `sdk-structural` knows only that summary.txt is wrong. Without a content check it follows the
   lineage to the writer of summary.txt (node C), which on a propagated error is not the origin.
   On natural dev it scored 0/28. That is reported as a result, not fixed for the test.
4. `sdk-oracle` uses the pipeline's own validator and is an upper bound, not a competitor.
   `sdk-llmcheck` uses a model as the content check and is a model-based method.
5. The LLM baselines see the full trace including the file contents; the SDK arms see the ledgers.
   Fault-free controls (false attribution) are not implemented; the SDK is always run with
   `assume_fault=True`.
6. One repeat per case at temperature 0 unless stated; the cache makes a rerun identical.

## Pre-registered analysis on the natural test split

Cases: all natural test runs with `pipeline_complete`, `outcome_failed` and a labelled fault.
Report every method in `evaluate.py`'s table, including those that do badly.

* Primary comparison: step-level accuracy of `sdk-llmcheck+integrity:glm-5p3-flash` against the best
  non-ground-truth LLM baseline by dev step accuracy, as a paired difference with a 95% bootstrap
  interval and an exact McNemar test.
* Secondary: the same for `sdk-structural`, `sdk-oracle` and the other baselines; ±1, ±2, top-3;
  cost per case; breakdown by model and trace length.
* No multiple-comparison correction; all comparisons are labelled exploratory except the primary.
* Exclusions are counted and listed in the report.

## Procedure

1. Commit this plan and the code at the commit that builds the corpus.
2. `python -m corpus.build --set natural --split test --workers 6` (72 cases).
3. `python -m corpus.evaluate --dataset natural --split test --out results/natural_eval_test.jsonl
   --report reports/corpus_eval_natural_test.md --token-cap 12000000`, once.
4. Write `reports/corpus_comparison_v2.md` with the deviations.
