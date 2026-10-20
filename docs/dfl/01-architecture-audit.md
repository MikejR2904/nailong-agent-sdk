# 1. Source-grounded audit: episodic retention and PCKP

Audit of the repository at commit `f9fee55` (branch `bench/trust-profile`), done before any code was
written. Line numbers refer to the files as they were then, except where marked *(new)*. Nothing
below assumes a feature exists; each claim names the function that implements it.

## 1.1 Episode lifecycle

| Stage | Where | What happens |
|---|---|---|
| Creation | `memory/episode_store.py` `InMemoryEpisodeStore.open_exploratory`, `open_action` | An `EpisodeRecord` (`memory/episode_models.py:44`) is created `OPEN`. Exploratory episodes have no prerequisites; action episodes may depend only on **closed exploratory** episodes (`open_action` raises otherwise). |
| Linking | `open_action` | `depends_on` is set on the action; `depended_on_by` is back-filled on each prerequisite. The graph is therefore a bipartite DAG of depth one: exploratory → action. |
| Closing | `close` | State becomes `CLOSED`. Closing an exploratory episode requires a non-empty `description`. |
| Access tracking | `mark_accessed` | Increments `access_count` and stamps `last_access_sequence`. Called by `BaseAgent._record_executed_result` (`agent/base_agent/agent.py:1845`) for the episodes an action consumed. |
| Production | `BaseAgent._record_executed_result` (`agent.py:1824`) | One episode per tool call. `content = {"call": ..., "result": {"status": "succeeded"/"failed"/"blocked", ...}}`; a source handle to the tool-result journal is attached. |
| Protection | `compact(protected_episode_ids=..., active_episode_id=...)` | Protected, active and every `OPEN` episode are **mandatory**; the set is closed under prerequisites (`_dependency_closure`, `episode_store.py:1036`). |
| Selection | `_compact_selection` (`:358`) dispatches to `_compact_greedy` (`:521`), `_compact_exact_pckp` (`:577`), `_compact_pask` (`:797`) and, *(new)*, `_compact_learned_pckp` (`:697`). |
| Compaction | `_compact_unretained` (`:1057`) then `_compact_record` (`:1146`) | Unretained episodes are tombstoned leaves-first so the retained closure stays intact; the raw evidence stays in the tool-result journal. |
| Summarisation / archive | `_semantic_pass` (`:1223`), `memory/semantic_compaction.py`, `memory/summary_ledger.py`, `memory/episode_archive.py` | Optional. Compacted groups are summarised, verified, committed; payloads are archived content-addressed. |
| Rehydration | `InMemoryEpisodeStore.rehydrate` (`:1722`), `ContextProjector.rehydrate` | Restores archived payloads within a token budget. |
| Persistence | `FileEpisodeStore.persist`, `export_snapshot` (`:436`), `adopt_snapshot` (`:458`) | Generation-numbered, hash-checked snapshots. |

## 1.2 Static utilities (the existing objective)

`_static_utility` (`episode_store.py:929`) computes, for each live episode,

`u_i = w_rel * relevance + w_cen * centrality + w_prov * provenance + w_rec * recency + w_freq * frequency`

with weights from `PaskCompactionPolicy` (`episode_models.py:75`): 0.40 / 0.20 / 0.20 / 0.10 / 0.05.
The diversity term (0.05) is deliberately absent because it is marginal and breaks additivity.

* `relevance`: `EpisodeRelevanceScorer.score` (`episode_scoring.py`); default is the lexical term
  overlap `|Q ∩ T(e)| / |Q|`, clamped to [0, 1].
* `centrality`: live dependents / max live dependents.
* `provenance`: 1.0 substrate-backed, 0.65 exploratory, 0.40 action (`_provenance_weight`).
* `recency`, `frequency`: normalised `last_access_sequence`, `access_count`.

These weights are hand-set; nothing in the repository fits them to outcomes.

## 1.3 PCKP construction, solving and certificates

* **Construction.** `_compact_exact_pckp` builds a `PckpProblem` (`foundations/optimization/models.py:45`):
  `token_budget`, and one `PckpItem` per live episode with `token_cost = _episode_tokens(record)`
  (`episode_scoring.py:50`, at least 1), **`utility = round(u_i * 1_000_000)`**, `prerequisites = depends_on`
  and `mandatory`. `PckpItem.utility` is `int >= 0`; `PckpProblem` rejects unknown prerequisites and cycles.
* **Solving.** `ExactPckpSolver.solve` (`solvers.py:61`): infeasible mandatory closure returns
  `INFEASIBLE_MANDATORY`; a rooted forest (every item has at most one prerequisite) with a small frontier
  uses an exact Pareto-frontier tree DP (`_solve_rooted_forest`, `:294`); otherwise a deterministic
  branch-and-bound with a fractional upper bound (`_solve_branch_and_bound`, `:91`; `_propagate` enforces
  closure, `_fractional_upper_bound` bounds). `max_branch_nodes` (`PaskCompactionPolicy.exact_max_branch_nodes`,
  default 50 000) turns an unfinished search into `BEST_EFFORT`.
* **Certificate.** `PckpSolution` (`models.py:66`): `status`, `selected_item_ids`, `mandatory_item_ids`,
  `token_cost`, `utility`, `upper_bound`, `optimality_gap`, `branch_nodes`, `solver`, `problem_hash`
  (SHA-256 of the canonical problem JSON), `diagnostics`. `OPTIMAL` is only returned after a complete proof;
  the default store policy may stop early and say so.
* **Validation.** `tests/foundations/test_optimization_solvers.py` compares both solver paths with brute
  force on random instances (`brute_force`, `random_problem`); `tests/memory/test_memory.py` checks
  compaction invariants on random episode graphs for all strategies.
* **Dossier.** The result's `dossier` records strategy, budget, the SHA-256 of the relevance query (never
  the text), mandatory and retained ids, every static utility with its components, the policy, the solver
  certificate and a guarantee string.

Consequence for this work: **the quantisation rule and integer range are fixed by the existing code**.
A learned predictor must produce `int >= 0` coefficients; the static path uses scale 10^6.

## 1.4 Interfaces that can host a predictor

| Interface | Fit |
|---|---|
| `EpisodeRelevanceScorer` Protocol (`episode_models.py:115`) | Returns one number per (query, episode). Too narrow: no graph, no budget, no joint context, and it feeds only one of five static terms. |
| `PaskCompactionPolicy` weights | A 5-parameter linear combination. A learned linear model over the same features is a strict generalisation, but weights cannot see new features. |
| `CompactionStrategy` + `InMemoryEpisodeStore.compact` | The right seam: a new strategy reuses mandatory/closure/solver handling and the dossier. Chosen. |

## 1.5 How selection reaches the model

`ContextProjector.project` (`context_projection.py:306`) calls `memory.compact(episode_token_budget,
protected_episode_ids=..., relevance_query=...)` and then keeps the surviving episode summaries, observations
and (compacted) stubs within the context budget. The episodes retained by compaction are exactly the
episodes whose content and summaries can be shown to the agent on the next turn; compacted ones become
one-line stubs and can be rehydrated only by an explicit request. Retention is therefore a real causal
input to downstream behaviour.

## 1.6 Existing downstream evaluation signals

| Candidate signal | Verdict |
|---|---|
| `foundations/benchmarks.py` (exact vs greedy PCKP) | Solver objective only. Not downstream performance. |
| `benchmarks/pckp_cases.json` | Same. |
| Trust Profile benchmark (`benchmarks/trust`) | Measures traceability, verifiability, authority and debuggability of *systems*; its runs do not vary memory retention under a fixed task, and its pre-registration and lock must not be touched. |
| `tests/memory/*` | Invariants (closure, mandatory, budget), not task success. |

**No suitable measured downstream signal exists.** A real one needs an agent run on a task under each
candidate retention, scored by an independent verifier. This work therefore (a) defines the executor
interface and refuses to mix measured and synthetic evidence, and (b) reports synthetic-oracle results
separately and labelled; it does not claim measured results.

## 1.7 What did not exist

No learned utility, no preference or outcome data, no feature extractor beyond the five static terms, no
model serialisation, no learned-policy audit record. All of these are new and opt-in.

## 1.8 Backward-compatibility constraints derived from the audit

1. `CompactionStrategy.EXACT_PCKP` stays the default; a new `LEARNED_PCKP` is explicit.
2. A store built without a predictor behaves bit-for-bit as before (test:
   `test_static_default_is_untouched_by_an_attached_predictor`).
3. Mandatory closure, budget and certificate are produced by the same code path
   (`_compact_exact_pckp` is reused; only the utility source differs).
4. The SDK carries no training trigger: compaction never fits a model.
5. No change to the version number, `benchmarks/trust`, its pre-registration or its lock.
