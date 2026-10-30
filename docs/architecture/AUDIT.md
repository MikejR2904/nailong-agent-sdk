# Audit of branch `sdk_fix` (commit 5812431), 2026-10-16

Scope: architecture and claims, not a line-by-line review. The line-by-line manual audit in
`docs/manual-audit-plan.md` is still at Stage 1 (`foundations/` done); this audit does not replace it.

## Method

1. Read `docs/reference/README.md` (workflows, evidence table) and `memory.md`, `observability.md`.
2. Checked each architectural claim that the presentation relies on against the source:
   `memory/episodes.py`, `memory/episode_models.py`, `memory/episode_scoring.py`,
   `memory/semantic_compaction.py` (`_verify_group`, `_verify_consolidation`),
   `foundations/optimization/{models,solvers}.py`, `agent/verification.py`,
   `state/project_state_models.py`, `state/orchestration.py` (controller phases),
   `state/graph_models.py`.
3. Re-read the stored benchmark reports in `benchmarks/results/`.
4. `ruff check src` and `ruff format --check src`; scan for `TODO/FIXME`, bare `except`,
   and silent `except Exception`.
5. Ran the full test suite (result below).

## Verdict

The code matches what the documentation says about the memory, verification and orchestration
layers. The architecture is coherent and the layering is real. The weakest part for a
research audience is not the code: it is **the empirical evidence for the contribution**
(see R1-R4).

## Claims verified against source

| Claim | Where | Status |
|---|---|---|
| Action episodes may depend only on exploratory episodes; exploratory episodes cannot declare dependencies | `memory/episodes.py::add_action`, `memory/episode_models.py::EpisodeRecord.validate_record` | Confirmed (enforced twice: graph and record) |
| Compaction is a dependency-closed knapsack with a solver certificate (`optimal`, `best-effort`, `infeasible-mandatory`) | `foundations/optimization/models.py` (`PckpStatus`, `PckpSolution`), `solvers.py` (rooted-forest DP, branch and bound, greedy baseline) | Confirmed. Certificate carries upper bound, gap, problem hash |
| Default strategy is exact PCKP; weights are static additive utilities | `PaskCompactionPolicy` (relevance 0.40, centrality 0.20, provenance 0.20, recency 0.10, frequency 0.05) | Confirmed. Note the weights are hand-set, not learned |
| A semantic summary is released only after archive read-back and 7+ deterministic checks | `SemanticCompactor._verify_group` | Confirmed: archive intact, dependencies resolve, citations in group, claims/decisions grounded, mandatory issues, actions, identifiers, compression |
| A model judge can only veto | `_semantic_stages` | Confirmed by structure (judges run after deterministic checks) |
| Verification gate returns a decision carrying `verifier_id`, `authority` class and bounded evidence | `agent/verification.py::VerificationDecision` | Confirmed (evidence capped at 4,096 JSON chars) |
| Working memory changes only through typed, evidence-linked transitions | `state/project_state_models.py` (`StateTransition.evidence min_length=1`, nine `StateTransitionKind`s) | Confirmed |
| Controller phases and transitions | `state/orchestration.py` | Confirmed; rejection returns to `PLANNING`, a repair goes back through plan approval (corrected in diagram D7) |
| Hygiene | `ruff check`, `ruff format --check` | Clean (148 files) |
| No TODO/FIXME markers in `src/` | grep | None |

## Findings

Severity is for the research presentation first, the engineering second.

### Research validity

- **R1 (high) - Benchmark is synthetic and the "reader" is not a model.**
  `scripts/run_memory_benchmark.py` states that its reader is deterministic text matching over
  the projected context and "does not measure reasoning". Three seeds, one generator (file reads
  with checksums, permission failures, dependent actions), one budget. It supports "the context
  retains more recoverable facts", not "agents perform better".
- **R2 (high) - Mean results, 3 seeds, 240 episodes** (`benchmarks/results/memory-benchmark-report.json`):

  | Arm | failures F1 | task reconstruction | checksum direct | visible tokens |
  |---|---:|---:|---:|---:|
  | PCKP only | 0.434 | 0.193 | 0.146 | 4,135 |
  | + semantic (extractive) | 0.893 | 0.428 | 0.224 | 5,290 |
  | + semantic, bounded graph (60 records) | 0.893 | 0.428 | 0.224 | 5,290 |

  Semantic compaction roughly doubles failure recall and task reconstruction at about +28%
  visible tokens. It also means that more than half of the facts the task needs are still not
  in the visible context after compaction; they are reachable only by archive retrieval
  (0.70 of the time, 0.83 with the bounded graph). Present both numbers.
- **R3 (medium) - Verifier arms are an ablation of an oracle.** The "noisy+verifier" arms use a
  rule that compares claims with the generator's ground truth, which a deployment does not
  have (the script says so). Do not present those F1 values as the verifier's real accuracy.
- **R4 (medium) - No baseline against published memory systems.** There is no comparison to
  rolling LLM summarization, MemGPT-style paging or retrieval-only memory on the same
  episodes. Without one, "better than the state of the art" cannot be claimed; "different
  structure with these measured properties" can.
- **R5 (medium) - PCKP weights are hand-set.** Utility is a fixed weighted sum with a
  lexical relevance scorer. The exactness is of the *optimisation*, not of the *utility
  model*; an optimal solution to a mis-specified utility is still a mis-specified selection.
  The `pckp-report.json` compares exact to a greedy baseline on a handful of cases
  (`retained_utility_gap`), not on real runs.
- **R6 (low) - Token budgets are characters / 4**, not a tokenizer. Acceptable for a bound,
  but report it when quoting budgets.

### Engineering

- **E1 (medium) - Three very large modules.** `agent/base_agent/agent.py` 2,548 lines,
  `memory/episode_store.py` 1,872, `state/graph.py` 1,572. `BaseAgent._run` starts at line 226
  and `_terminate` at 2,124; the run loop is one long method. The files are well documented,
  but they are the places where the next regression will land.
- **E2 (low) - 109 `except Exception` sites, 2 silent.** Most are in MCP tool wrappers
  (`mcp/controller_tools.py` 16) and convert to typed errors. The two that return `None` with
  no record are benign but break the project's own rule that every failure names its cause:
  `agent/openai_compatible/transport.py:426` (`_read_error_body`) and
  `tools/core/helpers.py:323` (`_to_png_bytes`).
- **E3 (low) - `InMemoryEpisodeGraph.add_exploratory/add_action` accept a `payload`
  argument that is ignored** ("accepted for call-site compatibility only"). Dead parameter.
- **E4 (low) - Reference-doc "called by" lists are heuristic**; the doc itself says about
  200 differ from a fresh scan. Fine for orientation; do not cite them in a thesis.
- **E5 (info) - Docs and tests are a strength.** 2,047 tests collected at the last recorded
  run, and `tests/test_reference_docs.py` fails if an entry is missing or stale.

## Test run

The full suite (`pytest tests -q -x`) was started on this tree. It was about 38% through with
no failures when the audit was written, and had not finished. Treat the suite as "no failure
seen so far", not as a pass, and re-run `.venv/Scripts/python.exe -m pytest tests -q` for the
final figure before quoting a count.

## What this audit did not cover

- Concurrency under real multi-process load (locks, WAL retry) beyond reading the contracts.
- The MCP security model (credentials, authority matrix) and the retention/vacuum paths.
- The `apps/sg_job_agent` application and `example_runs/`.
- Any live-model behaviour; no model was called.
