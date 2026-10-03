# `memory/` - what the model sees each turn, and what gets forgotten

The model is stateless between calls, so everything it knows on turn *t* is rebuilt from scratch by this folder plus the project-state view. `context.py` builds the fixed initial prompt once. `episodes.py` and `episode_store.py` record one *episode* per tool call and decide, with the PCKP solver, which episodes survive a token budget. `context_projection.py` turns the surviving episodes and the observation history into the bounded `ModelContext` pieces (observations, episode summaries, and one-line stubs for compacted episodes) and owns the tool-result journal that keeps full results outside the context. `context_selection.py` is unrelated to the per-turn loop: it is a pre-run specification selector used by the MCP `select_task_context` tool.

| File | Lines | Role |
|---|---:|---|
| [`memory/__init__.py`](#memory__init__py---package-marker-for-episode-memory-and-context-assembly) | 3 | package marker for episode memory and context assembly |
| [`memory/context.py`](#memorycontextpy---builds-the-fixed-initial-prompt-for-a-run) | 51 | builds the fixed initial prompt for a run |
| [`memory/context_projection.py`](#memorycontext_projectionpy---per-turn-bounded-context-projection-and-the-tool-result-journal) | 327 | per-turn bounded context projection and the tool-result journal |
| [`memory/context_selection.py`](#memorycontext_selectionpy---pre-run-specification-selection-by-design-stage-eda) | 170 | pre-run specification selection by design stage (EDA) |
| [`memory/episode_models.py`](#memoryepisode_modelspy---episode-records-compaction-policy-and-the-retention-contracts) | 176 | episode records, compaction policy and the retention contracts |
| [`memory/episode_scoring.py`](#memoryepisode_scoringpy---deterministic-lexical-relevance-and-cost-helpers-for-retention) | 54 | deterministic lexical relevance and cost helpers for retention |
| [`memory/episode_store.py`](#memoryepisode_storepy---episode-lifecycle-and-the-three-deterministic-compaction-strategies) | 857 | episode lifecycle and the three deterministic compaction strategies |
| [`memory/episodes.py`](#memoryepisodespy---task-scoped-episode-graph-of-one-line-summaries) | 76 | task-scoped episode graph of one-line summaries |

---

### `memory/__init__.py` - package marker for episode memory and context assembly

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `memory/context.py` - builds the fixed initial prompt for a run

*51 lines · depends on: `foundations/contracts.py` · used by: `agent/base_agent/agent.py`, `mcp/agent_tools.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Called once at the top of `BaseAgent.run`. The result is the `prompt` that is reused unchanged on every turn and sent as the byte-identical prefix that provider prompt caches can match.

**Contents**

- `assemble_initial_context(definition: AgentDefinition, task: ScopedAgentTask) -> AgentPrompt` - Returns an `AgentPrompt` with five ordered sections: identity, instructions (version + text), task (scope, locked interface, instructions, acceptance criteria), skills, and the declared tool definitions (name, description, input schema). Values are deep-copied so later mutation cannot change the prompt. · *Called by:* `base_agent/agent.py::BaseAgent.run`, `mcp/agent_tools.py::register_agent_tools.assemble_initial_context_tool`

---

### `memory/context_projection.py` - per-turn bounded context projection and the tool-result journal

*327 lines · depends on: `foundations/contracts.py`, `memory/episode_models.py`, `memory/episode_store.py` · used by: `agent/base_agent/agent.py`, `agent/graph_agent_executor.py`, `agent/runtime.py`, `mcp/agent_tools.py`, `tools/core/services.py`, `tools/registry.py` · re-exported at the package root: 6 name(s)*

**Role in the workflow.** Each loop iteration `BaseAgent.run` calls `ContextProjector.project`, which compacts episodes and returns the observations, episode summaries and compacted-episode stubs that go into the `ModelContext`. After every tool call `project_tool_result` stores the full result in the journal and returns only a bounded preview plus a handle, which is also what the `get_tool_result` core tool later reads.

**Contents**

- **class `ContextProjectionPolicy`** *(pydantic model; bases: StrictModel)* - Budgets for one task's provider-visible context: total context tokens (12,000), episode memory tokens (6,000), tool-result preview characters (1,024), compacted-stub token budget (1,500) and compacted-stub summary characters (160). · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`, `mcp/agent_tools.py::register_agent_tools.run_agent_task`
  - fields: `context_token_budget`, `episode_token_budget`, `tool_result_preview_chars`, `compacted_stub_token_budget`, `compacted_summary_chars`
  - `ContextProjectionPolicy.__init__(**data: Any) -> None` - Validates that the episode budget does not exceed the context budget.
- **class `ToolResultJournal`** *(Protocol; bases: Protocol)* - Protocol for a store of full tool results that must not be replayed verbatim into the context.
  - `ToolResultJournal.record(call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle` - Stores a call/result pair and returns an opaque handle.
  - `ToolResultJournal.read(handle_id: str) -> dict[str, Any]` - Returns the stored call/result payload for a handle id.
- **class `InMemoryToolResultJournal`** *(class)* - Process-local journal: ids `result-1`, `result-2`, ... with a SHA-256 content hash per record. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`
  - `InMemoryToolResultJournal.__init__() -> None` - Starts with no records and the next id at 1.
  - `InMemoryToolResultJournal.record(call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle` - Serializes the call and result to JSON and stores them via `_store`.
  - `InMemoryToolResultJournal.read(handle_id: str) -> dict[str, Any]` - Returns a stored payload or raises for an unknown handle id.
  - `InMemoryToolResultJournal._store(payload: dict[str, Any]) -> ToolResultHandle` - Canonical-encodes the payload, mints the handle (id, hash, byte count) and keeps the payload. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `orchestrator/orchestrator.py::Orchestrator._next_id`, `orchestrator/orchestrator.py::Orchestrator.approve`, `orchestrator/orchestrator.py::Orchestrator.cancel` (+9 more)
- **class `FileToolResultJournal`** *(class; bases: InMemoryToolResultJournal)* - Journal that also writes each record to `<run_root>/.agent-tool-results/<id>.json`, claiming every file exclusively so no instance overwrites another's evidence. · *Instantiated by:* `agent/runtime.py::AgentRuntimeServices.open`
  - `FileToolResultJournal.__init__(run_root: Path) -> None` - Creates the results directory under the run root and numbers new handles after the highest `result-N.json` already there.
  - `FileToolResultJournal.record(call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle` - Builds the payload and claims the next free `result-N.json` by exclusive creation (an id already on disk is skipped), keeps the payload in memory and returns the handle with its content hash and byte count.
  - `FileToolResultJournal.read(handle_id: str) -> dict[str, Any]` - Serves from memory, otherwise loads and caches the JSON file; raises for an unknown handle.
- **class `ContextProjection`** *(dataclass)* - Result of one projection: observations, retained episode summaries, metadata, the compaction result and the compacted-episode stubs. · *Instantiated by:* `memory/context_projection.py::ContextProjector.project`
  - fields: `observations`, `episodes`, `metadata`, `compaction`, `compacted_episodes`
- **class `ContextProjector`** *(class)* - Builds the bounded per-turn view from the immutable prompt, the observation history and the typed episode memory. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`
  - `ContextProjector.__init__(policy: ContextProjectionPolicy) -> None` - Stores the policy.
  - `ContextProjector.policy() -> ContextProjectionPolicy` *(property)* - Property: the active `ContextProjectionPolicy`. · *Called within this file by:* `memory/context_projection.py::ContextProjector.__init__`
  - `ContextProjector.project(prompt: AgentPrompt, observations: Sequence[ModelObservation], episode_summaries: Sequence[EpisodeSummary], memory: InMemoryEpisodeStore, protecte...` - Compacts episodes to the episode budget; on a deadlock returns an empty projection. Otherwise keeps summaries of live episodes, adds a newest-first, budget-limited list of stubs for compacted ones, then fills the remaining context budget with the newest observations whose episodes are still live, counting what it had to omit.
  - `ContextProjector.project_tool_result(call: ToolCall, result: ToolExecutionResult, journal: ToolResultJournal) -> ProjectedToolResult` - Records the full result in the journal and returns status, handle, a size-limited preview (marking truncation) and a truncated error. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`, `base_agent/agent.py::BaseAgent._record_unexecuted_result`
- `_compacted_stubs(summaries: Sequence[EpisodeSummary], observations: Sequence[ModelObservation], summary_chars: int, token_allowance: int) -> tuple[tuple[CompactedE...` - Builds one `CompactedEpisodeStub` per compacted episode (tool name, status, iteration and handle id come from that episode's observation), newest first, keeping as many as fit the token allowance and returning the stubs oldest-first with the omitted count and tokens used. · *Called by:* `memory/context_projection.py::ContextProjector.project`
- `_bounded_preview(value: Any, max_chars: int) -> tuple[Any | None, bool]` - Returns the value unchanged if its canonical JSON fits, else a truncated-preview record that points at the handle. · *Called by:* `memory/context_projection.py::ContextProjector.project_tool_result`
- `_truncate_text(value: str | None, max_chars: int) -> str | None` - Cuts text to a character limit with a truncation marker; passes None through. · *Called by:* `memory/context_projection.py::ContextProjector.project_tool_result`, `memory/context_projection.py::_compacted_stubs`
- `_estimate_tokens(value: Any) -> int` - Heuristic token estimate: canonical JSON length divided by 4 (at least 1). · *Called by:* `memory/context_projection.py::ContextProjector.project`, `memory/context_projection.py::_compacted_stubs`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON with `default=str`. · *Called within this file by:* `memory/context_projection.py::FileToolResultJournal.record`, `memory/context_projection.py::InMemoryToolResultJournal._store`, `memory/context_projection.py::_bounded_preview`, `memory/context_projection.py::_estimate_tokens`
- `_next_handle_number(root: Path) -> int` - One more than the highest `result-N.json` number in a directory (1 for an empty one). · *Called by:* `memory/context_projection.py::FileToolResultJournal.__init__`

**Algorithms & invariants.** Budgets are characters/4 estimates, not provider token counts, and the project-state view is budgeted separately by `ProjectStateProjector`. `FileToolResultJournal` claims each handle file exclusively and numbers new handles after the highest id already on disk, so separate instances and restarts never overwrite earlier evidence.

---

### `memory/context_selection.py` - pre-run specification selection by design stage (EDA)

*170 lines · depends on: `foundations/contracts.py`, `specifications/documents.py`, `specifications/preprocessing.py` · used by: `mcp/specification_tools.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Not part of the per-turn loop. The MCP `select_task_context` tool uses it to pick which specification nodes a task is scoped to: first prune document categories by `DesignStage`, then match scope pointers and task keywords inside the surviving documents.

**Contents**

- **class `DesignStage`** *(enum; bases: StrEnum)* - The EDA design stages (architecture exploration, RTL development/verification, synthesis/DFT, physical design, firmware, safety/security, signoff). · *Instantiated by:* `mcp/specification_tools.py::register_specification_tools.select_task_context`
  - members: `ARCHITECTURE_EXPLORATION`, `RTL_DEVELOPMENT`, `RTL_VERIFICATION`, `SYNTHESIS_DFT`, `PHYSICAL_DESIGN`, `FIRMWARE_DEVELOPMENT`, `SAFETY_SECURITY_ANALYSIS`, `SIGNOFF`
- **class `SelectedContext`** *(pydantic model; bases: StrictModel)* - Selection result: stage, chosen document ids, chosen nodes and the reasons per document. · *Instantiated by:* `memory/context_selection.py::TaskAwareContextSelector.select`, `memory/context_selection.py::TaskAwareContextSelector.select_verified_retrieval_nodes`
  - fields: `stage`, `selected_document_ids`, `nodes`, `selection_reasons`
- **class `TaskAwareContextSelector`** *(class)* - Stage-first selector that never loads all specifications. · *Instantiated by:* `mcp/specification_tools.py::register_specification_tools.select_task_context`
  - `TaskAwareContextSelector.select(trees: list[DocumentTree], stage: DesignStage, task_text: str, scope_pointers: list[str]=()) -> SelectedContext` - Keeps only trees whose category is allowed for the stage, then keeps nodes matching a scope pointer (in location or content) or a task keyword; records the reasons per document.
  - `TaskAwareContextSelector.select_verified_retrieval_nodes(trees: list[DocumentTree], stage: DesignStage, verified_nodes: list[DocumentNode]) -> SelectedContext` - Admits retrieval results only when their (document id, node id, source hash, location) exactly matches a frozen local tree node in an allowed category, so backend text is never trusted. · *No in-package callers (public API, entry point, or protocol hook).*

**Algorithms & invariants.** `STAGE_CATEGORIES` is the stage-to-allowed-specification-category matrix.

*Module-level names:* `STAGE_CATEGORIES`

---

### `memory/episode_models.py` - episode records, compaction policy and the retention contracts

*176 lines · depends on: `foundations/contracts.py` · used by: `agent/base_agent/agent.py`, `memory/context_projection.py`, `memory/episode_scoring.py`, `memory/episode_store.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Typed vocabulary shared by `episodes.py`, `episode_store.py` and the projector: what an episode record holds, how compaction is configured (default strategy: exact PCKP), what a compaction returns, and the extension protocols a host can implement.

**Contents**

- **class `EpisodeState`** *(enum; bases: StrEnum)* - Lifecycle: open, closed, compacted.
  - members: `OPEN`, `CLOSED`, `COMPACTED`
- **class `CompactionStrategy`** *(enum; bases: StrEnum)* - greedy-baseline, PASK (dynamic-diversity heuristic) or exact PCKP (the default).
  - members: `GREEDY_BASELINE`, `PASK`, `EXACT_PCKP`
- **class `EpisodeRecord`** *(pydantic model; bases: StrictModel)* - Durable episode state: id, owner, kind, state, substrate/snapshot info, dependencies in both directions, description, content payload, manifest requirement, tombstone and access statistics. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore._open`
  - fields: `id`, `owner_id`, `kind`, `state`, `substrate_backed`, `snapshot_version`, `depends_on`, `depended_on_by`, `description`, `content`, `requires_manifest`, `eda_manifest`, `tombstone`, `access_count`, `last_access_sequence`
  - `EpisodeRecord.validate_record() -> EpisodeRecord` *(validator)* - Validator: exploratory episodes have no dependencies, substrate-backed ones need a snapshot version, and closed exploratory ones need a description.
- **class `PaskCompactionPolicy`** *(pydantic model; bases: StrictModel)* - Strategy plus the utility weights (relevance 0.40, centrality 0.20, provenance 0.20, recency 0.10, frequency 0.05, diversity 0.05) and the exact-solver node limit (50,000). · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore.__init__`
  - fields: `strategy`, `task_relevance_weight`, `dependency_centrality_weight`, `provenance_weight`, `recency_weight`, `frequency_weight`, `diversity_weight`, `exact_max_branch_nodes`
  - `PaskCompactionPolicy.has_positive_weight() -> PaskCompactionPolicy` *(validator)* - Validator: at least one weight is positive, and exact PCKP needs a positive additive weight.
- **class `EpisodeRelevanceScorer`** *(Protocol; bases: Protocol)* - Host extension point: a deterministic score of how relevant an episode is to the task text.
  - `EpisodeRelevanceScorer.score(query: str, episode: EpisodeRecord) -> float` - Protocol method returning a relevance value for (query, episode).
- **class `CompactionStatus`** *(enum; bases: StrEnum)* - compacted, protected-over-budget, context-deadlock or within-budget.
  - members: `COMPACTED`, `PROTECTED_OVER_BUDGET`, `CONTEXT_DEADLOCK`, `WITHIN_BUDGET`
- **class `EpisodeUtility`** *(pydantic model; bases: StrictModel)* - Auditable per-episode score breakdown (relevance, centrality, provenance, recency, frequency, diversity) with cost and dependency closure. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore._static_utility`, `memory/episode_store.py::InMemoryEpisodeStore._utility`
  - fields: `episode_id`, `utility`, `utility_to_cost`, `marginal_token_cost`, `task_relevance`, `dependency_centrality`, `provenance`, `recency`, `frequency`, `diversity`, `dependency_closure`
- **class `CompactionResult`** *(pydantic model; bases: StrictModel)* - Outcome of one compaction: status, token counts before/after, ids compacted in this call, blocked ids, strategy and a decision dossier. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore.compact`
  - fields: `status`, `before_tokens`, `after_tokens`, `compacted_episode_ids`, `blocked_episode_ids`, `strategy`, `dossier`
- **class `EpisodeCheckpoint`** *(pydantic model; bases: StrictModel)* - Structural-only snapshot (graph hash plus per-episode structure) used to verify memory integrity. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore.checkpoint`
  - fields: `schema_version`, `graph_hash`, `episodes`
- **class `EpisodeStore`** *(Protocol; bases: Protocol)* - Protocol of the minimal episode-store operations.
  - `EpisodeStore.open_exploratory(owner_id: str, *, substrate_backed: bool=False, snapshot_version: str | None=None, content: dict[str, Any] | None=None) -> EpisodeRecord` - Protocol method: open an exploratory episode.
  - `EpisodeStore.open_action(owner_id: str, dependencies: list[str], *, content: dict[str, Any] | None=None, requires_manifest: bool=False, eda_manifest: dict[str, Any] | None...` - Protocol method: open an action episode depending on closed exploratory ones.
  - `EpisodeStore.close(episode_id: str, *, description: str | None=None) -> EpisodeRecord` - Protocol method: close an episode.
  - `EpisodeStore.list() -> list[EpisodeRecord]` - Protocol method: list all episode records.

---

### `memory/episode_scoring.py` - deterministic lexical relevance and cost helpers for retention

*54 lines · depends on: `foundations/contracts.py`, `memory/episode_models.py` · used by: `memory/episode_store.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Supplies the default relevance scorer and the token-cost estimate that `episode_store.py` feeds into the PCKP and PASK algorithms.

**Contents**

- **class `LexicalEpisodeRelevanceScorer`** *(class)* - Dependency-free default scorer: the fraction of the query's terms that appear in the episode. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore.__init__`
  - `LexicalEpisodeRelevanceScorer.score(query: str, episode: EpisodeRecord) -> float` - Extracts lowercase alphanumeric terms (length > 1) from the query and the episode and returns overlap divided by the query size (0 for an empty query).
- `_episode_tokens(record: EpisodeRecord) -> int` - Token cost of an episode: JSON length of its content (or description once compacted) divided by 4, at least 1. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._static_utility`, `memory/episode_store.py::InMemoryEpisodeStore._tokens_for`, `memory/episode_store.py::InMemoryEpisodeStore.estimate_tokens`
- `_terms(value: str) -> set[str]` - Set of lowercase `[a-z0-9_]+` terms longer than one character. · *Called within this file by:* `memory/episode_scoring.py::LexicalEpisodeRelevanceScorer.score`, `memory/episode_scoring.py::_episode_terms`
- `_episode_terms(record: EpisodeRecord) -> set[str]` - Terms from the episode's description plus the first 16,384 characters of its content JSON. · *Called by:* `memory/episode_scoring.py::LexicalEpisodeRelevanceScorer.score`, `memory/episode_store.py::InMemoryEpisodeStore._terms_for`
- `_provenance_weight(record: EpisodeRecord) -> float` - Trust prior by provenance: substrate-backed 1.0, exploratory 0.65, action with a manifest 0.20, other action 0.40. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._static_utility`, `memory/episode_store.py::InMemoryEpisodeStore._utility`
- `_clamp_score(value: float) -> float` - Clamps a score into [0, 1]. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._static_utility`, `memory/episode_store.py::InMemoryEpisodeStore._utility`

---

### `memory/episode_store.py` - episode lifecycle and the three deterministic compaction strategies

*857 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py`, `foundations/optimization/__init__.py`, `memory/episode_models.py`, `memory/episode_scoring.py` · used by: `agent/base_agent/agent.py`, `agent/graph_agent_executor.py`, `agent/runtime.py`, `memory/context_projection.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `BaseAgent` opens one episode per tool call (exploratory read or action), closes it, and the projector calls `compact` every turn with the episode token budget and the latest tool batch protected. Compacted episodes are replaced by structural tombstones; their raw evidence stays in the tool-result journal.

**Contents**

- **class `InMemoryEpisodeStore`** *(class)* - Enforces the episode rules and retention: action episodes depend only on closed exploratory ones, and compaction never breaks a retained dependency closure.
  - `InMemoryEpisodeStore.__init__(*, compaction_policy: PaskCompactionPolicy | None=None, relevance_scorer: EpisodeRelevanceScorer | None=None) -> None` - Stores the compaction policy and relevance scorer (defaults: exact PCKP and the lexical scorer).
  - `InMemoryEpisodeStore.compaction_policy() -> PaskCompactionPolicy` *(property)* - Property: the default `PaskCompactionPolicy`. · *Called by:* `memory/episode_store.py::FileEpisodeStore.__init__`, `memory/episode_store.py::InMemoryEpisodeStore.__init__`
  - `InMemoryEpisodeStore.open_exploratory(owner_id: str, *, substrate_backed: bool=False, snapshot_version: str | None=None, content: dict[str, Any] | None=None) -> EpisodeRecord` - Opens an exploratory episode (optionally substrate-backed with a snapshot version).
  - `InMemoryEpisodeStore.open_action(owner_id: str, dependencies: list[str], *, content: dict[str, Any] | None=None, requires_manifest: bool=False, eda_manifest: dict[str, Any] | None...` - Opens an action episode; dependencies must be unique, known, closed exploratory episodes, and the dependencies learn their dependent.
  - `InMemoryEpisodeStore.close(episode_id: str, *, description: str | None=None) -> EpisodeRecord` - Closes an open episode; exploratory episodes need a non-empty description.
  - `InMemoryEpisodeStore.attach_eda_manifest(episode_id: str, manifest: dict[str, Any]) -> EpisodeRecord` - Attaches an EDA manifest to an action episode, making a manifest-requiring episode compactable. · *No in-package callers (public API, entry point, or protocol hook).*
  - `InMemoryEpisodeStore.mark_accessed(episode_ids: Iterable[str]) -> None` - Bumps access count and recency sequence for consumed episodes (inputs to the utility score); refuses compacted episodes. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`
  - `InMemoryEpisodeStore.list() -> list[EpisodeRecord]` - All records sorted by id. · *Called within this file by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore._compact_unretained`, `memory/episode_store.py::InMemoryEpisodeStore._dependency_closure` (+4 more)
  - `InMemoryEpisodeStore.get(episode_id: str) -> EpisodeRecord | None` - One record or None. · *Called within this file by:* `memory/episode_store.py::InMemoryEpisodeStore._require`, `memory/episode_store.py::InMemoryEpisodeStore.open_action`
  - `InMemoryEpisodeStore.estimate_tokens() -> int` - Total token cost of all non-compacted episodes. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore.compact`
  - `InMemoryEpisodeStore.compact(token_budget: int, *, active_episode_id: str | None=None, protected_episode_ids: Iterable[str]=(), relevance_query: str='', policy: PaskCompaction...` - Entry point: returns WITHIN_BUDGET if already under the budget, otherwise dispatches to the greedy, exact-PCKP or PASK strategy. · *Called by:* `memory/context_projection.py::ContextProjector.project`
  - `InMemoryEpisodeStore.checkpoint() -> EpisodeCheckpoint` - Builds a structural-only checkpoint (per-episode structure, no content) with a SHA-256 graph hash. · *Called by:* `memory/episode_store.py::FileEpisodeStore.persist_checkpoint`, `memory/episode_store.py::InMemoryEpisodeStore.verify_checkpoint`
  - `InMemoryEpisodeStore.verify_checkpoint(checkpoint: EpisodeCheckpoint) -> bool` - True if the current structure and hash equal a given checkpoint. · *No in-package callers (public API, entry point, or protocol hook).*
  - `InMemoryEpisodeStore._compact_greedy(token_budget: int, active_episode_id: str | None, protected_episode_ids: set[str], before: int) -> CompactionResult` - Baseline: repeatedly tombstone the next eligible closed leaf (actions first, then substrate-backed, then others) until under budget, reporting protected-over-budget or deadlock if nothing is eligible. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore.compact`
  - `InMemoryEpisodeStore._compact_exact_pckp(token_budget: int, *, active_episode_id: str | None, protected_episode_ids: set[str], relevance_query: str, policy: PaskCompactionPolicy, before: ...` - Default: computes the mandatory closure (protected, active, open and manifest-incomplete episodes plus prerequisites), scores each episode with an additive static utility, solves the dependency-closed knapsack exactly, and compacts everything not selected, recording the solver certificate in the dossier. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore.compact`
  - `InMemoryEpisodeStore._compact_pask(token_budget: int, *, active_episode_id: str | None, protected_episode_ids: set[str], relevance_query: str, policy: PaskCompactionPolicy, before: ...` - PASK: starts from the mandatory closure and greedily adds the dependency closure with the best marginal utility-to-cost until nothing fits, then compacts the rest; diversity (new terms covered) makes the objective non-additive. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore.compact`
  - `InMemoryEpisodeStore._static_utility(episode_id: str, relevance_query: str, policy: PaskCompactionPolicy, live_ids: set[str], max_dependents: int, max_access_count: int, max_access_se...` - Weighted sum of relevance, dependency centrality, provenance, recency and frequency for one episode (no diversity, so it is additive). · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`
  - `InMemoryEpisodeStore._utility(episode_id: str, additions: set[str], covered_terms: set[str], relevance_query: str, policy: PaskCompactionPolicy, marginal_cost: int, live_ids: s...` - Marginal utility of a PASK candidate including the diversity term (share of its terms not yet covered). · *Called within this file by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`
  - `InMemoryEpisodeStore._dependency_closure(seeds: set[str], live_ids: set[str]) -> set[str]` - All prerequisites of the seeds; raises if a live episode would depend on a compacted one. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore._protected_tokens`, `memory/episode_store.py::InMemoryEpisodeStore._static_utility`
  - `InMemoryEpisodeStore._compact_unretained(unretained: set[str]) -> list[str]` - Tombstones the unretained set leaves-first so retained dependency closures stay intact. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`
  - `InMemoryEpisodeStore._open(owner_id: str, kind: EpisodeKind, *, dependencies: list[str] | None=None, substrate_backed: bool=False, snapshot_version: str | None=None, content...` - Allocates the next `episode-N` id and stores a new open record. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore.open_action`, `memory/episode_store.py::InMemoryEpisodeStore.open_exploratory`
  - `InMemoryEpisodeStore._eligible(active_episode_id: str | None, protected_episode_ids: set[str]) -> Iterable[EpisodeRecord]` - Greedy-baseline candidates: closed, unprotected, not active, no dependents, not manifest-incomplete. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`
    - `InMemoryEpisodeStore._eligible.priority(record: EpisodeRecord) -> tuple[int, str]` - Sort key: action episodes first, then substrate-backed, then other exploratory, each by id. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._eligible`
  - `InMemoryEpisodeStore._compact_record(episode_id: str) -> None` - Marks one record compacted, drops its content, sets its tombstone and removes it from its dependencies' dependents. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`, `memory/episode_store.py::InMemoryEpisodeStore._compact_unretained`
  - `InMemoryEpisodeStore._tokens_for(episode_ids: Iterable[str]) -> int` - Token cost of a set of episodes. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore._protected_tokens`
  - `InMemoryEpisodeStore._protected_tokens(protected: set[str], active_episode_id: str | None) -> int` - Token cost of the protected and active episodes together with their closure. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_greedy`
  - `InMemoryEpisodeStore._terms_for(episode_ids: Iterable[str]) -> set[str]` - Union of the lexical terms of a set of episodes. · *Called by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore._utility`
  - `InMemoryEpisodeStore._require(episode_id: str) -> EpisodeRecord` - Returns a record or raises for an unknown id. · *Called within this file by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `memory/episode_store.py::InMemoryEpisodeStore._compact_pask`, `memory/episode_store.py::InMemoryEpisodeStore._compact_record`, `memory/episode_store.py::InMemoryEpisodeStore._compact_unretained` (+9 more)
- **class `FileEpisodeStore`** *(class; bases: InMemoryEpisodeStore)* - Episode store that can persist records and structural checkpoints under `<run_root>/.agent-memory`.
  - `FileEpisodeStore.__init__(run_root: Path, *, compaction_policy: PaskCompactionPolicy | None=None, relevance_scorer: EpisodeRelevanceScorer | None=None) -> None` - Creates the memory directory and the file paths.
  - `FileEpisodeStore.persist() -> None` - Writes all episode records to `episodes.json` atomically (there is no matching load for the records).
  - `FileEpisodeStore.persist_checkpoint() -> EpisodeCheckpoint` - Writes the current structural checkpoint to `checkpoint.json` and returns it. · *No in-package callers (public API, entry point, or protocol hook).*
  - `FileEpisodeStore.load_checkpoint() -> EpisodeCheckpoint` - Reads the persisted checkpoint or raises if none exists. · *No in-package callers (public API, entry point, or protocol hook).*
  - `FileEpisodeStore._atomic_write(path: Path, content: str) -> None` *(staticmethod)* - Writes a temp file then `replace_atomic`s it. · *Called within this file by:* `memory/episode_store.py::FileEpisodeStore.persist`, `memory/episode_store.py::FileEpisodeStore.persist_checkpoint`

**Algorithms & invariants.** Mandatory (never compacted): the active and protected episodes, open episodes, and action episodes whose tool declared `requires_manifest` and have no manifest yet, plus all their prerequisites. If that closure alone exceeds the budget the result is PROTECTED_OVER_BUDGET (protected set present) or CONTEXT_DEADLOCK, which ends the run as BLOCKED. Exact PCKP is additive; PASK adds a diversity term that depends on already-chosen episodes, so it is a greedy heuristic with no optimality claim.

---

### `memory/episodes.py` - task-scoped episode graph of one-line summaries

*76 lines · depends on: `foundations/contracts.py`, `foundations/errors.py` · used by: `agent/base_agent/agent.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `BaseAgent` adds one summary per tool call here; the list is what the projector filters into `episode_summaries` (live) and into stubs (compacted), and what ends up in `AgentResult.episodes`.

**Contents**

- **class `InMemoryEpisodeGraph`** *(class)* - Holds `EpisodeSummary` records and payloads; enforces that action episodes depend only on exploratory episodes. · *Instantiated by:* `base_agent/agent.py::BaseAgent.run`
  - `InMemoryEpisodeGraph.__init__(now: Callable[[], datetime] | None=None) -> None` - Takes an injectable clock for deterministic timestamps.
  - `InMemoryEpisodeGraph.add_exploratory(summary: str, payload: Any=None) -> EpisodeSummary` - Adds an exploratory episode with no dependencies. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`
  - `InMemoryEpisodeGraph.add_action(summary: str, consumed_episode_ids: list[str] | None=None, payload: Any=None) -> EpisodeSummary` - Adds an action episode after checking each consumed episode exists and is exploratory (`EPISODE_GRAPH_INVARIANT` otherwise). · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`
  - `InMemoryEpisodeGraph.list() -> list[EpisodeSummary]` - All summaries in insertion (chronological) order. · *Called within this file by:* `memory/episodes.py::InMemoryEpisodeGraph._add`
  - `InMemoryEpisodeGraph._add(kind: EpisodeKind, summary: str, dependency_ids: list[str], payload: Any) -> EpisodeSummary` - Rejects an empty summary, assigns `episode-N` and a timestamp, and stores the summary and payload. · *Called by:* `memory/episodes.py::InMemoryEpisodeGraph.add_action`, `memory/episodes.py::InMemoryEpisodeGraph.add_exploratory`

