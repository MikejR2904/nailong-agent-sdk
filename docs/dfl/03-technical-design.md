# 3. Technical design: how Ranking-DFL interacts with PCKP

## 3.1 Principle

The machine-learned part estimates **objective coefficients**. The exact solver keeps sole
responsibility for feasibility (budget, prerequisite closure, mandatory retention) and for the optimality
certificate. A neural network never decides retention.

```
                     offline, explicit                         online, opt-in, no training
   candidate sets ──► downstream trials ──► preference pairs ──► train (linear | MLP) ──► artifact (JSON + digest)
   (6 generators,      (matched, repeated,    (significant only,     memory/utility_training.py       │
    all feasible)       measured OR synthetic)  weighted, deduped)                                     ▼
                                                                  compact(policy.strategy=LEARNED_PCKP)
                                                                  ├─ RetentionContext (live records, costs, relevance, query, budget)
                                                                  ├─ LearnedUtilityPredictor.predict  → û_i  (features at selection time only)
                                                                  ├─ resolve_learned_coefficients     → finite check → clip → round → q_i ∈ ℕ
                                                                  ├─ _compact_exact_pckp (unchanged)  → ExactPckpSolver → PckpSolution certificate
                                                                  └─ dossier["learned_ranking"]       → audit record
                                   any predictor failure ───────► static EXACT_PCKP + dossier["learned_ranking"]["status"="fallback-static-pckp"]
```

## 3.2 Components (all new, all opt-in)

| File | Responsibility |
|---|---|
| `memory/utility_features.py` | Schema `episode-utility-features-v1` (13 features in 6 groups), `RetentionContext`, `extract_features`, local hashed-embedding cosine (no external API). |
| `memory/utility_models.py` | `LinearRankingModel`, `MlpRankingModel`, digest-checked JSON artifact (`save_utility_model`, `load_utility_model`, `model_from_artifact`). |
| `memory/utility_training.py` | Pairwise logistic loss, gradient, Hessian, `train_ranking_model` (Newton / Adam). Only entry point to training; nothing in the SDK calls it. |
| `memory/learned_retention.py` | `EpisodeUtilityPredictor` protocol, `LearnedUtilityPredictor`, `QuantizationPolicy`, `resolve_learned_coefficients`. |
| `memory/episode_store.py` | `InMemoryEpisodeStore(utility_predictor=..., utility_quantization=...)`, `_compact_learned_pckp`, shared `_compact_exact_pckp(learned=...)`. `FileEpisodeStore` forwards both arguments. |
| `memory/episode_models.py` | `CompactionStrategy.LEARNED_PCKP`; the policy validator requires a positive static weight for it because the static weights are also the fallback. |
| `benchmarks/dfl/dfl_bench` | Instances, executors, candidates, preferences, splits, methods, experiment, report. Outside `src/`: the SDK carries no benchmark logic. |

## 3.2a Supporting APIs added for production use and for measured evaluation

* `FixedUtilityPredictor(utilities, predictor_id=..., version=...)` serves coefficients computed outside
  the SDK (an offline model, a rule engine, an experiment arm). It performs no inference, validates its
  table, exposes a `prediction_count`, and its artifact digest is the hash of the canonical table. It
  replaced the hand-written predictor test doubles: every predictor in the tests is now a real one
  (this class, or `LearnedUtilityPredictor` over a real model, which fails genuinely when its input
  width is wrong or its coefficients are non-finite).
* `InMemoryEpisodeStore.retain_only(ids, ...)` applies an externally chosen retention set through the
  same leaves-first tombstoning as `compact`, after checking that it names only live episodes, keeps
  every protected, active or open episode and its prerequisites, and keeps each retained episode's
  prerequisites. It is how a candidate set (or a decision made elsewhere) reaches the real context
  projector, and it returns a `CompactionResult` with strategy `EXPLICIT`.

## 3.3 Using it

```python
from nailong_agent_sdk.memory.episode_models import CompactionStrategy, PaskCompactionPolicy
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import LearnedUtilityPredictor

predictor = LearnedUtilityPredictor.from_artifact(path, predictor_id="ranking-dfl-linear", version="2026-10-20")
store = InMemoryEpisodeStore(utility_predictor=predictor)
store.compact(budget, relevance_query=task_text,
              policy=PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP))
```

Without `strategy=LEARNED_PCKP` the predictor is never called (test:
`test_static_default_is_untouched_by_an_attached_predictor`). Training is a separate, explicit step
(`train_ranking_model`, `save_utility_model`), typically offline.

## 3.4 Audit record

`CompactionResult.dossier["learned_ranking"]` for an applied decision:

| Field | Content |
|---|---|
| `status` | `applied` |
| `predictor` | `predictor_id`, `version`, `feature_schema_version`, `artifact_digest` |
| `task_digest` | SHA-256 of the relevance query. The raw task text is never stored by the dossier. |
| `predicted_utilities`, `quantized_utilities` | per-episode real and integer coefficients |
| `quantization`, `clipped_below_zero`, `clipped_above_ceiling` | the policy and every clipped episode |
| `selected_episode_ids`, `selected_token_cost`, `token_budget` | decision and cost |
| `solver_status`, `optimality_gap` | copied from the certificate; the full `PckpSolution` (with `problem_hash`) is in `dossier["solver"]` |
| `guarantee_scope` | states that optimality is for the quantised learned objective only |

The dossier's `strategy` becomes `learned-ranking-precedence-constrained-knapsack` and `objective`
says "additive learned ranking utility". The baseline `static_utilities` stay in the dossier for
comparison. `BaseAgent` emits the same exact-PCKP telemetry metrics for learned decisions.

## 3.5 Failure modes and fallback

All failures return the **static exact-PCKP solution** with `strategy = EXACT_PCKP` and a dossier entry
`learned_ranking = {status: "fallback-static-pckp", requested_strategy, predictor, error_code, error}`.

| Condition | `error_code` | Message names |
|---|---|---|
| strategy requested, no predictor | `UTILITY_PREDICTOR_MISSING` | the missing `utility_predictor` |
| predictor raises | `UTILITY_PREDICTOR_FAILED` | predictor id, version, exception type and text |
| a live episode has no prediction | `UTILITY_PREDICTION_INCOMPLETE` | the missing episode ids |
| NaN, infinity, non-number or bool | `UTILITY_PREDICTION_NONFINITE` | predictor, episode and value |

Artifact errors surface at load time (before any compaction), as `AgentSdkError` with codes
`UTILITY_MODEL_ARTIFACT_MISSING`, `_UNREADABLE`, `_CORRUPT` (bad JSON, not an object, digest mismatch,
bad parameters, inconsistent architecture), `_INCOMPATIBLE` (artifact version),
`UTILITY_MODEL_FEATURE_SCHEMA_MISMATCH`, `UTILITY_MODEL_NONFINITE`.
Mandatory-infeasible budgets are not a predictor failure: they keep the existing
`PROTECTED_OVER_BUDGET` / `CONTEXT_DEADLOCK` outcome with the solver's `infeasible-mandatory` certificate.
`BEST_EFFORT` is preserved and reported (`solver_status` in the learned record).

## 3.5a Scale: when the certificate says best-effort

The learned path inherits the solver's behaviour. Measured on general DAGs (`results/perf/scale.md`,
default `exact_max_branch_nodes = 50,000`): up to about 48 episodes every solve was `optimal` (median
0.4 s at 48); at about 96 and 160 episodes every solve was `best-effort` with a relative bound up to
0.36 (96) and 0.52 (160) for varied learned coefficients, and took 2-5 s. Rooted forests use an exact tree
DP and are unaffected. Consequences for use:

* Check `learned_ranking.solver_status`; `best-effort` means the selection is feasible and the certificate
  gives a bound, but the Theorem 2 `optimal` form does not apply (the `n/s + g/s` form does).
* Raise `PaskCompactionPolicy.exact_max_branch_nodes` to trade time for proof, or accept the bound.
* Latency of the predictor itself is about 0.6-0.9 ms at 12 episodes, small against the solve at scale.

## 3.6 Leakage and data-integrity design (training side)

* Features read only the store state at the time of the call (`RetentionContext`); a test shows that
  adding later episodes does not change earlier episodes' features.
* Splits are by connected groups sharing a template, graph-structure hash or task digest
  (`dfl_bench.splits`); `assert_no_leakage` re-checks template, graph, query and instance identity.
* Hyperparameters (`l2` for the linear model) are chosen on validation preferences only; test instances
  are untouched until the final evaluation.
* Candidates are validated against budget, closure and mandatory retention before any outcome is
  recorded (`assert_feasible`).
* Evidence kinds `synthetic` and `measured` cannot be mixed in one preference set.
* Matched conditions: every candidate for a task is run with the same trial seeds (common random numbers).

## 3.7 Compatibility

* Public API additions only: two optional constructor parameters and one enum member. Existing
  callers are unaffected; the existing strategies' tests pass unchanged.
* `benchmarks/trust`, its pre-registration, lock and results are not read or written by any of this code.
* The package version is not changed.
