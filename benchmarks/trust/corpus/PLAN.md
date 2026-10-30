# Failure corpus and attribution comparison: plan, written before the test split was generated

Status: the **dev** split (seeds 1 and 2) was generated and used to develop the methods. The
**test** split (seeds 3 to 6) had not been generated when this file was committed. It is generated
once and every method is evaluated on it once, with the code of the commit that contains this file.

## What is compared

Three real pipeline agents (A extracts per-region totals from a sales table, B computes the grand
total and the top region, C writes the headline) run on the SDK with its ledgers, tool-result
journal and artifact store. One tool-level fault is planted per run, at a known node and a known
call:

* `read` - `read_artifact` returns altered content (the store keeps the original);
* `write` - `write_draft` stores altered content (the call's arguments hold the original).

Faults are planted at each of the three nodes. Fault-free controls run too. Three models run the
pipelines: `glm-5p3-flash`, `deepseek-v4p1-flash`, `minimax-m3p1-flash`. Dev: 2 seeds, test: 4
seeds, so 126 cases in all (42 dev, 84 test).

**Label.** The node and the tool call where the fault was planted. **Symptom** given to every
method: the final `summary.txt` is wrong.

**Scored cases.** A case is scored if its pipeline completed and its final answer is wrong.
Cases that do not meet this are excluded and counted in the report. No other exclusion exists.

## Methods

| method | information used |
|---|---|
| `last-step`, `first-step` | the shape of the pipeline only |
| `llm-all:<judge>` | Who&When "all at once": the flat numbered trace and the symptom, one call |
| `llm-step:<judge>` | Who&When "step by step": the trace up to each step, one yes/no call per step, first yes |
| `sdk-structural` | `attribute_root_cause` with only the symptom check on `summary.txt` |
| `sdk-integrity` | the same plus `verify_records=True` and the other runs of the pipeline: no defect check |
| `sdk-oracle` | per-artifact and per-result checks that know the truth (an upper bound, not deployable) |
| `sdk-llmcheck:<judge>` | the same checks answered by a model that sees the source table |
| `sdk-llmcheck+integrity:<judge>` | both |

Judges: `glm-5p3-flash` and `deepseek-v4p1-flash` for all-at-once, `glm-5p3-flash` for
step-by-step and for the checker. Temperature 0. The prompt templates are in `methods.py`. FALAT
and CHIEF are not re-implemented: the two Who&When methods are the LLM baselines.

**Metrics.** Agent-level accuracy (the predicted node is the planted node) and step-level accuracy
(the predicted call is the planted call), with Wilson 95% intervals, by fault kind, node and model.

## What was changed after seeing dev results

Two changes to `attribute_root_cause`, both made on dev only:

1. a defective tool result that is an earlier call in a run than that run's defective write is
   ranked ahead of the write (dev step accuracy of `sdk-oracle`: 16/34 to 28/34);
2. `verify_records`, which compares each journaled read and write with the artifact store and needs
   no defect check (dev: 6 read faults at node A could not be seen by any content check; the
   integrity check names them). Fault-free controls: 0 of 6 runs raised a record inconsistency.

The second change is aimed at the fault class this corpus plants, so a good `sdk-integrity` score
shows that the SDK's records can reveal a tool that misreports, and nothing about how it fares on
a model's own reasoning errors.

## Not covered, stated in advance

* All faults are tool-level and planted; the corpus has no natural model errors and no
  reasoning faults. The LLM methods were built for those, and this corpus is a hard case for them
  (the flat trace of a tool fault is internally consistent).
* The pipelines are short (3 nodes, about 8 calls). Three models from one provider.
* The checker and the judges are small models; a stronger judge may do better.
* The author of the SDK tools designed the faults, so results favour the method that knows the
  fault class; read them with that in mind.
