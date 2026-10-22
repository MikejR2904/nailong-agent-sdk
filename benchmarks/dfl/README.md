# Ranking-DFL retention benchmark

Harness for training and evaluating learned episode utilities (`LEARNED_PCKP`) against static PCKP,
greedy PASK and random feasible selection. It lives outside `src/` on purpose and shares nothing with
`benchmarks/trust` (whose pre-registration, lock and results are untouched).

**Evidence status.** There are two kinds of evidence, never mixed. `dfl_bench` (this directory's main
package) runs against a *synthetic oracle*, a closed-form model of downstream success; it checks the
solver and the learning pipeline and is **not** agent performance. `dfl_bench.live` measures **real
model calls**: for each task and retention set the SDK assembles the context, a Fireworks model answers,
and a deterministic verifier scores it (see `PREREGISTRATION_LIVE.md` and `docs/dfl/06-measured-evaluation.md`). Every output is stamped `evidence_kind`, and synthetic and measured
evidence cannot be mixed in one preference set. For real agent runs, record outcomes with
`dfl_bench.ledger.write_outcomes` (one verifier-scored run per instance, selection and trial) and serve
them through `LedgerExecutor` (`evidence_kind = "measured"`, never fabricates a missing outcome).

## Regimes

| Regime | Oracle | Purpose |
|---|---|---|
| `aligned` | redundant + complementary | static relevance is informative |
| `shifted` | redundant + complementary | static relevance is misleading; failed, substrate-backed and depended-on episodes matter |
| `null` | redundant + complementary | requirement membership is independent of every observable feature: nothing to learn; false-positive control |
| `additive` | exactly additive | an additive utility can be exact; checks regret near the optimum |

## Run

From the repository root, with the project environment:

```bash
cd benchmarks/dfl
PYTHONPATH=../../src:. python -m dfl_bench --regimes aligned shifted null additive \
    --replicates 20 --base-seed 3000 --out results/holdout
PYTHONPATH=../../src:. python -m dfl_bench.perf scale --out results/perf
PYTHONPATH=../../src:. python -m dfl_bench.perf latency --out results/perf   # idle machine
```

`PREREGISTRATION.md` fixes the holdout seeds, hypotheses and test before the run; its SHA-256 is stored in
the results file. The earlier runs (`results/exploratory`, seeds 100-102; `results/confirmatory`, seeds
2000-2019) predate it and are kept for the record. Raw per-instance rows (`synthetic_results.json`) are
git-ignored because they are large and exactly reproducible from the seeds; the Markdown tables and the
bootstrap summaries are tracked.

## Measured (live) evaluation

```bash
cd benchmarks/dfl
PYTHONPATH=../../src:. python -m dfl_bench.live --out results/live                       # primary
PYTHONPATH=../../src:. python -m dfl_bench.live --seeds --heldout-families ledger deploy triage --out results/live_heldout   # rotations
```

It needs `FIREWORKS_API_KEY` (environment or the repository `.env`) and makes paid calls. Every call is
appended to `results/live/outcomes-<settings digest>.jsonl` before the next one is made, so an
interrupted run resumes without repeating a call, and outcomes recorded under different model settings
are never mixed (the file name carries a digest of model, provider and parameters). Tests that call the
model carry the `live` marker (`pytest -m "not live"` skips them; they also skip without a key).

## Layout

| Module | Role |
|---|---|
| `instances.py` | Replayable memory-graph recipes (template, regime, requirements, oracle form), built through the store's public lifecycle |
| `oracle.py` | Synthetic oracle, exhaustive optimum, `DownstreamExecutor` protocol |
| `ledger.py` | Measured-outcome ledger and the executor that serves it |
| `selection.py` | Selections through `compact()`, feasibility validation, the six candidate generators |
| `preferences.py` | Significance-filtered, confidence-weighted pairs; conflict resolution; evidence-kind guard |
| `splits.py` | Template/graph/task-disjoint splits and the leakage check |
| `methods.py` | Linear and MLP ranking trainers (hyperparameters chosen on validation), set-level regression, top-k with repair |
| `experiment.py` | One replicate: split, candidates, two on-policy rounds, test evaluation, ablations |
| `stats.py`, `report.py` | Bootstrap intervals, template-clustered bootstrap, sign test, Holm correction, tables, primary-hypothesis evaluation |
| `perf.py` | Solver scale check and latency micro-benchmark |
| `live/scenarios.py` | Generated tool-call histories (ledger, deployment, triage) with ground-truth questions and a deterministic verifier |
| `live/context.py` | The SDK's own store, journal, graph, projector and `ModelContext` for a task under a retention set |
| `live/executor.py` | Real model calls through `OpenAICompatibleAgentModel`, adaptive concurrency, ledger caching, token cap |
| `live/experiment.py` | One measured replicate, including the positive and negative validity controls |

Tests: `tests/memory/test_ranking_loss.py`, `test_utility_artifacts.py`,
`test_learned_pckp_integration.py`, and `tests/dfl_bench/`.
