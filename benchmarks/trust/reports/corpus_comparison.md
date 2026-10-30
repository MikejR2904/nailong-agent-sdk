# Root-cause attribution: the SDK against LLM-based methods on a labelled failure corpus

Plan: `corpus/PLAN.md`, committed before the test split was generated. Code: commit `e99e03c` plus
one client fix described under "Deviations". Raw reports: `corpus_eval_test.md` (corrected
baselines), `corpus_eval_test_as_registered.md` (the first run), `corpus_eval_dev_*.md`.

## What was compared

Three real agents (extract, analyse, report) run a three-step pipeline on the SDK. Three models
run it (`glm-5p3-flash`, `deepseek-v4p1-flash`, `minimax-m3p1-flash`). One tool-level fault is
planted per run at a known node and call: a read that returns altered content, or a write that
stores altered content. The final answer is wrong in 68 of the 72 test runs that completed. The 12
fault-free controls all produced the correct answer, so each failure is the planted fault.

Every method is given the same symptom (the final summary is wrong) and the same runs. It must
name the node and the call where the fault was planted.

## Result on the held-out test split (68 scored failed runs)

Agent = the node is right. Step = the exact call is right. Intervals are 95% Wilson.

| method | what it uses | agent | step |
|---|---|---|---|
| last step / first step | the shape of the pipeline | 24/68 (35%) / 20/68 (29%) | 12/68 (18%) / 12/68 (18%) |
| LLM all-at-once, `glm-5p3-flash` | the flat trace | 23/68 (34%) | 23/68 (34%) |
| LLM all-at-once, `deepseek-v4p1-flash` | the flat trace | 34/68 (50%) | 34/68 (50%) |
| LLM step-by-step, `glm-5p3-flash` | the flat trace | 36/68 (53%) | 36/68 (53%) |
| SDK, symptom check only | the SDK's records | 24/68 (35%) | 12/68 (18%) |
| SDK + defect checks answered by a model | records + a model that sees the source table | 68/68 [0.95, 1.00] | 56/68 (82%) [0.72, 0.90] |
| SDK + defect checks that know the truth | records + an oracle (an upper bound, not deployable) | 68/68 [0.95, 1.00] | 56/68 (82%) |
| SDK, record-consistency check, no defect check | records only | 68/68 [0.95, 1.00] | 68/68 [0.95, 1.00] |
| SDK, checks answered by a model + record-consistency | both | 68/68 | 68/68 |

By fault kind, the LLM methods name the right call for a read fault in 22 to 24 of 36 cases and
for a write fault in 0 to 12 of 32 (and the 12 are the cases where the fault is at the last
node, which "last step" also gets). The SDK arms with a record-consistency check get all 36 and
all 32. The SDK with defect checks alone gets all 32 write faults and 24 of 36 read faults: the 12
it misses are read faults at the first node, where the altered table is the only copy the model
saw and no content check can know it is wrong.

## How to read this

* **The SDK arms that use the record-consistency check are close to a tautology for this corpus.**
  Every planted fault is a tool that reports one thing and stores another, which is exactly what
  that check compares. It shows that the SDK's records can reveal such a tool without any
  definition of a defect, which a flat trace cannot (the trace of a tool fault is internally
  consistent). It says nothing about how the SDK fares on a model's own reasoning errors, which
  the corpus does not contain and which the LLM methods were designed for.
* **The comparison with the LLM methods is therefore partly a comparison of information.** The
  LLM methods see the trace; the SDK methods see the journal and the artifact store as well. That
  is the point of recording them, but a reader should not take 100% against about 50% as the
  verdict of two methods given the same inputs.
* **The honest SDK result without that advantage** is `SDK + defect checks answered by a model`:
  every agent right, 56 of 68 calls right, using only a small model as the checker and no
  knowledge of where the fault is. Its misses are read faults at the first node.
* The structural SDK method with only the symptom equals the last-step baseline (24/68), as
  expected: the symptom lies in the last write, and the SDK does not guess further without a
  check or a record that disagrees.

## Deviations from the plan, stated

1. **The first test run handicapped the LLM baselines.** The client allowed 1,500 output tokens;
   the reasoning models spent them thinking and returned nothing for 99 of 431 cached replies. I
   found this by looking at the "unparseable" rate after seeing the test results, fixed the
   client (8,000 tokens, and one retry at 24,000 if a reply is cut off), checked on dev that the
   unparseable rate fell, and reran test once with the fixed client. The SDK arms are unchanged
   between the two runs; the LLM baselines rose from 15, 1 and 36 correct (all-at-once `glm`,
   all-at-once `deepseek`, step-by-step `glm`) to 23, 34 and 36. The corrected numbers are the
   ones above, the as-registered ones are kept in `corpus_eval_test_as_registered.md`. This was a
   fix to the baselines, not to the SDK method; it still means the test split was evaluated
   twice.
2. **Two SDK changes were made after dev results** (the ordering rule and `verify_records`),
   before the test split existed. Dev scores of the changed arms: step accuracy from 16/34 to
   28/34 (ordering rule) and 34/34 (record-consistency check).
3. FALAT and CHIEF were not re-implemented; the two Who&When baselines stand in.

## Limits

* The faults are planted tool faults in a three-step pipeline, from one provider's models. The
  corpus has no natural model errors and no reasoning faults.
* The checker and the judges are small models; a stronger judge may do better. Only one judge
  model was used for step-by-step and for the checker.
* The author of the SDK tools designed the faults; results favour the method that knows the fault
  class.
* Token use of the last evaluation: about 308k tokens sent and 545k received for the test
  split's LLM calls (most calls were cached from earlier runs). The cost of generating the
  corpora was not tracked.
