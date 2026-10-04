# `foundations/` - dependency-free primitives every other folder builds on

Layer 0 of the package: nothing here imports from another SDK folder. It holds the serializable contracts (`contracts.py`, 62 modules depend on it), the typed error model and secret redaction (`errors.py`), crash-safe file publication, exclusive claims and cross-process locks (`atomic_io.py`), identifier validation and injective file names (`identifiers.py`), payload depth bounds (`json_limits.py`), newline-only text helpers (`text.py`), graph traversal helpers, opt-in logging, and the PCKP (precedence-constrained knapsack) solvers that the episode compactor and the evidence packer use to decide what to keep under a token budget.

| File | Lines | Role |
|---|---:|---|
| [`foundations/__init__.py`](#foundations__init__py---package-marker-for-the-dependency-free-layer) | 4 | package marker for the dependency-free layer |
| [`foundations/atomic_io.py`](#foundationsatomic_iopy---crash-safe-file-publication-exclusive-claims-and-cross-process-locks) | 114 | crash-safe file publication, exclusive claims and cross-process locks |
| [`foundations/benchmarks.py`](#foundationsbenchmarkspy---reproducible-exact-vs-greedy-pckp-benchmark-harness) | 88 | reproducible exact-vs-greedy PCKP benchmark harness |
| [`foundations/contracts.py`](#foundationscontractspy---the-serializable-baseagent-contract-definitions-tasks-turns-results-context-types) | 488 | the serializable BaseAgent contract: definitions, tasks, turns, results, context types |
| [`foundations/dependency_graph.py`](#foundationsdependency_graphpy---deterministic-cycle-and-blast-radius-traversal-over-dependent-prerequisite-edges) | 100 | deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges |
| [`foundations/errors.py`](#foundationserrorspy---typed-sdk-errors-and-secretreasoning-redaction-for-durable-records) | 236 | typed SDK errors and secret/reasoning redaction for durable records |
| [`foundations/identifiers.py`](#foundationsidentifierspy---identifier-validation-injective-file-names-and-collision-free-sequential-ids) | 60 | identifier validation, injective file names and collision-free sequential ids |
| [`foundations/json_limits.py`](#foundationsjson_limitspy---depth-bound-for-untrusted-json-like-payloads) | 25 | depth bound for untrusted JSON-like payloads |
| [`foundations/logging.py`](#foundationsloggingpy---opt-in-stdlib-logging-namespace-for-the-few-paths-outside-structured-telemetry) | 25 | opt-in stdlib logging namespace for the few paths outside structured telemetry |
| [`foundations/optimization/__init__.py`](#foundationsoptimization__init__py---re-exports-the-pckp-models-and-solvers) | 17 | re-exports the PCKP models and solvers |
| [`foundations/optimization/models.py`](#foundationsoptimizationmodelspy---pckp-problem-item-and-solution-contracts) | 91 | PCKP problem, item and solution contracts |
| [`foundations/optimization/solvers.py`](#foundationsoptimizationsolverspy---exact-tree-dp--branch-and-bound-and-greedy-pckp-solvers) | 490 | exact (tree DP / branch-and-bound) and greedy PCKP solvers |
| [`foundations/text.py`](#foundationstextpy---newline-only-line-splitting-and-utf-8-well-formedness-for-durable-text) | 36 | newline-only line splitting and UTF-8 well-formedness for durable text |

---

### `foundations/__init__.py` - package marker for the dependency-free layer

*4 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only; it re-exports nothing. Importers always use the deep module paths.

---

### `foundations/atomic_io.py` - crash-safe file publication, exclusive claims and cross-process locks

*114 lines · depends on: `foundations/errors.py` · used by: `agent/orchestrator/state_store.py`, `developer_tools/catalog.py`, `foundations/benchmarks.py`, `foundations/identifiers.py`, `memory/episode_store.py`, `observability/audit_log.py`, `observability/profiler.py`, `observability/telemetry_store.py` (+9 more) · not re-exported at the package root*

**Role in the workflow.** Every durable store in the SDK (project state, run records, telemetry reports, audit transcripts, tool-result journal, orchestration records, Gate 1 artifacts) writes a uniquely named temporary file (`unique_temporary_path`) first and then calls `replace_atomic`, so a reader never sees a half-written file and two writers never share a temporary name. `claim_exclusive` is the primitive behind collision-free id reservation, `exclusive_file_lock` serializes a critical section across processes, and `read_text_retrying` reads through the brief sharing violations a Windows reader sees while a writer replaces the file.

**Contents**

- `replace_atomic(temporary: Path, target: Path, *, attempts: int=5) -> None` - `os.replace`s the temporary file over the target. Only `PermissionError` is retried (up to `attempts`, sleeping 10 ms x attempt number) because on Windows a destination handle held briefly by another reader or by antivirus makes the rename fail transiently; any other error, and the final failed attempt, propagates. Rejects `attempts < 1`. The caller must have written and flushed the temp file. · *Called by:* `orchestrator/state_store.py::OrchestrationStateStore.save`, `orchestrator/state_store.py::OrchestrationStateStore.save_policy`, `memory/episode_store.py::FileEpisodeStore._atomic_write`, `observability/audit_log.py::_atomic_write` (+9 more)
- `read_text_retrying(path: Path, *, attempts: int=10) -> str` - Reads a UTF-8 text file, retrying only `PermissionError` (up to `attempts` times, sleeping 5 ms x attempt number) because on Windows a reader can be refused while a writer is replacing the file; the last attempt's error propagates. Rejects `attempts < 1`.
- `unique_temporary_path(target: Path) -> Path` - Returns a hidden sibling of the target named `.<name>.<pid>.<12 hex>.tmp`, so concurrent writers in one or many processes never collide on a temporary file.
- `claim_exclusive(path: Path) -> bool` - Creates the file with `O_CREAT | O_EXCL` (making its parent directory first) and returns True, or False if it already exists: an atomic claim that two racing callers can never both win.
- `exclusive_file_lock(path: Path, *, timeout_seconds: float=30.0, timeout_code: str='FILE_LOCK_TIMEOUT') -> Iterator[None]` *(contextmanager)* - Context manager that holds an operating-system lock on a dedicated lock file: `flock` on POSIX, byte-range locking on Windows (polled every 5 ms, raising `AgentSdkError` with the caller's `timeout_code` after `timeout_seconds`, default 30 s, with the likely causes).
- `_acquire_windows_lock(descriptor: int, path: Path, timeout_seconds: float, timeout_code: str) -> None` - Polls a non-blocking lock on byte 0 until `timeout_seconds` elapse, then raises `AgentSdkError(timeout_code)` naming the lock file and the likely causes (a writer hung mid-operation or software scanning the file).
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

*488 lines · depends on: `foundations/dependency_graph.py`, `foundations/errors.py`, `foundations/json_limits.py` · used by: `agent/base_agent/agent.py`, `agent/base_agent/types.py`, `agent/graph_agent_executor.py`, `agent/model.py`, `agent/openai_compatible/chat.py`, `agent/openai_compatible/semantic_gap.py`, `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py` (+55 more) · re-exported at the package root: 17 name(s)*

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
- **class `VersionedInstructions`** *(pydantic model; bases: StrictModel)* - Agent instruction text plus a version string (the version is part of node provenance and binding hashes). · *Instantiated by:* `agent/specialists.py::placeholder_specialist_definition`, `agent/specialists.py::planning_agent_definition`, `agent/specialists.py::rtl_worker_definition`
  - fields: `version`, `text`
- **class `FallbackModelBinding`** *(pydantic model; bases: StrictModel)* - A provider/model pair plus provider parameters; one entry in a failover chain.
  - fields: `provider`, `model`, `parameters`
- **class `ModelBinding`** *(class; bases: FallbackModelBinding)* - The primary model binding plus ordered fallbacks; the adapter checks it against the client it was given.
  - fields: `fallbacks`
  - `ModelBinding.fallback_bindings_are_distinct() -> ModelBinding` *(validator)* - Validator: no fallback may duplicate the primary (or another fallback) provider/model pair.
- **class `TerminationPolicy`** *(pydantic model; bases: StrictModel)* - Hard iteration cap, the output field that carries the status, and the escalation target for non-completed runs. · *Instantiated by:* `agent/specialists.py::placeholder_specialist_definition`, `agent/specialists.py::planning_agent_definition`, `agent/specialists.py::rtl_worker_definition`
  - fields: `max_iterations`, `status_field`, `escalation`
- **class `ToolDefinition`** *(pydantic model; bases: StrictModel)* - A tool the agent may call: name, description, JSON input schema, episode kind, concurrency, and `requires_manifest` (episodes of a tool that sets it are never compacted until an EDA manifest is attached). · *Instantiated by:* `agent/specialists.py::planning_agent_definition`, `agent/specialists.py::rtl_worker_definition`, `core/definitions.py::core_tool_definitions.definition`
  - fields: `name`, `description`, `input_schema`, `episode_kind`, `concurrency`, `requires_manifest`
  - `ToolDefinition.validate_input_schema(schema: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the tool's input schema must itself be a valid Draft 2020-12 JSON Schema.
- **class `AgentDefinition`** *(pydantic model; bases: StrictModel)* - The data-only agent description: identity, instructions, input/output schemas, tools, model binding, memory scope, termination policy and optional verification gate id. Runtime dependencies (model, executor, stores) are injected separately. · *Instantiated by:* `agent/specialists.py::placeholder_specialist_definition`, `agent/specialists.py::planning_agent_definition`, `agent/specialists.py::rtl_worker_definition`
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
- **class `RuntimeOptions`** *(pydantic model; bases: StrictModel)* - Deterministic-mode options bundle (scripted turns, watchdog timeouts, context/episode/preview/state budgets) accepted by the MCP run tool.
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
- `_validator_for(canonical_schema: str) -> Draft202012Validator` - LRU-cached (512) `Draft202012Validator` keyed by canonical schema text, avoiding rebuilding validators on every turn. · *Called by:* `foundations/contracts.py::_validate_instance`
- `_validate_instance(schema: dict[str, Any], instance: Any, label: str) -> None` - Runs the validator and converts the first jsonschema error into an `AgentSdkError` whose message includes the JSON path and reason, with `json_path`, `schema_rule`, sanitized `failed_value` and the full error text in `details`. · *Called by:* `foundations/contracts.py::validate_candidate_output`, `foundations/contracts.py::validate_task_input`, `foundations/contracts.py::validate_tool_arguments`
- `_canonical_json_schema(schema: dict[str, Any]) -> str | None` - Returns a cache key (sorted-key compact JSON) only when the schema survives JSON round-tripping; otherwise None so non-JSON schemas (for example Decimal constants) bypass the cache. · *Called by:* `foundations/contracts.py::_validate_instance`, `foundations/contracts.py::_validate_json_schema`

**Algorithms & invariants.** `AgentTurn` is a pydantic discriminated union on `type`, so a malformed turn fails with the offending variant and field path. Schema validation errors are deliberately field-specific because they are replayed to the model as correction hints. Tool-call arguments, tool-result outputs and final outputs are bounded to 64 nesting levels at validation time (`foundations/json_limits.py`).

*Module-level names:* `AgentTurn`

---

### `foundations/dependency_graph.py` - deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges

*100 lines · depends on: nothing in the package · used by: `foundations/contracts.py`, `foundations/optimization/models.py`, `specifications/gate.py`, `state/graph.py`, `state/planning.py` · not re-exported at the package root*

**Role in the workflow.** Used by plan/traceability validation: cycles make a plan invalid, and the reverse reachable set from a missing prerequisite is the list of dependents it breaks.

**Contents**

- `deterministic_cycles(nodes: Iterable[str], edges: Iterable[DependencyPair]) -> list[list[str]]` - Iterative depth-first search with an explicit stack (so a chain of any length is safe) over known nodes only: unknown endpoints are ignored so they can be reported separately as missing references. Nodes and neighbours are visited in sorted order and each detected cycle is returned as a closed path `[a, b, ..., a]`. Its output was checked identical to the earlier recursive version on 6,000 random graphs. · *Called by:* `specifications/gate.py::SpecificationGate.validate`, `state/graph.py::StateGraph._assert_acyclic`, `state/planning.py::PlanValidator._validate_cycles`
- `reverse_reachable_nodes(nodes: Iterable[str], edges: Iterable[DependencyPair], root_id: str) -> list[str]` - Breadth-first search from `root_id` along reversed edges: returns the sorted known dependents transitively affected by `root_id`. The root may be absent from `nodes`. · *Called by:* `foundations/dependency_graph.py::reverse_reachable_count`, `specifications/gate.py::SpecificationGate.admit_semantic_findings`
- `reverse_reachable_count(nodes: Iterable[str], edges: Iterable[DependencyPair], root_id: str) -> int` - Length of `reverse_reachable_nodes`; the blast-radius size. · *Called by:* `specifications/gate.py::SpecificationGate.validate`

*Module-level names:* `DependencyPair`

---

### `foundations/errors.py` - typed SDK errors and secret/reasoning redaction for durable records

*236 lines · depends on: `foundations/text.py` · used by: `agent/base_agent/agent.py`, `agent/model.py`, `agent/openai_compatible/chat.py`, `agent/openai_compatible/embeddings.py`, `agent/openai_compatible/semantic_gap.py`, `agent/openai_compatible/transport.py`, `agent/openai_compatible/vision.py`, `agent/verification.py` (+12 more) · not re-exported at the package root*

**Role in the workflow.** `AgentSdkError` (code, message, details) is the one structured exception the runtime converts into `AgentFailure`s; `TransientProviderError` marks retryable provider failures so `BaseAgent` and `FailoverAgentModel` retry instead of failing. The redaction functions run at every durable boundary (failure details, audit log, telemetry) so credentials and hidden model reasoning never reach disk.

**Contents**

- **class `AgentSdkError`** *(dataclass, exception; bases: Exception)* - Dataclass exception with a stable `code`, a human `message` (also its `str`) and optional `details`. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`, `base_agent/agent.py::BaseAgent._normalize_model_response`, `agent/model.py::FailoverAgentModel._call_with_failover`, `agent/model.py::ScriptedModel.next_turn` (+25 more)
  - fields: `code`, `message`, `details`
  - `AgentSdkError.__str__() -> str` - Returns the message so logs and tracebacks show the human text.
- **class `TransientProviderError`** *(dataclass, exception; bases: AgentSdkError)* - An `AgentSdkError` for failures likely to succeed on retry (429, 5xx, connection drop), carrying an optional server `retry_after_seconds`. · *Instantiated by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`, `openai_compatible/transport.py::_http_status_error`
  - fields: `retry_after_seconds`
- `sanitize_failure_details(details: dict[str, Any] | None) -> dict[str, Any]` - Redacts and bounds a details dict: secrets and hidden-reasoning keys are masked, and if the JSON exceeds 2,048 chars it is replaced by a truncation record with a content hash and preview. · *Called by:* `foundations/contracts.py::AgentFailure.details_are_safe`, `foundations/contracts.py::_validate_instance`
- `_redact_failure_value(value: Any) -> Any` - Recursive helper: masks dict values under secret or reasoning keys, scans strings for credential patterns, and recurses into lists. · *Called by:* `foundations/errors.py::sanitize_failure_details`
- `redact_secrets(value: Any) -> Any` - Shared redactor for audit log and telemetry: masks values under credential-shaped keys and credential-shaped spans inside strings, leaving surrounding text intact, and replaces lone surrogates so the result is always valid UTF-8. · *Called by:* `observability/audit_log.py::_bound_and_redact`, `observability/telemetry_store.py::TelemetryStore.append`
- `contains_secret_text(text: str) -> bool` - True when the free-text redactor would change the string (a credential-shaped span or `NAME=value` assignment), after scrubbing lone surrogates; used to refuse such text instead of masking it.
- `_redact_content(text: str) -> str` - Free-text pass: scrubs lone surrogates, skips the work entirely unless a cheap substring hint is present, then masks PEM private-key blocks, applies the credential regexes and the assignment scanner. · *Called by:* `foundations/errors.py::_redact_failure_value`, `foundations/errors.py::redact_secrets`
- `_redact_pem_blocks(text: str) -> str` - Linear scan that replaces each `-----BEGIN ... PRIVATE KEY-----` to `-----END ... PRIVATE KEY-----` block (found with a binary search over the end markers) with `[REDACTED]`; an unterminated block is left alone.
- `_redact_assignments(text: str) -> str` - Linear-time detector for `NAME_SECRET=value` or `"api_key": "value"` shapes: finds a keyword, widens to the whole identifier, and masks it plus the assignment tail. · *Called by:* `foundations/errors.py::_redact_content`
- `redact_hidden_reasoning(value: Any) -> Any` - Returns a copy of a payload in which every reserved reasoning key (`chain_of_thought`, `hidden_reasoning`, `reasoning_trace`, `scratchpad`) is renamed `<key>_redacted` with the value `[REDACTED]`, recursing through dicts, lists and tuples; used where a payload must be stored rather than rejected.
- `assert_no_hidden_reasoning(value: Any, path: str='$') -> None` - Raises `ValueError` naming the key and its JSON path (`$.a[0].b`) if any dict key is reserved for private model reasoning; durable telemetry and audit records refuse such payloads outright. · *Called by:* `observability/audit_log.py::AuditLogEntry.safe_payload`, `observability/telemetry_models.py::TelemetryEvent.reject_hidden_reasoning`

**Algorithms & invariants.** Detection is best-effort: key-name regex (authorization, api key, password, secret, token excluding budget/cost/plural counters, cookie, credential, private key) plus patterns for PEM private keys, `sk-` keys, AWS `AKIA` ids, GitHub tokens, Slack tokens and Bearer tokens. Patterns are anchored on literal prefixes so none can backtrack super-linearly.

*Module-level names:* `_SECRET_KEY`, `_HIDDEN_REASONING_KEYS`, `_MAX_FAILURE_DETAIL_CHARS`, `_SECRET_CONTENT_PATTERNS`, `_ASSIGNMENT_TAIL`, `_IDENTIFIER_CHAR`, `_CONTENT_HINT_SUBSTRINGS`

---

### `foundations/identifiers.py` - identifier validation, injective file names and collision-free sequential ids

*60 lines · depends on: `foundations/atomic_io.py` · used by: `agent/orchestrator/state_store.py`, `observability/audit_log.py`, `observability/telemetry_store.py`, `specifications/preprocessing.py`, `state/elastic.py`, `state/orchestration.py`, `state/run_state_store.py`, `state/shared_state.py` · not re-exported at the package root*

**Role in the workflow.** Every store that turns a caller-supplied id into a path validates it with `validate_identifier` or maps it with `file_safe_name`, so `../../x` can never leave the run root; the stores that number their own ids (`run-1`, `controller-2`, `orchestration-3`) reserve them through `reserve_sequential_identifier`.

**Contents**

- `is_valid_identifier(value: object) -> bool` - True for a string of 1 to 128 characters of letters, digits, `.`, `_` and `-` that starts with a letter or digit and does not end with a dot. · *No in-package callers (public API, entry point, or protocol hook).*
- `validate_identifier(value: object, kind: str) -> str` - Returns the value or raises `ValueError` naming the kind, the offending value and the allowed shape; non-strings are rejected the same way. · *No in-package callers (public API, entry point, or protocol hook).*
- `file_safe_name(value: str) -> str` - Returns the value unchanged when it is already a safe file name (at most 128 characters of letters, digits, `.`, `_`, `-`; not `.`, `..` or ending with a dot); otherwise a readable sanitized stem (at most 80 characters) plus `~` and the first 12 hex characters of the SHA-256 of the original, so two different inputs never share a file. · *No in-package callers (public API, entry point, or protocol hook).*
- `reserve_sequential_identifier(claims: Path, prefix: str, is_taken: Callable[[str], bool], *, start: int=1) -> tuple[str, int]` - Walks `<prefix>-<n>` from `start`, skips ids that `is_taken` reports, and returns the first one whose claim file under `claims` it creates exclusively (`claim_exclusive`) together with the next number to try; two processes can never receive the same id. · *No in-package callers (public API, entry point, or protocol hook).*

**Algorithms & invariants.** A safe file name never contains `~`, so a mapped name cannot equal a passthrough name; two different unsafe inputs share a file only if their 12-hex digests collide. Reservation relies on exclusive file creation rather than on checking whether a record exists, which is what makes it safe across processes.

---

### `foundations/json_limits.py` - depth bound for untrusted JSON-like payloads

*25 lines · depends on: nothing in the package · used by: `foundations/contracts.py`, `specifications/preprocessing.py`, `state/graph_models.py`, `state/project_state_models.py`, `state/shared_state.py` · not re-exported at the package root*

**Role in the workflow.** Validators on tool-call arguments, tool results, final outputs, graph node outputs and metadata, shared-state payloads, project-state transitions and parsed specification documents call `assert_json_depth` so a hostile or runaway payload is refused with a message naming the field, instead of failing later when pydantic serializes it.

**Contents**

- `assert_json_depth(value: Any, label: str, limit: int=MAX_JSON_DEPTH) -> Any` - Walks dicts, lists and tuples with an explicit stack (no recursion limit) and returns the value, or raises `ValueError` `<label> nests more than <limit> levels deep; the limit is <limit>.` when any branch exceeds `limit` (default `MAX_JSON_DEPTH`, 64). · *No in-package callers (public API, entry point, or protocol hook).*

**Algorithms & invariants.** The limit exists because pydantic's JSON-mode dump fails for payloads nested about 100 levels deep; 64 keeps every accepted payload serializable with a wide margin.

---

### `foundations/logging.py` - opt-in stdlib logging namespace for the few paths outside structured telemetry

*25 lines · depends on: nothing in the package · used by: `agent/model.py`, `mcp/client.py`, `tools/tasks.py` · not re-exported at the package root*

**Role in the workflow.** The primary record is the hash-chained telemetry/audit store; logging only covers cases such as a host callback raising. A NullHandler keeps the SDK silent unless the host attaches its own handler to the `nailong_agent_sdk` logger.

**Contents**

- `get_logger(name: str) -> logging.Logger` - Returns a logger named `nailong_agent_sdk.<name>`. · *Called by:* `agent/model.py::<module>`, `mcp/client.py::<module>`, `tools/tasks.py::<module>`

*Module-level names:* `_ROOT_LOGGER_NAME`

---

### `foundations/optimization/__init__.py` - re-exports the PCKP models and solvers

*17 lines · depends on: `foundations/optimization/models.py`, `foundations/optimization/solvers.py` · used by: `foundations/benchmarks.py`, `memory/episode_store.py`, `specifications/evidence_graph.py` · not re-exported at the package root*

**Role in the workflow.** Convenience import surface for `ExactPckpSolver`, `GreedyPckpBaseline` and the problem/solution contracts.

---

### `foundations/optimization/models.py` - PCKP problem, item and solution contracts

*91 lines · depends on: `foundations/contracts.py`, `foundations/dependency_graph.py` · used by: `foundations/optimization/__init__.py`, `foundations/optimization/solvers.py` · re-exported at the package root: 4 name(s)*

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

*490 lines · depends on: `foundations/optimization/models.py` · used by: `foundations/optimization/__init__.py` · re-exported at the package root: 2 name(s)*

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
  - `_Selection.join(left: _Selection, right: _Selection) -> _Selection` *(classmethod)* - Joins two ropes, returning the other one unchanged when either is empty.
  - `_Selection.ids() -> tuple[str, ...]` *(property)* - Flattens the rope to a tuple with an explicit stack (no recursion limit).
  - `_Selection.key() -> tuple[tuple[int, str], ...]` *(property)* - Cached `tie_key` of the flattened ids.
- `_solve_rooted_forest(problem: PckpProblem, mandatory: set[str], problem_hash: str) -> PckpSolution` - Exact Pareto-frontier DP for a forest where each item has at most one prerequisite: each subtree keeps only the states no cheaper state matches in utility (so the table is bounded by the number of distinct utilities, not by the budget), child tables merge into their parent and root tables merge into the answer. Ties go to the lower cost, then `tie_key`. Always OPTIMAL. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
  - `_solve_rooted_forest.subtree_states(root_id: str) -> _States` - Builds the state table of one tree bottom-up over its preorder (no recursion), merging each child's table into its parent's; a mandatory child can never be left out.
- `_offer(states: _States, cost: int, utility: int, selection: _Selection) -> None` - Records a (cost, utility, selection) candidate if its cost slot is empty or it has higher utility or, at equal utility, a smaller `tie_key`.
- `_merge_states(left: _States, right: _States, budget: int, *, keep_left: bool) -> _States` - Combines two state tables under the budget: optionally keeps each left state alone (when the right subtree is optional), adds every affordable left+right pair and returns the dominance-pruned result.
- `_prune_dominated(states: _States) -> _States` - Keeps only states whose utility strictly exceeds that of every cheaper state. · *Called by:* `optimization/solvers.py::_solve_rooted_forest`
- `_frontier_bound(problem: PckpProblem) -> int` - `min(budget, total cost, total utility + 1)`: an upper bound on the number of states a frontier table can hold, used to decide whether the DP applies.
- `_dependents(items: Iterable[PckpItem]) -> dict[str, set[str]]` - Inverts the prerequisite relation into item -> set of dependents. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
- `_closure(seeds: set[str], items: dict[str, PckpItem]) -> set[str]` - All prerequisites reachable from the seeds, including the seeds (iterative, deterministic order). · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve`
- `_propagate(selected: set[str], excluded: set[str], items: dict[str, PckpItem], dependents: dict[str, set[str]]) -> tuple[set[str], set[str]] | None` - Fixpoint constraint propagation: selecting an item selects its prerequisites; excluding an item excludes its dependents; returns None on contradiction. · *No in-package callers (public API, entry point, or protocol hook).*
- `_fractional_upper_bound(selected: set[str], excluded: set[str], items: dict[str, PckpItem], density_order: list[PckpItem], budget: int) -> Fraction` - Optimistic utility bound: selected utility plus a greedy fractional fill of the remaining budget by utility density (free items always taken); -1 when the selection already exceeds the budget. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`
- `_cost(selected: Iterable[str], items: dict[str, PckpItem]) -> int` - Sum of token costs of a selection. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`, `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve` (+2 more)
- `_utility(selected: Iterable[str], items: dict[str, PckpItem]) -> int` - Sum of utilities of a selection. · *Called within this file by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`, `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve` (+1 more)
- `_is_rooted_forest(items: Iterable[PckpItem]) -> bool` - True when every item has at most one prerequisite (the tree-DP precondition). · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`
- `_problem_hash(problem: PckpProblem) -> str` - SHA-256 of the canonical problem JSON; ties a certificate to its exact input. · *Called by:* `optimization/solvers.py::ExactPckpSolver.solve`, `optimization/solvers.py::GreedyPckpBaseline.solve`
- `_fraction_text(value: Fraction) -> str` - Renders a `Fraction` as an integer or `n/d` string for exact bounds in certificates. · *Called by:* `optimization/solvers.py::ExactPckpSolver._solve_branch_and_bound`
- `_descending_id_key(value: str) -> tuple[int, ...]` - Negated code points so `max` picks the lexicographically first id. · *Called by:* `optimization/solvers.py::GreedyPckpBaseline.solve`

**Algorithms & invariants.** All arithmetic is exact (integers and `Fraction`), ordering is fixed (sorted ids), and no randomness or model call is involved, so a certificate is reproducible from its problem hash. `OPTIMAL` is only ever reported after a full proof; a bounded search is BEST_EFFORT. The tree DP and the branch-and-bound search break ties identically (higher utility, then lower cost, then `tie_key`), which is why both return the same selection.
---

### `foundations/text.py` - newline-only line splitting and UTF-8 well-formedness for durable text

*36 lines · depends on: nothing in the package · used by: `foundations/errors.py`, `observability/audit_log.py`, `specifications/preprocessing.py`, `tools/artifacts.py`, `tools/core/helpers.py`, `tools/core/services.py`, `tools/registry.py` · not re-exported at the package root*

**Role in the workflow.** Durable stores and parsers split text with `split_lines`, which breaks only on `\r\n`, `\r` and `\n`, so characters such as U+2028 or a form feed inside a JSON string can no longer cut a record in two; ids and payloads that reach disk are checked or scrubbed for unpaired surrogates, which UTF-8 cannot encode.

**Contents**

- `split_lines(text: str, *, keepends: bool=False) -> list[str]` - Splits on `\r\n`, `\r` or `\n` only (unlike `str.splitlines`), dropping the empty final element a trailing newline would produce; `keepends=True` keeps the terminators. · *No in-package callers (public API, entry point, or protocol hook).*
- `scrub_surrogates(text: str) -> str` - Replaces every unpaired surrogate code point with U+FFFD; ASCII text is returned untouched. · *No in-package callers (public API, entry point, or protocol hook).*
- `assert_well_formed_text(value: str, field: str) -> str` - Returns the string or raises `ValueError` naming the field, the surrogate code point (`U+D800`) and its index. · *No in-package callers (public API, entry point, or protocol hook).*
