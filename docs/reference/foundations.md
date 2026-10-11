# `foundations/` - dependency-free primitives every other folder builds on

Layer 0 of the package: nothing here imports from another SDK folder. It holds the serializable contracts (`contracts.py`, 62 modules depend on it), the typed error model and secret redaction (`errors.py`), crash-safe file publication, exclusive claims and cross-process locks (`atomic_io.py`), per-record locks for the stores (`record_locks.py`), identifier validation and injective file names (`identifiers.py`), payload depth bounds (`json_limits.py`), newline-only text helpers (`text.py`), comparison of Windows path spellings (`paths.py`), canonical JSON, digests and the token estimate (`hashing.py`), graph traversal helpers, opt-in logging, and the PCKP (precedence-constrained knapsack) solvers that the episode compactor and the evidence packer use to decide what to keep under a token budget.

| File | Lines | Role |
|---|---:|---|
| [`foundations/__init__.py`](#foundations__init__py---package-marker-for-the-dependency-free-layer) | 4 | package marker for the dependency-free layer |
| [`foundations/atomic_io.py`](#foundationsatomic_iopy---crash-safe-file-publication-exclusive-claims-and-cross-process-locks) | 158 | crash-safe file publication, exclusive claims and cross-process locks |
| [`foundations/benchmarks.py`](#foundationsbenchmarkspy---reproducible-exact-vs-greedy-pckp-benchmark-harness) | 88 | reproducible exact-vs-greedy PCKP benchmark harness |
| [`foundations/contracts.py`](#foundationscontractspy---the-serializable-baseagent-contract-definitions-tasks-turns-results-context-types) | 526 | the serializable BaseAgent contract: definitions, tasks, turns, results, context types |
| [`foundations/dependency_graph.py`](#foundationsdependency_graphpy---deterministic-cycle-and-blast-radius-traversal-over-dependent-prerequisite-edges) | 104 | deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges |
| [`foundations/detached.py`](#foundationsdetachedpy---run-a-blocking-call-on-a-daemon-thread-that-the-awaiting-task-may-abandon) | 42 | run a blocking call on a daemon thread that the awaiting task may abandon |
| [`foundations/errors.py`](#foundationserrorspy---typed-sdk-errors-and-secretreasoning-redaction-for-durable-records) | 284 | typed SDK errors and secret/reasoning redaction for durable records |
| [`foundations/hashing.py`](#foundationshashingpy---canonical-json-encodings-sha-256-digests-and-the-token-estimate) | 56 | canonical JSON encodings, SHA-256 digests and the token estimate |
| [`foundations/identifiers.py`](#foundationsidentifierspy---identifier-validation-injective-file-names-and-collision-free-sequential-ids) | 90 | identifier validation, injective file names and collision-free sequential ids |
| [`foundations/json_limits.py`](#foundationsjson_limitspy---depth-bound-for-untrusted-json-like-payloads) | 28 | depth bound for untrusted JSON-like payloads |
| [`foundations/logging.py`](#foundationsloggingpy---opt-in-stdlib-logging-namespace-for-the-few-paths-outside-structured-telemetry) | 25 | opt-in stdlib logging namespace for the few paths outside structured telemetry |
| [`foundations/optimization/__init__.py`](#foundationsoptimization__init__py---re-exports-the-pckp-models-and-solvers) | 17 | re-exports the PCKP models and solvers |
| [`foundations/optimization/models.py`](#foundationsoptimizationmodelspy---pckp-problem-item-and-solution-contracts) | 90 | PCKP problem, item and solution contracts |
| [`foundations/optimization/solvers.py`](#foundationsoptimizationsolverspy---exact-tree-dp--branch-and-bound-and-greedy-pckp-solvers) | 503 | exact (tree DP / branch-and-bound) and greedy PCKP solvers |
| [`foundations/paths.py`](#foundationspathspy---comparison-of-windows-extended-length-path-spellings-as-one-location) | 40 | comparison of Windows extended-length path spellings as one location |
| [`foundations/record_locks.py`](#foundationsrecord_lockspy---per-record-cross-process-locks-re-entrant-for-the-holding-thread) | 62 | per-record cross-process locks, re-entrant for the holding thread |
| [`foundations/text.py`](#foundationstextpy---newline-only-line-splitting-and-utf-8-well-formedness-for-durable-text) | 50 | newline-only line splitting and UTF-8 well-formedness for durable text |
| [`foundations/version.py`](#foundationsversionpy---the-installed-package-name-and-version-and-the-identity-strings-derived-from-them) | 23 | the installed package name and version, and the identity strings derived from them |

---

### `foundations/__init__.py` - package marker for the dependency-free layer

*4 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only; it re-exports nothing. Importers always use the deep module paths.

---

### `foundations/atomic_io.py` - crash-safe file publication, exclusive claims and cross-process locks

*158 lines · depends on: `foundations/errors.py` · used by: `agent/orchestrator/state_store.py`, `agent/retention.py`, `developer_tools/catalog.py`, `foundations/benchmarks.py`, `foundations/identifiers.py`, `foundations/record_locks.py`, `memory/context_projection.py`, `memory/episode_store.py` (+9 more) · not re-exported at the package root*

**Role in the workflow.** Every durable store in the SDK (project state, run records, telemetry reports, audit transcripts, tool-result journal, orchestration records) writes a uniquely named temporary file (`unique_temporary_path`) first and then calls `replace_atomic`, so a reader never sees a half-written file and two writers never share a temporary name. `claim_exclusive` is the primitive behind collision-free id reservation, `exclusive_file_lock` serializes a critical section across processes, `read_text_retrying` reads through the brief sharing violations a Windows reader sees while a writer replaces the file, and `file_fingerprint` is the version stamp a store compares to notice that another writer replaced a record (`record_locks.py` builds the per-record locks on `exclusive_file_lock`).

**Contents**

- `file_fingerprint(path: Path) -> Fingerprint | None` - Cheap identity of a file: `(inode, modification time in ns, size)`, or None when it does not exist. A store keeps the value it saw when it last read or wrote a record and compares it before saving, which is how a change made by another writer is noticed; replacing a file (`replace_atomic`) always changes the inode or the time. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.fingerprint`, `orchestrator/state_store.py::OrchestrationStateStore.load`, `state/orchestration.py::ControllerStateStore.fingerprint`, `state/orchestration.py::ControllerStateStore.load` (+5 more)
- `replace_atomic(temporary: Path, target: Path, *, attempts: int=5) -> None` - `os.replace`s the temporary file over the target. Only `PermissionError` is retried (up to `attempts`, sleeping 10 ms x attempt number) because on Windows a destination handle held briefly by another reader or by antivirus makes the rename fail transiently; any other error, and the final failed attempt, propagates. Rejects `attempts < 1`. The caller must have written and flushed the temp file. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.save`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `developer_tools/catalog.py::write_public_api_catalog`, `foundations/benchmarks.py::write_pckp_benchmark_report` (+11 more)
- `read_text_retrying(path: Path, *, attempts: int=10) -> str` - Reads a UTF-8 text file, retrying only `PermissionError` (up to `attempts` times, sleeping 5 ms x attempt number) because on Windows a reader can be refused while a writer is replacing the file; the last attempt's error propagates. Rejects `attempts < 1`.
- `unique_temporary_path(target: Path) -> Path` - Returns a hidden sibling of the target named `.<name>.<pid>.<12 hex>.tmp`, so concurrent writers in one or many processes never collide on a temporary file. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.save`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `developer_tools/catalog.py::write_public_api_catalog`, `foundations/benchmarks.py::write_pckp_benchmark_report` (+12 more)
- `claim_exclusive(path: Path) -> bool` - Creates the file with `O_CREAT | O_EXCL` (making its parent directory first) and returns True, or False if it already exists: an atomic claim that two racing callers can never both win.
- `exclusive_file_lock(path: Path, *, timeout_seconds: float=30.0, timeout_code: str='FILE_LOCK_TIMEOUT') -> Iterator[None]` *(contextmanager)* - Context manager that holds an operating-system lock on a dedicated lock file (`flock` on POSIX, byte-range locking on Windows). Both platforms poll a non-blocking attempt every 5 ms and raise `AgentSdkError` with the caller's `timeout_code` after `timeout_seconds` (default 30 s), naming the lock file and the likely causes. · *Called by:* `agent/retention.py::RunRetention.prune`, `foundations/record_locks.py::RecordLocks.hold`, `observability/audit_log.py::AuditTranscriptStore._append_transaction`, `tools/approvals.py::ApprovalRegistry._transaction`
- `_lock_timeout(path: Path, timeout_seconds: float, timeout_code: str) -> AgentSdkError` - The `AgentSdkError(timeout_code)` both platforms raise when a lock stays held: it names the lock file and the wait, lists the likely causes (a writer hung mid-operation or software scanning the file) and carries the lock path and timeout in its details. · *Called by:* `foundations/atomic_io.py::_acquire_posix_lock`, `foundations/atomic_io.py::_acquire_windows_lock`
- `_acquire_posix_lock(descriptor: int, path: Path, timeout_seconds: float, timeout_code: str) -> None` - Polls a non-blocking `flock` on the descriptor until `timeout_seconds` elapse, then raises the `_lock_timeout` error; a writer that hangs while holding the lock no longer blocks every other process for ever. · *Called by:* `foundations/atomic_io.py::exclusive_file_lock`
- `_release_posix_lock(descriptor: int) -> None` - Unlocks the descriptor locked by `_acquire_posix_lock`. · *Called by:* `foundations/atomic_io.py::exclusive_file_lock`
- `_acquire_windows_lock(descriptor: int, path: Path, timeout_seconds: float, timeout_code: str) -> None` - Polls a non-blocking lock on byte 0 until `timeout_seconds` elapse, then raises the `_lock_timeout` error.
- `_release_windows_lock(descriptor: int) -> None` - Unlocks the byte locked by `_acquire_windows_lock`.

**Algorithms & invariants.** Atomic replace gives all-or-nothing *visibility* of the new content; it does not fsync the temp file or the directory, so it is not a power-loss durability guarantee.

---

### `foundations/benchmarks.py` - reproducible exact-vs-greedy PCKP benchmark harness

*88 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py`, `foundations/optimization/__init__.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 6 name(s)*

**Role in the workflow.** Used only by tooling (`scripts/run_pckp_benchmark.py` and the root exports). It runs both PCKP solvers on identical frozen inputs so the quality gap between the exact compactor and the greedy baseline can be measured and compared across code changes.

**Contents**

- **class `PckpBenchmarkCase`** *(pydantic model; bases: StrictModel)* - One frozen benchmark input: an id, a full `PckpProblem`, and a human description.
  - fields: `case_id`, `problem`, `description`
- **class `PckpBenchmarkResult`** *(pydantic model; bases: StrictModel)* - Both solver certificates for one case plus wall-clock nanoseconds for each and `retained_utility_gap` (exact utility minus greedy utility). · *Instantiated by:* `foundations/benchmarks.py::run_pckp_benchmark`
  - fields: `case_id`, `problem_hash`, `exact`, `greedy`, `exact_elapsed_ns`, `greedy_elapsed_ns`, `retained_utility_gap`
- **class `PckpBenchmarkReport`** *(pydantic model; bases: StrictModel)* - Versioned container (`pckp-benchmark-v1`) of all case results; the JSON that gets written to disk. · *Instantiated by:* `foundations/benchmarks.py::run_pckp_benchmark`
  - fields: `schema_version`, `results`
- `load_pckp_cases(path: Path) -> list[PckpBenchmarkCase]` - Reads a JSON file that must contain a list and validates every element as a `PckpBenchmarkCase`. · *No in-package callers (public API, entry point, or protocol hook).*
- `run_pckp_benchmark(cases: list[PckpBenchmarkCase]) -> PckpBenchmarkReport` - Runs `ExactPckpSolver` and `GreedyPckpBaseline` on each case (sorted by case id for a stable order), timing each with `perf_counter_ns`, and records the utility gap. · *No in-package callers (public API, entry point, or protocol hook).*
- `write_pckp_benchmark_report(report: PckpBenchmarkReport, path: Path) -> None` - Writes the report as sorted-key indented JSON through a unique temporary file and `replace_atomic`. · *No in-package callers (public API, entry point, or protocol hook).*

---

### `foundations/contracts.py` - the serializable BaseAgent contract: definitions, tasks, turns, results, context types

*526 lines · depends on: `foundations/dependency_graph.py`, `foundations/errors.py`, `foundations/identifiers.py`, `foundations/json_limits.py` · used by: `agent/base_agent/agent.py`, `agent/base_agent/types.py`, `agent/graph_agent_executor.py`, `agent/model.py`, `agent/openai_compatible/chat.py`, `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `agent/retention.py` (+52 more) · re-exported at the package root: 17 name(s)*

**Role in the workflow.** The shared vocabulary of the whole SDK. A host builds an `AgentDefinition` (data only) and a `ScopedAgentTask`; `BaseAgent` validates them, asks the model for `AgentTurn`s (tool call, tool batch, final, blocked), executes tools producing `ToolExecutionResult`s, records `ModelObservation`s/`EpisodeSummary`s, and returns an `AgentResult` carrying lifecycle events and projection metadata. Every model adapter, store, MCP tool and integration exchanges these types, which is why it is the most depended-on module.

**Contents**

- **class `StrictModel`** *(pydantic model; bases: BaseModel)* - Pydantic base class with `extra='forbid'`: unknown fields in any public contract are rejected instead of silently dropped.
  - fields: `model_config`
- **class `MemoryScope`** *(enum; bases: StrEnum)* - How long an agent's memory lives: none, task-scoped (default), or cross-session (requires an injected persistent store and a rationale).
  - members: `NONE`, `TASK_SCOPED`, `CROSS_SESSION`
- **class `EscalationTarget`** *(enum; bases: StrEnum)* - Where a non-completed run escalates: none, the controller, or a human.
  - members: `NONE`, `CONTROLLER`, `HUMAN`
- **class `EpisodeKind`** *(enum; bases: StrEnum)* - Exploratory (read-only observation) versus action (state-changing) episodes; drives the episode-graph rules.
  - members: `EXPLORATORY`, `ACTION`
- **class `ToolConcurrency`** *(enum; bases: StrEnum)* - Whether a tool may run in parallel with other ready calls in one model batch (`parallel-safe`) or must run alone (`serial`, the default).
  - members: `SERIAL`, `PARALLEL_SAFE`
- **class `AgentRunStatus`** *(enum; bases: StrEnum)* - Terminal states of a run: completed, blocked, failed, cancelled.
  - members: `COMPLETED`, `BLOCKED`, `FAILED`, `CANCELLED`
- **class `VersionedInstructions`** *(pydantic model; bases: StrictModel)* - Agent instruction text plus a version string (the version is part of node provenance and binding hashes).
  - fields: `version`, `text`
- **class `FallbackModelBinding`** *(pydantic model; bases: StrictModel)* - A provider/model pair plus provider parameters; one entry in a failover chain.
  - fields: `provider`, `model`, `parameters`
- **class `ModelBinding`** *(class; bases: FallbackModelBinding)* - The primary model binding plus ordered fallbacks; the adapter checks it against the client it was given. · *Instantiated by:* `agent/model.py::FailoverAgentModel._context_for`
  - fields: `fallbacks`
  - `ModelBinding.fallback_bindings_are_distinct() -> ModelBinding` *(validator)* - Validator: no fallback may duplicate the primary (or another fallback) provider/model pair.
- **class `TerminationPolicy`** *(pydantic model; bases: StrictModel)* - Hard iteration cap, the output field that carries the status, and the escalation target for non-completed runs.
  - fields: `max_iterations`, `status_field`, `escalation`
- **class `ToolDefinition`** *(pydantic model; bases: StrictModel)* - A tool the agent may call: name, description, JSON input schema, episode kind, and concurrency. · *Instantiated by:* `agent/task_runner.py::_mcp_tool_definition`, `core/definitions.py::core_tool_definitions.definition`
  - fields: `name`, `description`, `input_schema`, `episode_kind`, `concurrency`
  - `ToolDefinition.validate_input_schema(schema: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the tool's input schema must itself be a valid Draft 2020-12 JSON Schema.
- **class `AgentDefinition`** *(pydantic model; bases: StrictModel)* - The data-only agent description: identity, instructions, input/output schemas, tools, model binding, memory scope, termination policy and optional verification gate id. Runtime dependencies (model, executor, stores) are injected separately.
  - fields: `identity`, `instructions`, `input_schema`, `tools`, `model_binding`, `output_schema`, `memory_scope`, `memory_rationale`, `termination_policy`, `verification_gate_id`
  - `AgentDefinition.identity_is_one_line(identity: str) -> str` *(validator, classmethod)* - Validator: identity must be a single line (it is used in telemetry and prompts).
  - `AgentDefinition.validate_contract_schema(schema: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: input and output schemas must be valid Draft 2020-12 JSON Schemas.
  - `AgentDefinition.validate_definition_invariants() -> AgentDefinition` *(validator)* - Validator: tool names are unique, and cross-session memory requires a written `memory_rationale`.
- **class `TaskScope`** *(pydantic model; bases: StrictModel)* - A human label for the task plus free-form `boundaries` (BaseAgent reads `project_id` and `stage` from here).
  - fields: `label`, `boundaries`
- **class `SkillContext`** *(pydantic model; bases: StrictModel)* - A versioned skill snippet matched to the task and placed in the prompt's skills section.
  - fields: `id`, `version`, `content`
- **class `ScopedAgentTask`** *(pydantic model; bases: StrictModel)* - One unit of work: id, validated input payload, scope, locked interface, instructions, acceptance criteria and skills. Never a project transcript.
  - fields: `id`, `input`, `scope`, `locked_interface`, `instructions`, `acceptance_criteria`, `skills`
  - `ScopedAgentTask.criteria_are_nonempty(criteria: list[str]) -> list[str]` *(validator, classmethod)* - Validator: no acceptance criterion may be blank.
- **class `ToolCall`** *(pydantic model; bases: StrictModel)* - One requested tool invocation: id, tool name, arguments, the exploratory episodes it consumed, and in-batch dependencies.
  - fields: `id`, `name`, `arguments`, `consumed_episode_ids`, `depends_on_call_ids`
  - `ToolCall.arguments_are_bounded(arguments: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: call arguments nest at most 64 levels (`assert_json_depth`), so a hostile payload cannot make pydantic serialization fail later.
  - `ToolCall.dependencies_are_unique_and_external() -> ToolCall` *(validator)* - Validator: dependency ids are unique and a call cannot depend on itself.
- **class `AgentFailure`** *(pydantic model; bases: StrictModel)* - Bounded, redacted failure record (stable `code`, human `message`, sanitized `details`) attached to results and tool results. · *Instantiated by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent.run`, `base_agent/agent.py::_state_update_failure` (+2 more)
  - fields: `code`, `message`, `details`
  - `AgentFailure.details_are_safe(details: dict[str, Any] | None) -> dict[str, Any]` *(validator, classmethod)* - Validator run before validation: pushes `details` through `sanitize_failure_details` (redact secrets, cap size).
  - `AgentFailure.from_sdk_error(error: AgentSdkError) -> AgentFailure` *(classmethod)* - Builds an `AgentFailure` from an `AgentSdkError`; the standard conversion used at every termination site. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent.run`, `base_agent/agent.py::_state_update_failure`
- **class `ToolExecutionResult`** *(pydantic model; bases: StrictModel)* - What a tool executor returns: status (succeeded/failed/blocked), optional output, error text and an optional structured `AgentFailure`. · *Instantiated by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `core/services.py::CoreToolDispatcher.execute`, `tools/registry.py::HarnessToolExecutor._execute_registered` (+3 more)
  - fields: `status`, `output`, `error`, `failure`
  - `ToolExecutionResult.output_is_bounded(output: Any) -> Any` *(validator, classmethod)* - Validator: the result output nests at most 64 levels.
- **class `ToolCallTurn`** *(pydantic model; bases: StrictModel)* - Model turn requesting exactly one tool call.
  - fields: `type`, `call`
  - `ToolCallTurn.call_declares_no_batch_dependencies(call: ToolCall) -> ToolCall` *(validator, classmethod)* - Validator: the single call must not list `depends_on_call_ids`, because dependencies only make sense inside a tool-batch turn; the error names the call id and the ids it listed.
- **class `ToolBatchTurn`** *(pydantic model; bases: StrictModel)* - Model turn requesting a batch of correlated tool calls with declared dependencies. · *Instantiated by:* `base_agent/agent.py::BaseAgent.run`
  - fields: `type`, `calls`
  - `ToolBatchTurn.batch_dependencies_are_declared_and_acyclic() -> ToolBatchTurn` *(validator)* - Validator: unique call ids, every dependency refers to a call in the batch, and the dependency graph has no cycle (the shared iterative `deterministic_cycles`, so a long chain cannot overflow the stack).
- **class `FinalTurn`** *(pydantic model; bases: StrictModel)* - Model turn proposing the final output (validated later against the output schema and the verification gate).
  - fields: `type`, `output`
  - `FinalTurn.output_is_bounded(output: Any) -> Any` *(validator, classmethod)* - Validator: the final output nests at most 64 levels; a deeper one is rejected naming `final output` and the limit.
- **class `BlockedTurn`** *(pydantic model; bases: StrictModel)* - Model turn declaring it cannot proceed, with a non-empty reason; ends the run as BLOCKED.
  - fields: `type`, `reason`
- **class `RuntimeOptions`** *(pydantic model; bases: StrictModel)* - Per-run limits and budgets (watchdog timeouts, context, episode, preview and state budgets) plus the scripted turns of a run that has no model endpoint; `mode` may only say `deterministic`. `TaskRunOptions` extends it with everything else a run takes from the caller.
  - fields: `mode`, `scripted_turns`, `run_deadline_seconds`, `model_turn_timeout_seconds`, `tool_call_timeout_seconds`, `verification_timeout_seconds`, `context_token_budget`, `episode_token_budget`, `tool_result_preview_chars`, `project_state_token_budget`
- **class `PromptSection`** *(pydantic model; bases: StrictModel)* - One labeled section (identity, instructions, task, skills, tools) of the initial prompt. · *Instantiated by:* `memory/context.py::assemble_initial_context`
  - fields: `kind`, `value`
- **class `AgentPrompt`** *(pydantic model; bases: StrictModel)* - The ordered list of prompt sections built once per run by `assemble_initial_context`. · *Instantiated by:* `memory/context.py::assemble_initial_context`
  - fields: `sections`
- **class `EpisodeSummary`** *(pydantic model; bases: StrictModel)* - One-line summary of an episode (id, kind, summary text, creation time, dependencies) exposed to the model while the episode is live. · *Instantiated by:* `memory/episodes.py::InMemoryEpisodeGraph._add`
  - fields: `id`, `kind`, `summary`, `created_at`, `dependency_ids`
- **class `CompactedEpisodeStub`** *(pydantic model; bases: StrictModel)* - Residue of a compacted episode that stays visible to the model: id, kind, one-line summary, tool name, status, iteration and the result handle id used to fetch the dropped result later. · *Instantiated by:* `memory/context_projection.py::_compacted_stubs`
  - fields: `episode_id`, `kind`, `summary`, `tool_name`, `status`, `iteration`, `handle_id`
- **class `ModelObservation`** *(pydantic model; bases: StrictModel)* - What the model is told happened: a projected tool result or an agent-error message (rejected answer, retried model failure), tagged with its iteration. · *Instantiated by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._record_executed_result`, `base_agent/agent.py::BaseAgent._record_unexecuted_result`, `base_agent/agent.py::BaseAgent.run`
  - fields: `kind`, `iteration`, `message`, `tool_call_id`, `tool_name`, `episode_id`, `result`
- **class `ToolResultHandle`** *(pydantic model; bases: StrictModel)* - Opaque pointer (id, content hash, byte count, truncated flag) to a full tool result stored outside the context. · *Instantiated by:* `memory/context_projection.py::FileToolResultJournal.record`, `memory/context_projection.py::InMemoryToolResultJournal._store`
  - fields: `handle_id`, `content_hash`, `byte_count`, `truncated`
- **class `ProjectedToolResult`** *(pydantic model; bases: StrictModel)* - Bounded form of a tool result safe for the model: status, handle, size-limited preview and truncated error. · *Instantiated by:* `memory/context_projection.py::ContextProjector.project_tool_result`
  - fields: `status`, `handle`, `preview`, `error`
- **class `ContextProjectionMetadata`** *(pydantic model; bases: StrictModel)* - Bookkeeping for one projection: estimated tokens, budgets, episodes compacted this turn, and counts of omitted observations and omitted compacted stubs. · *Instantiated by:* `memory/context_projection.py::ContextProjector.project`
  - fields: `estimated_tokens`, `context_token_budget`, `episode_token_budget`, `compacted_episode_ids`, `omitted_observation_count`, `omitted_compacted_count`
- **class `AgentLifecycleEvent`** *(pydantic model; bases: StrictModel)* - One typed lifecycle event (run-started, context-projected, tool-requested, terminated, ...) with iteration, timestamp and free-form details. · *Instantiated by:* `base_agent/agent.py::BaseAgent._run.emit`
  - fields: `type`, `task_id`, `iteration`, `at`, `details`
- **class `AgentEscalation`** *(pydantic model; bases: StrictModel)* - Escalation record (controller or human) with the reason, attached to non-completed results. · *Instantiated by:* `base_agent/agent.py::BaseAgent._terminate`
  - fields: `target`, `reason`
- **class `AgentResult`** *(pydantic model; bases: StrictModel)* - Everything a run returns: status, iteration count, output, reason, structured failure, escalation, the prompt used, final project state view, episode summaries, projection history, lifecycle events and the profiler report. · *Instantiated by:* `base_agent/agent.py::BaseAgent._terminate`
  - fields: `status`, `task_id`, `iterations`, `output`, `reason`, `failure`, `escalation`, `context`, `project_state`, `episodes`, `projection_history`, `events`, `profile`
- `validate_task_input(definition: AgentDefinition, task: ScopedAgentTask) -> None` - Validates a task's input payload against the definition's input schema; raises `SCHEMA_VALIDATION_FAILED`. · *Called by:* `base_agent/agent.py::BaseAgent.run`
- `validate_tool_arguments(tool: ToolDefinition, arguments: dict[str, Any]) -> None` - Validates a tool call's arguments against that tool's input schema before execution. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_call`
- `validate_candidate_output(definition: AgentDefinition, output: Any) -> None` - Validates a proposed final output against the definition's output schema; the failure message names the failing JSON path and is fed back to the model. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`
- `_validate_json_schema(schema: dict[str, Any], label: str) -> None` - Checks that a schema is valid Draft 2020-12 (cached when JSON round-trippable), converting `SchemaError` into a `ValueError` with the label. · *Called by:* `foundations/contracts.py::AgentDefinition.validate_contract_schema`, `foundations/contracts.py::ToolDefinition.validate_input_schema`
- `_check_schema_cached(canonical_schema: str) -> None` - LRU-cached (512) `check_schema` keyed by the canonical JSON text of the schema. · *Called by:* `foundations/contracts.py::_validate_json_schema`
- `_refuse_retrieval(uri: str) -> Never` - Retriever of the schema registry: raises `NoSuchResource` for every URI, so no document is ever fetched from a URL or a file. · *Called by:* `foundations/contracts.py::<module>`
- `_new_validator(schema: dict[str, Any]) -> Draft202012Validator` - Builds a Draft 2020-12 validator over a registry that knows only the JSON Schema metaschemas and the schema itself. · *Called by:* `foundations/contracts.py::_validate_instance`, `foundations/contracts.py::_validator_for`
- `_validator_for(canonical_schema: str) -> Draft202012Validator` - LRU-cached (512) validator keyed by canonical schema text, avoiding rebuilding validators on every turn; built through `_new_validator`, so it can only resolve references inside the schema. · *Called by:* `foundations/contracts.py::_validate_instance`
- `_validate_instance(schema: dict[str, Any], instance: Any, label: str) -> None` - Runs the validator and converts the first jsonschema error into an `AgentSdkError` whose message includes the JSON path and reason, with `json_path`, `schema_rule`, sanitized `failed_value` and the full error text in `details`; a `$ref` that points outside the schema raises `SCHEMA_REFERENCE_UNRESOLVABLE` naming the reference. · *Called by:* `foundations/contracts.py::validate_candidate_output`, `foundations/contracts.py::validate_task_input`, `foundations/contracts.py::validate_tool_arguments`
- `_canonical_json_schema(schema: dict[str, Any]) -> str | None` - Returns a cache key (sorted-key compact JSON) only when the schema survives JSON round-tripping; otherwise None so non-JSON schemas (for example Decimal constants) bypass the cache. · *Called by:* `foundations/contracts.py::_validate_instance`, `foundations/contracts.py::_validate_json_schema`

**Algorithms & invariants.** `AgentTurn` is a pydantic discriminated union on `type`, so a malformed turn fails with the offending variant and field path. Schema validation errors are deliberately field-specific because they are replayed to the model as correction hints. Tool-call arguments, tool-result outputs and final outputs are bounded to 64 nesting levels at validation time (`foundations/json_limits.py`). A schema's `$ref` resolves only inside the schema (a `#/$defs/...` pointer or its own `$id`) or to a JSON Schema metaschema; any other reference, a URL or a file, is refused with `SCHEMA_REFERENCE_UNRESOLVABLE` and nothing is fetched or read.

*Module-level names:* `AgentTurn`, `_LOCAL_REFERENCES_ONLY`

---

### `foundations/dependency_graph.py` - deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges

*104 lines · depends on: nothing in the package · used by: `foundations/contracts.py`, `foundations/optimization/models.py`, `state/graph.py`, `state/planning.py` · not re-exported at the package root*

**Role in the workflow.** Used by plan/traceability validation: cycles make a plan invalid, and the reverse reachable set from a missing prerequisite is the list of dependents it breaks.

**Contents**

- `deterministic_cycles(nodes: Iterable[str], edges: Iterable[DependencyPair]) -> list[list[str]]` - Iterative depth-first search with an explicit stack (so a chain of any length is safe) over known nodes only: unknown endpoints are ignored so they can be reported separately as missing references. Nodes and neighbours are visited in sorted order and each detected cycle is returned as a closed path `[a, b, ..., a]`. Its output was checked identical to the earlier recursive version on 6,000 random graphs. · *Called by:* `foundations/contracts.py::ToolBatchTurn.batch_dependencies_are_declared_and_acyclic`, `optimization/models.py::_assert_acyclic`, `state/graph.py::StateGraph._assert_acyclic`, `state/planning.py::PlanValidator._validate_cycles`
- `reverse_reachable_nodes(nodes: Iterable[str], edges: Iterable[DependencyPair], root_id: str) -> list[str]` - Breadth-first search from `root_id` along reversed edges: returns the sorted known dependents transitively affected by `root_id`. The root may be absent from `nodes`. · *Called by:* `foundations/dependency_graph.py::reverse_reachable_count`
- `reverse_reachable_count(nodes: Iterable[str], edges: Iterable[DependencyPair], root_id: str) -> int` - Length of `reverse_reachable_nodes`; the blast-radius size. · *No in-package callers (public API, entry point, or protocol hook).*

*Module-level names:* `DependencyPair`

---

### `foundations/detached.py` - run a blocking call on a daemon thread that the awaiting task may abandon

*42 lines · depends on: nothing in the package · used by: `agent/openai_compatible/chat.py`, `tools/core/services.py` · not re-exported at the package root*

**Role in the workflow.** `asyncio.to_thread` uses the event loop's default executor, which `asyncio.run` joins when the loop shuts down, so a cancelled await on a slow request still holds the process until the request ends. A call run here is not joined: the awaiting task can be cancelled and the run can finish at once. The model call and the network tools use it.

**Contents**

- `run_detached(function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T` *(async)* - Runs `function(*args, **kwargs)` on a new daemon thread under a copy of the caller's context and awaits its result or exception. If the awaiting task is cancelled the thread keeps running; its outcome is dropped, and the hand-back to a loop that has closed meanwhile is skipped without an error. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `core/services.py::CoreToolDispatcher._render_pdf_page`, `core/services.py::CoreToolDispatcher._web_fetch`, `core/services.py::DuckDuckGoHtmlClient.search`
  - `run_detached.deliver(complete: Callable[[Any], None], value: Any) -> None` - Completes the future from the loop thread unless it is already done (cancelled). · *Called by:* `foundations/detached.py::run_detached.work`
  - `run_detached.work() -> None` - The thread body: calls the function, captures its result or exception and schedules `deliver` on the loop; a closed loop is ignored. · *Called by:* `base_agent/agent.py::_interruptible`, `foundations/detached.py::run_detached`

**Algorithms & invariants.** The thread is a daemon, so an abandoned call never keeps the interpreter alive, and it ends when the call returns or its own timeout fires.

---

### `foundations/errors.py` - typed SDK errors and secret/reasoning redaction for durable records

*284 lines · depends on: `foundations/hashing.py`, `foundations/text.py` · used by: `agent/base_agent/agent.py`, `agent/model.py`, `agent/openai_compatible/chat.py`, `agent/openai_compatible/embeddings.py`, `agent/openai_compatible/transport.py`, `agent/openai_compatible/vision.py`, `agent/orchestrator/state_store.py`, `agent/retention.py` (+16 more) · not re-exported at the package root*

**Role in the workflow.** `AgentSdkError` (code, message, details) is the one structured exception the runtime converts into `AgentFailure`s; `TransientProviderError` marks retryable provider failures so `BaseAgent` and `FailoverAgentModel` retry instead of failing. The redaction functions run at every durable boundary (failure details, audit log, telemetry) so credentials and hidden model reasoning never reach disk.

**Contents**

- **class `AgentSdkError`** *(dataclass, exception; bases: Exception)* - Dataclass exception with a stable `code`, a human `message` (also its `str`) and optional `details`. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`, `base_agent/agent.py::BaseAgent._normalize_model_response`, `agent/model.py::FailoverAgentModel._call_with_failover`, `agent/model.py::ScriptedModel.next_turn` (+34 more)
  - fields: `code`, `message`, `details`
  - `AgentSdkError.__str__() -> str` - Returns the message so logs and tracebacks show the human text.
- **class `TransientProviderError`** *(dataclass, exception; bases: AgentSdkError)* - An `AgentSdkError` for failures likely to succeed on retry (429, 5xx, connection drop), carrying an optional server `retry_after_seconds`. · *Instantiated by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`, `openai_compatible/transport.py::_http_status_error`
  - fields: `retry_after_seconds`
- `sanitize_failure_details(details: dict[str, Any] | None) -> dict[str, Any]` - Redacts and bounds a details dict: secrets and hidden-reasoning keys are masked (inside lists, tuples, sets and bytes too), and if the JSON exceeds 2,048 chars it is replaced by a truncation record with a content hash and preview. · *Called by:* `foundations/contracts.py::AgentFailure.details_are_safe`, `foundations/contracts.py::_validate_instance`
- `redact_secrets(value: Any) -> Any` - Shared redactor for audit log and telemetry: masks values under credential-shaped keys and credential-shaped spans inside strings, leaving surrounding text intact, walks lists, tuples, sets and bytes, and replaces lone surrogates so the result is always valid UTF-8. · *Called by:* `openai_compatible/transport.py::_scrub`, `mcp/client.py::_failure_detail`, `mcp/client.py::_liveness_detail`, `observability/audit_log.py::_bound_and_redact` (+2 more)
- `_redact_value(value: Any, *, hide_reasoning_keys: bool) -> Any` - Recursive helper behind `redact_secrets` and `sanitize_failure_details`: masks dict values under secret keys (and, with `hide_reasoning_keys`, reasoning keys), recurses into lists, tuples, sets and frozensets keeping their type, decodes bytes (invalid sequences replaced) and scans strings for credential patterns. · *Called by:* `foundations/errors.py::redact_secrets`, `foundations/errors.py::sanitize_failure_details`
- `contains_secret_text(text: str) -> bool` - True when the free-text redactor would change the string (a credential-shaped span or `NAME=value` assignment), after scrubbing lone surrogates; used to refuse such text instead of masking it.
- `_redact_content(text: str) -> str` - Free-text pass: scrubs lone surrogates, skips the work entirely unless a cheap substring hint is present, then masks PEM private-key blocks, applies the credential regexes, masks the password of a `scheme://user:password@host` URL and runs the assignment scanner. · *Called by:* `foundations/errors.py::_redact_value`, `foundations/errors.py::contains_secret_text`
- `_redact_pem_blocks(text: str) -> str` - Linear scan that replaces each `-----BEGIN ... PRIVATE KEY-----` to `-----END ... PRIVATE KEY-----` block (found with a binary search over the end markers) with `[REDACTED]`; an unterminated block is left alone.
- `_redact_assignments(text: str) -> str` - Linear-time detector for `NAME_SECRET=value`, `"api_key": "value"` and quoted values that contain spaces: finds a keyword, widens to the whole identifier, and masks it plus the assignment tail; for an `Authorization` header whose value starts with a scheme word the credential after the scheme is masked too. · *Called by:* `foundations/errors.py::_redact_content`
- `redact_hidden_reasoning(value: Any) -> Any` - Returns a copy of a payload in which every reserved reasoning key (`chain_of_thought`, `hidden_reasoning`, `reasoning_trace`, `scratchpad`) is renamed `<key>_redacted` with the value `[REDACTED]`, recursing through dicts, lists, tuples and sets (a set becomes a list ordered by `repr`); used where a payload must be stored rather than rejected.
- `assert_no_hidden_reasoning(value: Any, path: str='$') -> None` - Raises `ValueError` naming the key and its JSON path (`$.a[0].b`) if any dict key is reserved for private model reasoning, looking inside lists, tuples and sets; durable telemetry and audit records refuse such payloads outright. · *Called by:* `observability/audit_log.py::AuditLogEntry.safe_payload`, `observability/telemetry_models.py::TelemetryEvent.reject_hidden_reasoning`

**Algorithms & invariants.** Detection is best-effort: a key-name regex (authorization, api key, password, passwd, passphrase, secret, access and signing keys, token excluding budget/cost/plural counters, cookie, credential, private key) plus patterns for PEM private keys, `sk-` and `sk_live_`/`sk_test_` keys, AWS `AKIA`/`ASIA` ids, GitHub (`gh*_`, `github_pat_`), GitLab (`glpat-`), Hugging Face (`hf_`), Google (`AIza`), npm, Fireworks (`fw_`) and Slack tokens, JSON web tokens, the password of a `scheme://user:password@host` URL and Bearer tokens. An `Authorization` value that begins with a scheme word (`Basic`, `Digest`, `Token`, ...) is masked through its credential. Token patterns start only at a run boundary and use possessive quantifiers, and every pattern is anchored on a literal prefix, so none can backtrack super-linearly.

*Module-level names:* `_SECRET_KEY`, `_HIDDEN_REASONING_KEYS`, `_MAX_FAILURE_DETAIL_CHARS`, `_SECRET_CONTENT_PATTERNS`, `_ASSIGNMENT_TAIL`, `_IDENTIFIER_RUN`, `_CONTENT_HINT_SUBSTRINGS`, `_RUN_START`, `_URL_CREDENTIAL`, `_AUTHORIZATION_SCHEMES`, `_AUTHORIZATION_CREDENTIAL`

---

### `foundations/hashing.py` - canonical JSON encodings, SHA-256 digests and the token estimate

*56 lines · depends on: nothing in the package · used by: `agent/base_agent/agent.py`, `agent/graph_agent_executor.py`, `agent/retention.py`, `foundations/errors.py`, `foundations/optimization/solvers.py`, `integrations/_utils.py`, `integrations/jev/decision.py`, `integrations/jev/models.py` (+18 more) · not re-exported at the package root*

**Role in the workflow.** Every durable record that carries an integrity hash builds it from one of these encodings: the audit transcript, the telemetry ledger, the tool-result journal, project-state events, the run record and its history, provenance records, episode checkpoints, evidence-graph and retrieval digests and integration receipts. They are defined once so a record written by an earlier version keeps verifying. `estimate_tokens` is the one place a value becomes a token count.

**Contents**

- `canonical_json(value: Any) -> str` - Sorted keys, compact separators, ASCII escapes, and an object JSON cannot encode written as its `str()`. The encoding behind the integrity hashes of the audit transcript, the telemetry ledger, the tool-result journal, provenance records and the integration digests. · *Called by:* `base_agent/agent.py::BaseAgent._bounded_json_text`, `base_agent/agent.py::BaseAgent._provider_tool_result`, `foundations/errors.py::sanitize_failure_details`, `foundations/hashing.py::canonical_hash` (+8 more)
- `strict_canonical_json(value: Any) -> str` - The same encoding without the `str()` fallback: a value JSON cannot hold raises `TypeError`. Used where an unexpected type must fail loudly: the run-record hash, the run history entries, episode checkpoints, evidence-graph and PCKP problem hashes and binding hashes. · *Called by:* `agent/graph_agent_executor.py::GraphAgentBinding.binding_hash`, `optimization/solvers.py::_problem_hash`, `memory/episode_store.py::InMemoryEpisodeStore.checkpoint`, `specifications/evidence_graph.py::EvidenceGraph.content_hash` (+2 more)
- `model_canonical_json(value: Any) -> str` - The same encoding except that an object with `model_dump` is written as its JSON-mode dump instead of its `str()`. Used by the project-state hashes and files. · *Called by:* `state/project_state_engine.py::ProjectStateProjector.project`, `state/project_state_engine.py::_bounded_value`, `state/project_state_models.py::StateAction.summary_is_bounded`, `state/project_state_models.py::_event_hash` (+2 more)
- `sha256_hex(data: str | bytes) -> str` - SHA-256 hex digest of text (encoded as UTF-8) or bytes. · *Called by:* `agent/graph_agent_executor.py::GraphAgentBinding.binding_hash`, `foundations/errors.py::sanitize_failure_details`, `foundations/hashing.py::canonical_hash`, `optimization/solvers.py::_problem_hash` (+15 more)
- `canonical_hash(value: Any) -> str` - `sha256_hex(canonical_json(value))`: the SDK's general-purpose content hash for state records. · *Called by:* `base_agent/agent.py::project_state_hash_from_result`, `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `integrations/_utils.py::canonical_digest`, `jev/decision.py::_normalize_jev_response` (+16 more)
- `estimate_tokens(value: Any, serialize: Callable[[Any], str]=canonical_json) -> int` - Heuristic token count: the length of the value's encoding divided by 4, at least 1. `serialize` picks the encoding (default `canonical_json`).
- `_model_or_text(item: Any) -> Any` - JSON fallback for `model_canonical_json`: a model's JSON-mode dump, otherwise `str()`. · *Called by:* `foundations/hashing.py::model_canonical_json`

**Algorithms & invariants.** The three encodings are not interchangeable. For a value that is already JSON they agree byte for byte; they differ for anything JSON cannot hold: `canonical_json` writes an unknown object as its `str()` (a pydantic model becomes `a=1 b='x'`), `model_canonical_json` writes a model as its fields, and `strict_canonical_json` refuses with `TypeError`. A hash written with one cannot be verified with another when such a value is present, so every call site keeps the encoding it always used and `tests/foundations/test_hashing.py` freezes the output of all three. The token estimate counts compact JSON; the episode store used to count a form with spaces after the separators, so its estimates are now about a tenth lower.

---

### `foundations/identifiers.py` - identifier validation, injective file names and collision-free sequential ids

*90 lines · depends on: `foundations/atomic_io.py` · used by: `agent/orchestrator/models.py`, `agent/orchestrator/state_store.py`, `agent/task_runner.py`, `foundations/contracts.py`, `foundations/optimization/models.py`, `foundations/record_locks.py`, `integrations/jev/exploration.py`, `mcp/client.py` (+17 more) · not re-exported at the package root*

**Role in the workflow.** Every store that turns a caller-supplied id into a path validates it with `validate_identifier` or maps it with `file_safe_name`, so `../../x` can never leave the run root; the stores that number their own ids (`run-1`, `controller-2`, `orchestration-3`) reserve them through `reserve_sequential_identifier`.

**Contents**

- `is_valid_identifier(value: object) -> bool` - True for a string of 1 to 128 characters of letters, digits, `.`, `_` and `-` that starts with a letter or digit and does not end with a dot. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.exists`, `foundations/identifiers.py::validate_identifier`, `state/orchestration.py::ControllerStateStore.exists`, `state/run_state_store.py::RunStateStore.exists`
- `validate_identifier(value: object, kind: str) -> str` - Returns the value or raises `ValueError` naming the kind, the offending value and the allowed shape; non-strings are rejected the same way. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore._record_path`, `orchestrator/state_store.py::OrchestrationStateStore.locked`, `state/elastic.py::ElasticSpawnRequest.request_id_is_an_identifier`, `state/orchestration.py::ControllerStateStore._events_path` (+6 more)
- `repeated_values(values: Iterable[Hashable]) -> list[Hashable]` - The values that occur more than once, each listed once in the order it first repeats. · *Called by:* `foundations/identifiers.py::require_unique`
- `require_unique(values: Iterable[Hashable], label: str) -> None` - Raises `ValueError` `<label> must be unique; repeated: "a", "b".` naming up to ten repeats (then `and N more`); the one place every plan, graph, tool, policy and specification validator reports a repeated id. · *Called by:* `orchestrator/models.py::AgentExecutionProfile.profile_authority_is_consistent`, `orchestrator/models.py::OrchestrationPolicy.configuration_ids_are_consistent`, `orchestrator/models.py::OrchestrationRequest.selected_skill_ids_are_unique`, `agent/task_runner.py::AgentTaskRunner._prepare_tools` (+27 more)
- `file_safe_name(value: str) -> str` - Returns the value unchanged when it is already a safe file name (at most 128 characters of letters, digits, `.`, `_`, `-`; not `.`, `..` or ending with a dot); otherwise a readable sanitized stem (at most 80 characters) plus `~` and the first 12 hex characters of the SHA-256 of the original, so two different inputs never share a file. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore._policy_path`, `agent/task_runner.py::AgentTaskRunner._workspace_path`, `agent/task_runner.py::AgentTaskRunner.workspace_for`, `foundations/record_locks.py::RecordLocks.hold` (+7 more)
- `reserve_sequential_identifier(claims: Path, prefix: str, is_taken: Callable[[str], bool], *, start: int=1) -> tuple[str, int]` - Walks `<prefix>-<n>` from `start`, skips ids that `is_taken` reports, and returns the first one whose claim file under `claims` it creates exclusively (`claim_exclusive`) together with the next number to try; two processes can never receive the same id. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.reserve_orchestration_id`, `state/orchestration.py::ControllerStateStore.reserve_controller_id`, `state/run_state_store.py::RunStateStore.reserve_run_id`

**Algorithms & invariants.** A safe file name never contains `~`, so a mapped name cannot equal a passthrough name; two different unsafe inputs share a file only if their 12-hex digests collide. Reservation relies on exclusive file creation rather than on checking whether a record exists, which is what makes it safe across processes.

---

### `foundations/json_limits.py` - depth bound for untrusted JSON-like payloads

*28 lines · depends on: nothing in the package · used by: `foundations/contracts.py`, `specifications/preprocessing.py`, `state/graph_models.py`, `state/project_state_models.py`, `state/shared_state.py` · not re-exported at the package root*

**Role in the workflow.** Validators on tool-call arguments, tool results, final outputs, graph node outputs and metadata, shared-state payloads, project-state transitions and parsed specification documents call `assert_json_depth` so a hostile or runaway payload is refused with a message naming the field, instead of failing later when pydantic serializes it.

**Contents**

- `assert_json_depth(value: Any, label: str, limit: int=MAX_JSON_DEPTH) -> Any` - Walks dicts, lists and tuples with an explicit stack (no recursion limit) and returns the value, or raises `ValueError` `<label> nests more than <limit> levels deep; the limit is <limit>.` when any branch exceeds `limit` (default `MAX_JSON_DEPTH`, 64). · *Called by:* `foundations/contracts.py::FinalTurn.output_is_bounded`, `foundations/contracts.py::ToolCall.arguments_are_bounded`, `foundations/contracts.py::ToolExecutionResult.output_is_bounded`, `specifications/preprocessing.py::SpecificationPreprocessor._parse` (+8 more)

**Algorithms & invariants.** The limit exists because pydantic's JSON-mode dump fails for payloads nested about 100 levels deep; 64 keeps every accepted payload serializable with a wide margin.

---

### `foundations/logging.py` - opt-in stdlib logging namespace for the few paths outside structured telemetry

*25 lines · depends on: nothing in the package · used by: `agent/model.py`, `mcp/client.py`, `observability/audit_log.py`, `tools/tasks.py` · not re-exported at the package root*

**Role in the workflow.** The primary record is the hash-chained telemetry/audit store; logging only covers cases such as a host callback raising. A NullHandler keeps the SDK silent unless the host attaches its own handler to the `nailong_agent_sdk` logger.

**Contents**

- `get_logger(name: str) -> logging.Logger` - Returns a logger named `nailong_agent_sdk.<name>`. · *Called by:* `agent/model.py::<module>`, `mcp/client.py::<module>`, `observability/audit_log.py::<module>`, `tools/tasks.py::<module>`

*Module-level names:* `_ROOT_LOGGER_NAME`

---

### `foundations/optimization/__init__.py` - re-exports the PCKP models and solvers

*17 lines · depends on: `foundations/optimization/models.py`, `foundations/optimization/solvers.py` · used by: `foundations/benchmarks.py`, `memory/episode_store.py`, `specifications/evidence_graph.py` · not re-exported at the package root*

**Role in the workflow.** Convenience import surface for `ExactPckpSolver`, `GreedyPckpBaseline` and the problem/solution contracts.

---

### `foundations/optimization/models.py` - PCKP problem, item and solution contracts

*90 lines · depends on: `foundations/contracts.py`, `foundations/dependency_graph.py`, `foundations/identifiers.py` · used by: `foundations/optimization/__init__.py`, `foundations/optimization/solvers.py` · re-exported at the package root: 4 name(s)*

**Role in the workflow.** The compactor and the evidence packer translate their domain (episodes, source spans) into a `PckpProblem`, call a solver, and read back a `PckpSolution` certificate that is recorded in the decision dossier.

**Contents**

- **class `PckpStatus`** *(enum; bases: StrEnum)* - Whether the answer is proven optimal, only the best found within a limit, or infeasible because the mandatory closure alone exceeds the budget.
  - members: `OPTIMAL`, `BEST_EFFORT`, `INFEASIBLE_MANDATORY`
- **class `PckpItem`** *(pydantic model; bases: StrictModel)* - One item: id, integer token cost, integer utility, direct prerequisites and a `mandatory` flag. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `specifications/evidence_graph.py::StructuralContextSelector.select`
  - fields: `item_id`, `token_cost`, `utility`, `prerequisites`, `mandatory`
  - `PckpItem.prerequisites_are_unique_and_external() -> PckpItem` *(validator)* - Validator: prerequisites are unique and an item cannot require itself.
- **class `PckpProblem`** *(pydantic model; bases: StrictModel)* - A budget plus the item set; selecting an item forces selecting all its prerequisites. · *Instantiated by:* `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `specifications/evidence_graph.py::StructuralContextSelector.select`
  - fields: `token_budget`, `items`
  - `PckpProblem.validate_problem_graph() -> PckpProblem` *(validator)* - Validator: unique ids, every prerequisite exists, and the prerequisite graph is acyclic.
- **class `PckpSolution`** *(pydantic model; bases: StrictModel)* - Solver certificate: status, selected and mandatory ids, cost, utility, upper bound, optimality gap, branch-node count, solver name, problem hash and diagnostics. · *Instantiated by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`, `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve`, `optimization/solvers.py::_solve_rooted_forest`
  - fields: `status`, `selected_item_ids`, `mandatory_item_ids`, `token_cost`, `utility`, `upper_bound`, `optimality_gap`, `branch_nodes`, `solver`, `problem_hash`, `diagnostics`
- `_assert_acyclic(dependencies: dict[str, set[str]]) -> None` - Cycle check over the prerequisite map using the shared iterative `deterministic_cycles`; raises `ValueError` naming the first item of the first cycle (`PCKP dependencies contain a cycle at "<id>"`). · *Called within this file by:* `optimization/models.py::PckpProblem.validate_problem_graph`

---

### `foundations/optimization/solvers.py` - exact (tree DP / branch-and-bound) and greedy PCKP solvers

*503 lines · depends on: `foundations/hashing.py`, `foundations/optimization/models.py` · used by: `foundations/optimization/__init__.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `ExactPckpSolver` is what `InMemoryEpisodeStore` uses by default to choose which episodes survive compaction (retain the dependency-closed set with maximum utility under the token budget); `GreedyPckpBaseline` exists for comparison and benchmarks.

**Contents**

- **class `ExactPckpSolver`** *(class)* - Exact solver with an optional node limit (`max_branch_nodes`), a switch for the tree DP, and a tree-DP budget ceiling (default 50,000 tokens). · *Instantiated by:* `foundations/benchmarks.py::run_pckp_benchmark`, `memory/episode_store.py::InMemoryEpisodeStore._compact_exact_pckp`, `specifications/evidence_graph.py::StructuralContextSelector.select`
  - `ExactPckpSolver.__init__(*, max_branch_nodes: int | None=None, enable_tree_dynamic_program: bool=True, max_tree_token_budget: int=50000) -> None` - Validates the limits (node limit at least 1 when set, non-negative tree budget) and stores them.
  - `ExactPckpSolver.solve(problem: PckpProblem) -> PckpSolution` - Builds the item map, computes the mandatory closure, returns an INFEASIBLE_MANDATORY certificate if it exceeds the budget, otherwise uses the Pareto-frontier tree DP when every item has at most one prerequisite and `_frontier_bound` is within `max_tree_token_budget`, else branch-and-bound.
  - `ExactPckpSolver._solve_branch_and_bound(problem: PckpProblem, mandatory: set[str], dependents: dict[str, set[str]], problem_hash: str) -> PckpSolution` - Depth-first search over items in sorted-id order (exclusion branch first). Each node propagates constraints, prunes on cost over budget, on a fractional (LP-relaxation) upper bound below the incumbent, or on an equal bound with a higher cost, and equal-utility leaves resolve to the cheaper selection, then the smaller `tie_key`. If the node limit stops the search the result is BEST_EFFORT with the gap measured against the root bound. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
- **class `GreedyPckpBaseline`** *(class)* - Deterministic density-first baseline for the same objective; makes no optimality claim. · *Instantiated by:* `foundations/benchmarks.py::run_pckp_benchmark`
  - `GreedyPckpBaseline.solve(problem: PckpProblem) -> PckpSolution` - Repeatedly adds the best utility-per-cost dependency closure that still fits the budget (ties broken by utility, then cost, then id) until nothing fits.
- `tie_key(selected: Iterable[str]) -> tuple[tuple[int, str], ...]` - Total order for equal-utility, equal-cost selections: the sorted ids, each tagged 0, plus an end sentinel tagged 1, so the selection that includes the earliest differing id wins. Common items cancel, so the order is the same when applied to whole selections or to the parts a dynamic program merges.
- **class `_Selection`** *(class)* - Persistent rope of item ids: leaves hold ids and joins are O(1), so merging two partial selections never copies them; `ids` flattens iteratively and `key` caches `tie_key`.
  - `_Selection.__init__(ids: tuple[str, ...]=(), left: _Selection | None=None, right: _Selection | None=None) -> None` - Stores the leaf ids and optional left and right children.
  - `_Selection.join(left: _Selection, right: _Selection) -> _Selection` *(classmethod)* - Joins two ropes, returning the other one unchanged when either is empty. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent._normalize_model_response`, `base_agent/agent.py::BaseAgent._pask_relevance_query`, `agent/elastic_context.py::_child_entry` (+43 more)
  - `_Selection.ids() -> tuple[str, ...]` *(property)* - Flattens the rope to a tuple with an explicit stack (no recursion limit). · *Called by:* `optimization/solvers.py::_Selection.__init__`, `optimization/solvers.py::_Selection.key`, `optimization/solvers.py::_solve_rooted_forest`, `state/planning.py::Plan.task_ids_are_unique`
  - `_Selection.key() -> tuple[tuple[int, str], ...]` *(property)* - Cached `tie_key` of the flattened ids. · *Called by:* `openai_compatible/chat.py::_safe_parameters`, `agent/retention.py::_TombstoneAppender.record`, `agent/task_runner.py::AgentTaskRunner.run`, `developer_tools/inspect.py::verify_project_evidence` (+35 more)
- `_solve_rooted_forest(problem: PckpProblem, mandatory: set[str], problem_hash: str) -> PckpSolution` - Exact Pareto-frontier DP for a forest where each item has at most one prerequisite: each subtree keeps only the states no cheaper state matches in utility (so the table is bounded by the number of distinct utilities, not by the budget), child tables merge into their parent and root tables merge into the answer. Ties go to the lower cost, then `tie_key`. Always OPTIMAL. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
  - `_solve_rooted_forest.subtree_states(root_id: str) -> _States` - Builds the state table of one tree bottom-up over its preorder (no recursion), merging each child's table into its parent's; a mandatory child can never be left out.
- `_offer(states: _States, cost: int, utility: int, selection: _Selection) -> None` - Records a (cost, utility, selection) candidate if its cost slot is empty or it has higher utility or, at equal utility, a smaller `tie_key`.
- `_merge_states(left: _States, right: _States, budget: int, *, keep_left: bool) -> _States` - Combines two state tables under the budget: optionally keeps each left state alone (when the right subtree is optional), adds every affordable left+right pair and returns the dominance-pruned result.
- `_prune_dominated(states: _States) -> _States` - Keeps only states whose utility strictly exceeds that of every cheaper state. · *Called by:* `optimization/solvers.py::_solve_rooted_forest`
- `_frontier_bound(problem: PckpProblem) -> int` - `min(budget, total cost, total utility + 1)`: an upper bound on the number of states a frontier table can hold, used to decide whether the DP applies.
- `_dependents(items: Iterable[PckpItem]) -> dict[str, set[str]]` - Inverts the prerequisite relation into item -> set of dependents. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
- `_closure(seeds: set[str], items: dict[str, PckpItem]) -> set[str]` - All prerequisites reachable from the seeds, including the seeds (iterative, deterministic order). · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve`
- `_propagate(selected: set[str], excluded: set[str], items: dict[str, PckpItem], dependents: dict[str, set[str]]) -> tuple[set[str], set[str]] | None` - Fixpoint constraint propagation: selecting an item selects its prerequisites; excluding an item excludes its dependents; returns None on contradiction. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`
- `_fractional_upper_bound(selected: set[str], excluded: set[str], items: dict[str, PckpItem], density_order: list[PckpItem], budget: int) -> Fraction` - Optimistic utility bound: selected utility plus a greedy fractional fill of the remaining budget by utility density (free items always taken); -1 when the selection already exceeds the budget. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`
- `_cost(selected: Iterable[str], items: dict[str, PckpItem]) -> int` - Sum of token costs of a selection. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`, `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve` (+2 more)
- `_utility(selected: Iterable[str], items: dict[str, PckpItem]) -> int` - Sum of utilities of a selection. · *Called within this file by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`, `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve` (+1 more)
- `_is_rooted_forest(items: Iterable[PckpItem]) -> bool` - True when every item has at most one prerequisite (the tree-DP precondition). · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
- `_problem_hash(problem: PckpProblem) -> str` - SHA-256 of the canonical problem JSON; ties a certificate to its exact input. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve`
- `_fraction_text(value: Fraction) -> str` - Renders a `Fraction` as an integer or `n/d` string for exact bounds in certificates. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`
- `_descending_id_key(value: str) -> tuple[int, ...]` - Negated code points so `max` picks the lexicographically first id. · *Called by:* `optimization/solvers.py::GreedyPckpBaseline.solve`

**Algorithms & invariants.** All arithmetic is exact (integers and `Fraction`), ordering is fixed (sorted ids), and no randomness or model call is involved, so a certificate is reproducible from its problem hash. `OPTIMAL` is only ever reported after a full proof; a bounded search is BEST_EFFORT. The tree DP and the branch-and-bound search break ties identically (higher utility, then lower cost, then `tie_key`), which is why both return the same selection.
---

### `foundations/paths.py` - comparison of Windows extended-length path spellings as one location

*40 lines · depends on: nothing in the package · used by: `agent/openai_compatible/vision.py`, `agent/task_files.py`, `agent/task_runner.py`, `specifications/preprocessing.py`, `tools/artifacts.py`, `tools/core/services.py`, `tools/policy.py` · not re-exported at the package root*

**Role in the workflow.** Every check that a caller-supplied relative path stays inside a root resolves the path and compares it with the root: artifact writes, the core file tools, capability path scopes, specification and image loading, and a task's workspace. They call `relative_to_base` instead of `Path.relative_to`. `Path.resolve()` on Windows can return the same location as `\\?\C:\...`: CPython drops that marker only after resolving the shortened path again, and when that second lookup fails (it did, once, while other processes were replacing the file) the marker stays, so a file inside the root looked like it escaped it.

**Contents**

- `strip_extended_prefix(path: P) -> P` - Returns a Windows path without its extended-length marker: `\\?\C:\x` becomes `C:\x` and `\\?\UNC\server\share` becomes `\\server\share`. Any other path (including `\\?\Volume{...}` and `\\?\GLOBALROOT`, which name a different location without the marker) and every non-Windows path is returned unchanged. · *Called by:* `foundations/paths.py::relative_to_base`
- `relative_to_base(path: P, base: PurePath) -> P` - `path.relative_to(base)` after removing the extended-length marker from both sides, so the answer depends on the location and not on how Windows spelled it; raises the same `ValueError` as `PurePath.relative_to` when `path` is outside `base`. · *Called by:* `openai_compatible/vision.py::SourceVerifiedImageLoader.__call__`, `agent/task_runner.py::AgentTaskRunner._workspace`, `specifications/preprocessing.py::SpecificationPreprocessor._resolve`, `tools/artifacts.py::ArtifactStore._resolve_relative` (+4 more)

**Algorithms & invariants.** Only the comparison is normalised. Callers keep using the path `resolve()` returned for reading and writing, so a path too long for the legacy limit, which needs the marker, still opens. Only the drive and UNC forms are unwrapped, because those are the forms for which the marker adds nothing; a `PurePosixPath` is never rewritten, so a POSIX file name that happens to start with a backslash-question-backslash sequence is left alone. Matching is case-insensitive exactly where `PureWindowsPath` is.

---

### `foundations/record_locks.py` - per-record cross-process locks, re-entrant for the holding thread

*62 lines · depends on: `foundations/atomic_io.py`, `foundations/identifiers.py` · used by: `agent/orchestrator/state_store.py`, `state/orchestration.py`, `state/project_state_store.py`, `state/run_state_store.py` · not re-exported at the package root*

**Role in the workflow.** A store that reads a record, changes it and writes it back holds the record's lock for that whole sequence, so two processes (or threads) changing the same run, controller, orchestration or project state take turns instead of overwriting one another. Each store keeps its locks under `<store>/.locks/`, one file per record, so unrelated records never wait for each other.

**Contents**

- **class `RecordLocks`** *(class)* - The lock set of one store directory: one lock file per record id. · *Instantiated by:* `orchestrator/state_store.py::OrchestrationStateStore.__init__`, `state/orchestration.py::ControllerStateStore.__init__`, `state/project_state_store.py::FileProjectStateStore.__init__`, `state/run_state_store.py::RunStateStore.__init__`
  - `RecordLocks.__init__(directory: Path, *, timeout_code: str, timeout_seconds: float=30.0) -> None` - Remembers the lock directory, the error code raised when the lock stays held and the wait in seconds (default 30); a timeout that is not positive raises `ValueError`.
  - `RecordLocks.hold(record_id: str) -> Iterator[None]` *(contextmanager)* - Holds the exclusive lock of `<directory>/<file_safe_name(record_id)>.lock` (`exclusive_file_lock`) for the body of the `with` block; after the timeout it raises `AgentSdkError` with the configured code, naming the lock file. The thread that holds the lock may take it again (a per-thread depth count keyed by the absolute lock path), so a command can call a method that locks the same record; any other thread, in this process or another, waits. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.locked`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `state/orchestration.py::ControllerStateStore.locked`, `state/project_state_store.py::FileProjectStateStore.locked` (+1 more)
- `_depths() -> dict[str, int]` - The calling thread's table of the lock files it holds and how many times each; thread-local, created on first use. · *Called by:* `foundations/record_locks.py::RecordLocks.hold`

**Algorithms & invariants.** The lock is advisory: it covers the code that takes it, and every writer in the SDK does; readers do not, because a record is replaced atomically and a snapshot names the sidecar prefix it owns. The operating system releases the lock when its holder exits or is killed, so a crashed writer never leaves a stale lock; a run root on a network filesystem that does not implement file locking gets no cross-process exclusion from it. Re-entrance is per thread, not per task: no SDK code awaits while it holds a record lock, so two coroutines of one thread never interleave inside one. A command that spans stores takes the locks in a fixed order, orchestration, controller, run, project state, so two commands cannot wait for each other. `tests/state/test_concurrent_writers.py` drives several processes at one record and fails (with lost updates and a corrupt run history) when the locks are removed.

---

### `foundations/text.py` - newline-only line splitting and UTF-8 well-formedness for durable text

*50 lines · depends on: nothing in the package · used by: `foundations/errors.py`, `observability/audit_log.py`, `observability/telemetry_models.py`, `specifications/preprocessing.py`, `tools/artifacts.py`, `tools/core/helpers.py`, `tools/core/services.py` · not re-exported at the package root*

**Role in the workflow.** Durable stores and parsers split text with `split_lines`, which breaks only on `\r\n`, `\r` and `\n`, so characters such as U+2028 or a form feed inside a JSON string can no longer cut a record in two; ids and payloads that reach disk are checked or scrubbed for unpaired surrogates, which UTF-8 cannot encode.

**Contents**

- `split_lines(text: str, *, keepends: bool=False) -> list[str]` - Splits on `\r\n`, `\r` or `\n` only (unlike `str.splitlines`), dropping the empty final element a trailing newline would produce; `keepends=True` keeps the terminators. · *Called by:* `specifications/preprocessing.py::SpecificationPreprocessor._line_nodes`, `tools/artifacts.py::ArtifactStore.diff`, `core/helpers.py::_bounded_regex_search`, `core/helpers.py::_read_lines` (+2 more)
- `scrub_surrogates(text: str) -> str` - Replaces every unpaired surrogate code point with U+FFFD; ASCII text is returned untouched. · *Called by:* `foundations/errors.py::_redact_content`, `foundations/errors.py::_redact_value`, `foundations/errors.py::contains_secret_text`
- `assert_well_formed_text(value: str, field: str) -> str` - Returns the string or raises `ValueError` naming the field, the surrogate code point (`U+D800`) and its index. · *Called by:* `observability/audit_log.py::AuditLogEntry.identifiers_are_well_formed`, `observability/telemetry_models.py::TelemetryContext.identifiers_are_well_formed`
---

### `foundations/version.py` - the installed package name and version, and the identity strings derived from them

*23 lines · depends on: nothing in the package · used by: `mcp/server.py`, `observability/trace_context.py`, `tools/core/helpers.py`, `tools/core/services.py` · not re-exported at the package root*

**Role in the workflow.** What the package calls itself, in one place: the MCP server reports `PACKAGE_NAME` and `package_version()`, and the web tools identify themselves with `http_user_agent`. The version is read from the installed distribution's metadata, so `pyproject.toml` is the only place it is written.

**Contents**

- `package_version() -> str` - The installed distribution's version, read once and cached; `0+unknown` when the distribution is not installed (a source tree that was never `pip install`ed). · *Called by:* `foundations/version.py::http_user_agent`, `mcp/server.py::<module>`, `observability/trace_context.py::_process_resource`
- `http_user_agent(role: str) -> str` - `nailong-agent-sdk/<version> <role>`, the User-Agent of the web fetch and web search clients. · *Called by:* `core/helpers.py::_http_get_public`, `core/services.py::DuckDuckGoHtmlClient._search`

**Algorithms & invariants.** `PACKAGE_NAME` is the distribution name `nailong-agent-sdk`; the import name is `nailong_agent_sdk`.

*Module-level names:* `PACKAGE_NAME`

