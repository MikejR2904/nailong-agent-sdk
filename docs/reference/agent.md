# `agent/` - the BaseAgent turn loop, model adapters, verification and orchestration

`base_agent/` is the single-task runtime: one `BaseAgent.run` is a bounded loop of context projection, one model turn, and governed tool execution until the model returns an accepted final answer or the run is blocked, failed, cancelled or out of iterations. `model.py` defines the provider-neutral model contract; `openai_compatible/` implements it (plus embeddings, vision and semantic-gap analysis) over Chat Completions. `verification.py` holds named, host-registered output gates. `runtime.py` wires durable stores around an agent, `graph_agent_executor.py` runs agents as graph nodes, and `orchestrator/` compiles a user policy and an already-proposed plan into a controller-governed multi-agent graph. Model output is always treated as untrusted proposals.

| File | Lines | Role |
|---|---:|---|
| [`agent/__init__.py`](#agent__init__py---package-marker-for-agent-execution) | 3 | package marker for agent execution |
| [`agent/base_agent/__init__.py`](#agentbase_agent__init__py---public-surface-of-the-baseagent-runtime-package) | 28 | public surface of the BaseAgent runtime package |
| [`agent/base_agent/agent.py`](#agentbase_agentagentpy---the-single-task-agent-loop) | 1918 | the single-task agent loop |
| [`agent/base_agent/types.py`](#agentbase_agenttypespy---small-value-types-used-by-the-baseagent-loop) | 74 | small value types used by the BaseAgent loop |
| [`agent/graph_agent_executor.py`](#agentgraph_agent_executorpy---runs-registered-baseagent-bindings-as-graph-nodes) | 195 | runs registered BaseAgent bindings as graph nodes |
| [`agent/model.py`](#agentmodelpy---provider-neutral-model-contract-streaming-events-and-failover) | 326 | provider-neutral model contract, streaming events and failover |
| [`agent/openai_compatible/__init__.py`](#agentopenai_compatible__init__py---public-surface-of-the-openai-compatible-adapters) | 44 | public surface of the OpenAI-compatible adapters |
| [`agent/openai_compatible/chat.py`](#agentopenai_compatiblechatpy---chat-completions-adapter-for-the-agent-model-contract) | 576 | Chat Completions adapter for the agent model contract |
| [`agent/openai_compatible/embeddings.py`](#agentopenai_compatibleembeddingspy---synchronous-embedding-provider-for-retrieval-indexes) | 68 | synchronous embedding provider for retrieval indexes |
| [`agent/openai_compatible/semantic_gap.py`](#agentopenai_compatiblesemantic_gappy---model-proposed-semantic-findings-for-gate-1) | 232 | model-proposed semantic findings for Gate 1 |
| [`agent/openai_compatible/transport.py`](#agentopenai_compatibletransportpy---json-http-transports-and-endpoint-configuration) | 379 | JSON HTTP transports and endpoint configuration |
| [`agent/openai_compatible/vision.py`](#agentopenai_compatiblevisionpy---image-proposals-through-a-verified-byte-loader) | 162 | image proposals through a verified byte loader |
| [`agent/orchestrator/__init__.py`](#agentorchestrator__init__py---public-surface-of-the-orchestrator-package) | 31 | public surface of the orchestrator package |
| [`agent/orchestrator/models.py`](#agentorchestratormodelspy---user-owned-orchestration-policy-request-and-record-contracts) | 170 | user-owned orchestration policy, request and record contracts |
| [`agent/orchestrator/orchestrator.py`](#agentorchestratororchestratorpy---deterministic-composition-of-policy-plan-controller-and-graph-execution) | 637 | deterministic composition of policy, plan, controller and graph execution |
| [`agent/orchestrator/state_store.py`](#agentorchestratorstate_storepy---atomic-local-persistence-of-orchestration-records-and-policies) | 57 | atomic local persistence of orchestration records and policies |
| [`agent/runtime.py`](#agentruntimepy---composition-root-for-the-durable-stores-around-an-agent) | 100 | composition root for the durable stores around an agent |
| [`agent/specialists.py`](#agentspecialistspy---declarative-agent-definitions-for-the-frameworks-roles) | 178 | declarative agent definitions for the framework's roles |
| [`agent/verification.py`](#agentverificationpy---host-registered-bounded-output-acceptance-gates) | 223 | host-registered, bounded output-acceptance gates |

---

### `agent/__init__.py` - package marker for agent execution

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only; public names are re-exported from the package root.

---

### `agent/base_agent/__init__.py` - public surface of the BaseAgent runtime package

*28 lines · depends on: `agent/base_agent/agent.py`, `agent/base_agent/types.py` · used by: `agent/graph_agent_executor.py`, `agent/runtime.py`, `integrations/langchain.py`, `integrations/langgraph.py`, `mcp/agent_tools.py` · not re-exported at the package root*

**Role in the workflow.** Re-exports `BaseAgent`, `project_state_hash_from_result` and the helper types.

---

### `agent/base_agent/agent.py` - the single-task agent loop

*1918 lines · depends on: `agent/base_agent/types.py`, `agent/model.py`, `agent/verification.py`, `foundations/contracts.py`, `foundations/errors.py`, `memory/context.py`, `memory/context_projection.py`, `memory/episode_models.py`, `memory/episode_store.py`, `memory/episodes.py`, `observability/audit_log.py`, `observability/metrics.py`, `observability/profiler.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `state/project_state_engine.py`, `state/project_state_models.py`, `state/project_state_store.py`, `tools/tools.py` · used by: `agent/base_agent/__init__.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Per iteration: check the run deadline and cancellation; project context (compact episodes, select observations); project the bounded project state; enforce the three budget guards; call the model under a watchdog; validate the response as an `AgentTurn`; then act on it. A `blocked` turn ends BLOCKED; a `final` turn is validated and optionally gated; a tool turn runs a governed batch whose results become observations, episodes and project-state transitions. Every exit goes through `_terminate`, which records the agent result in project state, seals the profile, emits telemetry and audit entries, and builds the `AgentResult`. An instance runs one task (its profiler allows a single run).

**Contents**

- **class `BaseAgent`** *(class)* - Runs one scoped task through a bounded, observable tool loop; model output is untrusted and only declared tools execute. · *Instantiated by:* `agent/runtime.py::AgentRuntimeServices.create_agent`, `mcp/agent_tools.py::register_agent_tools.run_agent_task`
  - `BaseAgent.__init__(definition: AgentDefinition, model: AgentModel, tool_executor: ToolExecutor | None=None, verification_gates: VerificationGateRegistry | None=None,...` - Wires the definition, model, optional tool executor, verification registry, hooks, projectors, journal, project-state store, episode store factory, telemetry, audit logs, profiler and stream listener. Raises `CROSS_SESSION_STORE_REQUIRED` for `CROSS_SESSION` memory in every case: persistent cross-session stores are not supported yet, and the message says so.
  - `BaseAgent.run(task: ScopedAgentTask, cancellation: CancellationToken | None=None) -> AgentResult` *(async)* - Validates the task, assembles the immutable prompt, opens the project state and the per-run episode graph and store, then loops iterations as described above until a terminal result; raises only for malformed caller input, a re-entrant call or programming errors. Malformed model output (including a single tool-call turn that lists dependencies) is recorded as a failure, not raised.
    - `BaseAgent.run.emit(event_type: str, iteration: int, **details: Any) -> None` - Appends a lifecycle event, emits a deterministic-authority telemetry event (ERROR severity for a non-completed `terminated` or `escalated`), and appends a hash-chained audit entry that cites the telemetry event id. · *Called within this file by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._apply_tool_outcome_to_project_state`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`, `base_agent/agent.py::BaseAgent._execute_tool_batch` (+4 more)
  - `BaseAgent._normalize_model_response(response: Any, continuation: ProviderContinuation | None) -> tuple[Any, ProviderContinuation | None, ProviderUsage | None]` - Unwraps a `ModelTurnResponse` (turn, continuation, usage) or a bare turn and validates it against the `AgentTurn` union; a failure raises `MODEL_TURN_INVALID` listing up to five `field.path: message` entries. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._accept_final_turn(output: Any, task: ScopedAgentTask, iteration: int, prompt: Any, episodes: InMemoryEpisodeGraph, events: list[AgentLifecycleEvent], emit: Callable...` *(async)* - Validates the candidate output against the output schema (a failure becomes an `agent-error` observation and the loop continues), runs the verification gate under its watchdog (timeout, typed error, unexpected exception, missing decision and a rejection each end FAILED with a distinct code), and otherwise terminates COMPLETED. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._execute_tool_batch(task: ScopedAgentTask, iteration: int, batch: ToolBatchTurn, prompt: Any, episodes: InMemoryEpisodeGraph, memory: InMemoryEpisodeStore, events: li...` *(async)* - Schedules a batch's calls by declared dependencies: a call whose dependency did not succeed is recorded blocked; one serial (or undeclared) call runs alone, otherwise all ready parallel calls run together; each outcome is reduced into project state; a blocked result stops the remaining calls and ends BLOCKED; a terminal outcome ends the run; otherwise returns observations and provider results in call order. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._execute_profiled_tool_call(task: ScopedAgentTask, iteration: int, call: ToolCall, prior_outcomes: dict[str, ToolCallOutcome], episodes: InMemoryEpisodeGraph, memory: InMemor...` *(async)* - Wraps one tool call in a profiler span whose status mirrors the result status. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`
  - `BaseAgent._execute_tool_call(task: ScopedAgentTask, iteration: int, call: ToolCall, prior_outcomes: dict[str, ToolCallOutcome], episodes: InMemoryEpisodeGraph, memory: InMemor...` *(async)* - Rejects an undeclared tool, a missing executor and invalid arguments (each ends FAILED), builds the invocation context with the merged consumed episode ids, runs pre-tool hooks (a denial ends BLOCKED), the executor and post-tool hooks under the tool watchdog, maps timeout, SDK error and unexpected exception to typed failures, then records the episode, the audit `tool-result` entry and the `tool-completed` event. · *Called by:* `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`
  - `BaseAgent._record_executed_result(tool: ToolDefinition, call: ToolCall, result: ToolExecutionResult, iteration: int, episodes: InMemoryEpisodeGraph, memory: InMemoryEpisodeStore) -...` - Writes the result into the episode graph and the episode store (exploratory episodes are opened and closed immediately; action episodes also carry consumed ids, the manifest requirement and any `manifest` in the output), stores the full result in the journal through the projector and returns the outcome with a handle-bearing observation. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_call`
  - `BaseAgent._pask_relevance_query(task: ScopedAgentTask) -> str` *(staticmethod)* - The bounded task signal (scope label, instructions, acceptance criteria, at most 16,384 characters) used by PASK compaction scoring. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._record_unexecuted_result(call: ToolCall, result: ToolExecutionResult, iteration: int, terminal_status: AgentRunStatus | None=None) -> ToolCallOutcome` - Builds an outcome for a call that never ran (blocked or rejected), still journalled so the model can see why. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`, `base_agent/agent.py::BaseAgent._execute_tool_call`
  - `BaseAgent._tool_message(call: ToolCall, result: ToolExecutionResult, handle_id: str) -> str` *(staticmethod)* - The one-line observation text naming the tool, its status, the error and the result handle. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`, `base_agent/agent.py::BaseAgent._record_unexecuted_result`
  - `BaseAgent._provider_tool_result(outcome: ToolCallOutcome) -> ProviderToolResult` - A one-time JSON projection of the result, truncated to the preview limit, for adapters that need provider-native tool-result continuation. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`
  - `BaseAgent._effective_call(call: ToolCall, prior_outcomes: dict[str, ToolCallOutcome]) -> ToolCall` - Adds the exploratory episode ids produced by a call's dependencies to its consumed episode ids, deduplicated in order. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_call`
  - `BaseAgent._tool_definition(call: ToolCall) -> ToolDefinition | None` - Looks a call's tool up among the definition's declared tools. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`, `base_agent/agent.py::BaseAgent._execute_tool_call`
  - `BaseAgent._await_with_watchdog(operation: Awaitable[Any], operation_timeout_seconds: float | None, deadline: float | None) -> Any` *(async, staticmethod)* - Awaits an operation under the smaller of its own timeout and the remaining run deadline; raises `TimeoutError` when either expires. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._record_provider_usage(telemetry_context: TelemetryContext, usage: ProviderUsage, iteration: int) -> None` - Records provider-reported token counters as metrics, or an unavailable marker with a reason for each counter the provider omitted, and the remaining-context metric when computable. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._terminate(status: AgentRunStatus, task: ScopedAgentTask, iterations: int, reason: str | None, prompt: Any, episodes: InMemoryEpisodeGraph, events: list[Agen...` - Single exit path: records the agent result as a project-state transition (a recording failure demotes COMPLETED to FAILED and keeps the output), builds the escalation when the status is not COMPLETED, emits `terminated`, `escalated` and `state-updated`, finishes the profiler, emits the profile telemetry and terminal metrics (derived from at most the run's first 1,000 telemetry events), appends the audit profile entry and returns the `AgentResult`. · *Called by:* `base_agent/agent.py::BaseAgent._accept_final_turn`, `base_agent/agent.py::BaseAgent._execute_tool_batch`, `base_agent/agent.py::BaseAgent._record_tool_outcome`, `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._record_tool_outcome(outcome: ToolCallOutcome, task: ScopedAgentTask, iteration: int, prompt: Any, episodes: InMemoryEpisodeGraph, events: list[AgentLifecycleEvent], e...` - Applies an outcome to project state; a failure terminates the run with a typed state-update failure. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`
  - `BaseAgent._apply_tool_outcome_to_project_state(outcome: ToolCallOutcome, emit: Callable[..., None], iteration: int) -> ProjectState` - Reduces a tool outcome into a `TOOL_OUTCOME` transition (using the configured action summary size) and emits `state-updated`. · *Called by:* `base_agent/agent.py::BaseAgent._record_tool_outcome`
  - `BaseAgent._episode_summary_text(call: ToolCall, result: ToolExecutionResult, *, max_chars: int=320) -> str` *(staticmethod)* - One-line episode summary: tool, bounded arguments, status and a bounded output or error. · *Called by:* `base_agent/agent.py::BaseAgent._record_executed_result`
  - `BaseAgent._bounded_json_text(value: Any, max_chars: int) -> str` *(staticmethod)* - Canonical JSON text, truncated with a marker past the limit. · *Called by:* `base_agent/agent.py::BaseAgent._episode_summary_text`
  - `BaseAgent._project_id_for(task: ScopedAgentTask) -> str` *(staticmethod)* - Project id from `scope.boundaries['project_id']`, else the task id. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._stage_schema_for(task: ScopedAgentTask) -> StageStateSchema` *(staticmethod)* - Stage schema from `scope.boundaries['stage']`, else `unclassified`. · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._estimate_model_context_tokens(prompt: Any, project_state_view: Any) -> int` *(staticmethod)* - Estimates tokens of only the immutable prompt plus the bounded state view (JSON length divided by four). · *Called by:* `base_agent/agent.py::BaseAgent.run`
  - `BaseAgent._with_projection_history(result: AgentResult, projection_history: Sequence[ContextProjectionMetadata]) -> AgentResult` *(staticmethod)* - Attaches the per-iteration projection metadata to a result. · *Called by:* `base_agent/agent.py::BaseAgent.run`
- `_state_update_failure(error: Exception) -> AgentFailure` - Converts a state-recording exception into a typed `AgentFailure` (`PROJECT_STATE_UPDATE_FAILED` for non-SDK errors). · *Called by:* `base_agent/agent.py::BaseAgent._record_tool_outcome`, `base_agent/agent.py::BaseAgent._terminate`
- `_demote_for_state_failure(status: AgentRunStatus, reason: str | None, failure: AgentFailure | None, state_failure: AgentFailure) -> tuple[AgentRunStatus, str, AgentFailure ...` - Turns a COMPLETED run whose result could not be recorded into FAILED, or appends the recording failure to another status's reason. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`
- `project_state_hash_from_result(status: AgentRunStatus, output: Any, reason: str | None) -> str` - Content hash of status, output and reason, stored as terminal provenance without retaining the output. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`

**Algorithms & invariants.** Failure handling summary: a transient provider error becomes an `agent-error` observation and costs one iteration; any other model error, a watchdog timeout, an undeclared tool, bad tool arguments and a verification rejection end FAILED immediately; a denied or blocked tool ends BLOCKED. The three guards are CONTEXT_DEADLOCK, PROJECT_STATE_BUDGET_EXCEEDED and CONTEXT_BUDGET_EXCEEDED (all BLOCKED). Graph episode ids and episode-store ids are assumed to coincide (both sequential), which holds only for a fresh store.

*Module-level names:* `_AGENT_TURN_ADAPTER`

---

### `agent/base_agent/types.py` - small value types used by the BaseAgent loop

*74 lines · depends on: `agent/model.py`, `foundations/contracts.py`, `tools/tools.py` · used by: `agent/base_agent/__init__.py`, `agent/base_agent/agent.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `BaseAgent` accepts a cancellation token, a watchdog policy and tool hooks, and returns `ToolCallOutcome`/`ToolBatchExecution` internally.

**Contents**

- **class `CancellationToken`** *(Protocol; bases: Protocol)* - Protocol for anything with an `is_cancelled()` method; checked once per iteration.
  - `CancellationToken.is_cancelled() -> bool` - Return True when the caller wants the run to stop. · *Called by:* `base_agent/agent.py::BaseAgent.run`
- **class `ToolHookDecision`** *(class)* - Return value of a pre-tool hook: allowed or denied, with a reason.
  - `ToolHookDecision.__init__(allowed: bool, reason: str | None=None) -> None` - Stores `allowed` and `reason`.
- **class `AgentWatchdogPolicy`** *(class)* - Optional wall-clock limits: whole-run deadline and per-model-turn, per-tool-call and per-verification timeouts. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`, `mcp/agent_tools.py::register_agent_tools.run_agent_task`
  - `AgentWatchdogPolicy.__init__(*, run_deadline_seconds: float | None=None, model_turn_timeout_seconds: float | None=None, tool_call_timeout_seconds: float | None=None, verificat...` - Stores the four limits, rejecting any non-positive value with a message naming the field.
- **class `ToolCallOutcome`** *(dataclass)* - One tool call's call, result, model observation and episode id, plus a terminal status and reason when the call must end the run. · *Instantiated by:* `base_agent/agent.py::BaseAgent._record_executed_result`, `base_agent/agent.py::BaseAgent._record_unexecuted_result`
  - fields: `call`, `result`, `observation`, `episode_id`, `episode_kind`, `terminal_status`, `terminal_reason`
- **class `ToolBatchExecution`** *(dataclass)* - Observations and provider tool-result projections of a completed batch. · *Instantiated by:* `base_agent/agent.py::BaseAgent._execute_tool_batch`
  - fields: `observations`, `provider_results`

**Algorithms & invariants.** `PreToolHook` and `PostToolHook` are the async hook callable types, defined here.

*Module-level names:* `PreToolHook`, `PostToolHook`

---

### `agent/graph_agent_executor.py` - runs registered BaseAgent bindings as graph nodes

*195 lines · depends on: `agent/base_agent/__init__.py`, `agent/model.py`, `agent/runtime.py`, `agent/verification.py`, `foundations/contracts.py`, `memory/context_projection.py`, `memory/episode_store.py`, `state/graph_models.py`, `state/project_state_models.py`, `tools/tools.py` · used by: `agent/orchestrator/orchestrator.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** The controller's `execute_graph` receives `GraphAgentExecutor.executors()`; each agent or elastic node looks up its binding, builds task, model and tool executor from the node context, runs the agent and converts its result into a `GraphNodeResult` with a provenance hash.

**Contents**

- **class `GraphAgentBinding`** *(dataclass)* - Host-owned binding for one node: definition, task adapter, model factory, optional tool executor factory, gates, hooks, version, idempotence flag and per-node policies. Factories are not serialised.
  - fields: `node_id`, `definition`, `task_adapter`, `model_factory`, `tool_executor_factory`, `verification_gates`, `pre_tool_hooks`, `post_tool_hooks`, `binding_version`, `idempotent`, `watchdog_policy`, `context_projection_policy`, `project_state_projection_policy`, `episode_store_factory`
  - `GraphAgentBinding.binding_hash() -> str` *(property)* - Property: hash of node id, agent identity, instructions version, binding version and idempotence, included in node provenance. · *Called by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`
- **class `GraphAgentExecutor`** *(class)* - Executor that runs bindings through one `AgentRuntimeServices`. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`
  - `GraphAgentExecutor.__init__(services: AgentRuntimeServices, bindings: Mapping[str, GraphAgentBinding]) -> None` - Requires unique node ids and map keys equal to each binding's node id.
  - `GraphAgentExecutor.executors() -> dict[GraphNodeKind, NodeExecutor]` - Maps both the agent and elastic node kinds to `execute`. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`, `state/controller_runtime.py::ControllerRuntime.execute_graph`, `state/graph.py::StateGraph.execute.execute_one`, `state/harness_coordinator.py::HarnessCoordinator.execute_run.execute_one`
  - `GraphAgentExecutor.idempotent_node_ids() -> set[str]` - Ids of bindings declared replay-safe, used by crash recovery. · *No in-package callers (public API, entry point, or protocol hook).*
  - `GraphAgentExecutor.execute(node: GraphNode, context: GraphNodeExecutionContext) -> GraphNodeResult` *(async)* - Fails if no binding exists; otherwise builds task, model, tool executor and projection policy, runs the agent and returns its status, output, reason and diagnostics (status, iteration count, binding hash) with a hash of the agent result payload as provenance. Any exception becomes a FAILED node result naming the exception type. · *Called within this file by:* `agent/graph_agent_executor.py::GraphAgentExecutor.executors`
- `_graph_status(status: AgentRunStatus) -> GraphNodeStatus` - Maps agent run statuses to graph node statuses. · *Called by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`
- `_hash_payload(payload: Any) -> str` - SHA-256 of canonical JSON. · *Called by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`

*Module-level names:* `GraphTaskAdapter`, `GraphModelFactory`, `GraphToolExecutorFactory`, `GraphContextProjectionPolicyFactory`

---

### `agent/model.py` - provider-neutral model contract, streaming events and failover

*326 lines · depends on: `foundations/contracts.py`, `foundations/errors.py`, `foundations/logging.py`, `state/project_state_models.py` · used by: `agent/base_agent/agent.py`, `agent/base_agent/types.py`, `agent/graph_agent_executor.py`, `agent/openai_compatible/chat.py`, `agent/runtime.py`, `integrations/langchain.py`, `integrations/langgraph.py`, `mcp/agent_tools.py` · re-exported at the package root: 15 name(s)*

**Role in the workflow.** `BaseAgent` builds a `ModelContext` each turn and calls `next_turn` (or `stream_turn` when a listener is set and the adapter supports it); adapters return an `AgentTurn` or a `ModelTurnResponse`; `FailoverAgentModel` can wrap several adapters.

**Contents**

- **class `ProviderUsage`** *(dataclass)* - Provider-reported token counters (never estimated by the SDK) and request id. · *Instantiated by:* `openai_compatible/chat.py::_provider_usage`
  - fields: `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`, `context_window_tokens`, `request_id`
  - `ProviderUsage.__post_init__() -> None` - Rejects negative counters, naming the field.
- **class `ProviderContinuation`** *(dataclass)* - Opaque provider-owned conversation state the agent stores and forwards without interpreting. · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._continuation_from_response`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.accept_tool_results`
  - fields: `provider`, `state`
- **class `ModelTurnResponse`** *(dataclass)* - An untrusted turn with optional continuation and usage. · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._turn_response_from`
  - fields: `turn`, `continuation`, `usage`
- **class `ModelTextDelta`** *(dataclass)* - One streamed fragment of assistant text. · *Instantiated by:* `openai_compatible/chat.py::_ChatStreamAccumulator.consume`
  - fields: `text`
- **class `ModelToolCallDelta`** *(dataclass)* - One streamed fragment of a tool call (id and name only on a call's first chunk). · *Instantiated by:* `openai_compatible/chat.py::_ChatStreamAccumulator._consume_tool_call_deltas`
  - fields: `index`, `call_id`, `name`, `arguments_delta`
- **class `ModelStreamCompleted`** *(dataclass)* - End-of-stream marker carrying usage. · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - fields: `usage`
- **class `ModelContext`** *(dataclass)* - Everything a model sees in one turn: task, immutable prompt, iteration, bounded project-state view, recent observations, episode summaries, compacted-episode stubs, continuation, projection metadata, model binding and output schema. · *Instantiated by:* `base_agent/agent.py::BaseAgent.run`
  - fields: `task`, `prompt`, `iteration`, `project_state`, `observations`, `episodes`, `continuation`, `projection`, `model_binding`, `output_schema`, `compacted_episodes`
- **class `ProviderToolResult`** *(dataclass)* - Bounded one-time result projection required by a provider continuation protocol. · *Instantiated by:* `base_agent/agent.py::BaseAgent._provider_tool_result`
  - fields: `call_id`, `name`, `status`, `content`, `truncated`
- **class `ProviderToolResultConsumer`** *(Protocol; bases: Protocol)* - Optional adapter capability: accept tool results for a continuation.
  - `ProviderToolResultConsumer.accept_tool_results(continuation: ProviderContinuation, results: Sequence[ProviderToolResult]) -> ProviderContinuation` *(async)* - Bind results to the provider's tool calls and return the updated continuation.
- **class `AgentModel`** *(Protocol; bases: Protocol)* - Protocol for a model adapter.
  - `AgentModel.next_turn(context: ModelContext) -> AgentTurn | ModelTurnResponse` *(async)* - Return the next turn for a context.
- **class `StreamingAgentModel`** *(Protocol; bases: Protocol)* - Optional capability: stream deltas to a listener while still returning the whole turn.
  - `StreamingAgentModel.stream_turn(context: ModelContext, on_delta: ModelStreamListener) -> AgentTurn | ModelTurnResponse` *(async)* - Stream and return the full turn.
- **class `ScriptedModel`** *(class)* - Deterministic test adapter that replays pre-validated turns by iteration number. · *Instantiated by:* `mcp/agent_tools.py::register_agent_tools.run_agent_task`
  - `ScriptedModel.__init__(turns: Sequence[AgentTurn | dict[str, Any]]) -> None` - Validates every scripted turn against the `AgentTurn` union.
  - `ScriptedModel.next_turn(context: ModelContext) -> AgentTurn` *(async)* - Returns the turn for the current iteration or raises `SCRIPTED_MODEL_EXHAUSTED`.
- **class `ModelFailoverAttempt`** *(dataclass)* - Record of one failed attempt: adapter index, error text, whether it was retryable and the retry number. · *Instantiated by:* `agent/model.py::FailoverAgentModel._call_with_failover`
  - fields: `index`, `error`, `retryable`, `retry_number`
- **class `FailoverAgentModel`** *(class)* - Tries adapters in declared order; a `TransientProviderError` is retried on the same adapter with bounded exponential backoff (honouring `retry_after_seconds`), any other error moves to the next adapter at once.
  - `FailoverAgentModel.__init__(models: Sequence[AgentModel], *, max_retries_per_model: int=2, base_backoff_seconds: float=1.0, max_backoff_seconds: float=20.0, on_attempt: Calla...` - Requires a primary adapter and non-negative retry and backoff settings.
  - `FailoverAgentModel.next_turn(context: ModelContext) -> AgentTurn | ModelTurnResponse` *(async)* - Fail over around `next_turn`. · *Called within this file by:* `agent/model.py::FailoverAgentModel.stream_turn.call_one`
  - `FailoverAgentModel.stream_turn(context: ModelContext, on_delta: ModelStreamListener) -> AgentTurn | ModelTurnResponse` *(async)* - Fail over around streaming, calling adapters without streaming through `next_turn`.
    - `FailoverAgentModel.stream_turn.call_one(model: AgentModel) -> AgentTurn | ModelTurnResponse` *(async)* - Calls one adapter in streaming mode when it supports it. · *Called by:* `agent/model.py::FailoverAgentModel.stream_turn`
  - `FailoverAgentModel._call_with_failover(call_model: Callable[[AgentModel], Awaitable[Any]]) -> Any` *(async)* - The retry and fallback loop; when everything fails raises `MODEL_FALLBACK_EXHAUSTED` with the last error and the attempts of this call only (the instance's `attempts` list stays cumulative). · *Called by:* `agent/model.py::FailoverAgentModel.next_turn`, `agent/model.py::FailoverAgentModel.stream_turn`
  - `FailoverAgentModel._record(attempt: ModelFailoverAttempt) -> None` - Appends an attempt and notifies the listener, logging and swallowing listener errors. · *Called within this file by:* `agent/model.py::FailoverAgentModel._call_with_failover`
  - `FailoverAgentModel._backoff_seconds(error: TransientProviderError, retry_number: int) -> float` - Server-declared delay or exponential backoff, capped at the maximum. · *Called by:* `agent/model.py::FailoverAgentModel._call_with_failover`

*Module-level names:* `_AGENT_TURN_ADAPTER`, `_logger`, `ModelStreamEvent`, `ModelStreamListener`

---

### `agent/openai_compatible/__init__.py` - public surface of the OpenAI-compatible adapters

*44 lines · depends on: `agent/openai_compatible/chat.py`, `agent/openai_compatible/embeddings.py`, `agent/openai_compatible/semantic_gap.py`, `agent/openai_compatible/transport.py`, `agent/openai_compatible/vision.py` · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Re-exports the chat model, embedding provider, vision adapter, semantic-gap analyzer, transports and endpoint. No ambient credentials and no default model: the host supplies URL, key and exact model.

---

### `agent/openai_compatible/chat.py` - Chat Completions adapter for the agent model contract

*576 lines · depends on: `agent/model.py`, `agent/openai_compatible/transport.py`, `foundations/contracts.py`, `foundations/errors.py` · used by: `agent/openai_compatible/__init__.py`, `agent/openai_compatible/semantic_gap.py`, `agent/openai_compatible/vision.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `BaseAgent` passes a `ModelContext`; `_chat_payload` builds a three-message request (system, stable task context, volatile per-turn context), the transport posts it off the event loop, and the response is mapped to an `AgentTurn`: tool calls become one dependency-free `tool-batch`, otherwise the content must be a JSON `AgentTurn`.

**Contents**

- **class `OpenAICompatibleAgentModel`** *(class; bases: AgentModel)* - Adapter for an OpenAI-compatible chat endpoint; implements `next_turn`, `stream_turn` and `accept_tool_results`.
  - `OpenAICompatibleAgentModel.__init__(endpoint: OpenAICompatibleEndpoint, *, provider: str, model: str, transport: JsonHttpTransport | None=None, streaming_transport: StreamingJsonHttp...` - Requires non-empty provider and model identifiers; defaults to the urllib and httpx streaming transports.
  - `OpenAICompatibleAgentModel.next_turn(context: ModelContext) -> ModelTurnResponse` *(async)* - Validates the binding, builds the payload and posts it in a worker thread, then maps the response.
  - `OpenAICompatibleAgentModel.stream_turn(context: ModelContext, on_delta: ModelStreamListener) -> ModelTurnResponse` *(async)* - Streams chunks through an accumulator, emitting text and tool-call deltas, then maps the folded response through the same code as `next_turn` and emits the completion with usage.
  - `OpenAICompatibleAgentModel._require_binding(context: ModelContext) -> ModelBinding` - Requires the declared binding and checks it matches this adapter. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - `OpenAICompatibleAgentModel._turn_response_from(response: Mapping[str, Any]) -> ModelTurnResponse` - Builds the turn, continuation and usage from a response. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - `OpenAICompatibleAgentModel.accept_tool_results(continuation: ProviderContinuation, results: Sequence[ProviderToolResult]) -> ProviderContinuation` *(async)* - Requires a continuation from this provider whose tool call ids exactly match the returned results, then stores the results as `tool` messages.
  - `OpenAICompatibleAgentModel._validate_binding(binding: ModelBinding) -> None` - Raises `MODEL_BINDING_MISMATCH` with both pairs when provider or model differ. · *Called within this file by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._require_binding`
  - `OpenAICompatibleAgentModel._chat_payload(context: ModelContext, binding: ModelBinding) -> dict[str, Any]` - Builds the request: a system instruction, a stable message (task id and input, prompt, output schema; byte-identical across turns for provider prompt caching) and a volatile message (project state, iteration, recent observations, episode summaries, compacted-episode stubs and their omitted count), plus any continuation messages, the declared tools and `json_object` response format only when no tools exist. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - `OpenAICompatibleAgentModel._continuation_from_response(response: Mapping[str, Any], turn: AgentTurn) -> ProviderContinuation | None` - Keeps the raw tool calls of a tool turn for the next request. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._turn_response_from`
  - `OpenAICompatibleAgentModel._continuation_messages(continuation: ProviderContinuation) -> list[dict[str, Any]]` - Rebuilds the assistant tool-call message and the tool-result messages from a continuation, validating their shape. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._chat_payload`
- `_safe_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]` - Requires JSON-compatible finite parameters and forbids credential and endpoint keys. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._chat_payload`
- `_chat_tools(context: ModelContext) -> list[dict[str, Any]]` - Converts the prompt's declared tools into function-tool schemas. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._chat_payload`
- `_agent_turn_from_chat_response(response: Mapping[str, Any]) -> AgentTurn` - Turns tool calls into a tool batch (names the offending part of a malformed call) or parses JSON content, then validates against `AgentTurn` with up to five field-path errors. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._turn_response_from`
- `_chat_message(response: Mapping[str, Any]) -> Mapping[str, Any]` - First choice's message object or a typed error. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._continuation_from_response`, `openai_compatible/chat.py::_agent_turn_from_chat_response`, `openai_compatible/chat.py::_chat_content`
- `_chat_content(response: Mapping[str, Any]) -> str` - Non-empty text content or a typed error. · *Called by:* `openai_compatible/chat.py::_agent_turn_from_chat_response`, `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`, `openai_compatible/vision.py::OpenAICompatibleVisionAdapter.extract`
- `_provider_usage(response: Mapping[str, Any]) -> ProviderUsage | None` - Reads prompt, completion, cached and reasoning token counters and the request id. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel._turn_response_from`
- `_optional_nonnegative_int(value: Any) -> int | None` - Accepts None or a non-negative integer. · *Called by:* `openai_compatible/chat.py::_provider_usage`
- **class `_ChatStreamAccumulator`** *(class)* - Folds streamed chunks into the single non-streaming response shape. · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - `_ChatStreamAccumulator.__init__() -> None` - Starts empty.
  - `_ChatStreamAccumulator.consume(chunk: Mapping[str, Any]) -> list[ModelTextDelta | ModelToolCallDelta]` - Collects id, usage, text and tool-call fragments and returns the deltas to emit. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
  - `_ChatStreamAccumulator._consume_tool_call_deltas(raw_tool_calls: list[Any]) -> list[ModelToolCallDelta]` - Concatenates per-index tool-call fragments. · *Called by:* `openai_compatible/chat.py::_ChatStreamAccumulator.consume`
  - `_ChatStreamAccumulator.finalize() -> dict[str, Any]` - Produces the full response with tool calls or concatenated content. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`
- `_emit(on_delta: ModelStreamListener, event: Any) -> None` *(async)* - Calls a sync or async stream listener. · *Called within this file by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`

**Algorithms & invariants.** The blocking call runs in a worker thread, so a watchdog timeout cancels the await but the thread runs until the socket timeout.

*Module-level names:* `_AGENT_TURN_ADAPTER`

---

### `agent/openai_compatible/embeddings.py` - synchronous embedding provider for retrieval indexes

*68 lines · depends on: `agent/openai_compatible/transport.py`, `foundations/errors.py` · used by: `agent/openai_compatible/__init__.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `QdrantRetrievalIndex` calls `embed` for documents and queries.

**Contents**

- **class `OpenAICompatibleEmbeddingProvider`** *(class)* - Synchronous embedding adapter matching the `EmbeddingProvider` shape.
  - `OpenAICompatibleEmbeddingProvider.__init__(endpoint: OpenAICompatibleEndpoint, *, model: str, transport: JsonHttpTransport | None=None) -> None` - Requires a non-empty model identifier.
  - `OpenAICompatibleEmbeddingProvider.embed(text: str) -> list[float]` - Requires non-empty text, posts to `/embeddings` and returns a vector only if the response has exactly one non-empty list of finite numbers.

---

### `agent/openai_compatible/semantic_gap.py` - model-proposed semantic findings for Gate 1

*232 lines · depends on: `agent/openai_compatible/chat.py`, `agent/openai_compatible/transport.py`, `foundations/contracts.py`, `foundations/errors.py`, `specifications/documents.py`, `specifications/gate_models.py` · used by: `agent/openai_compatible/__init__.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `analyze` sends the frozen requirements to a model and returns provenance-stamped proposals; `SpecificationGate.validate` still has to admit each finding mechanically.

**Contents**

- **class `UntrustedSemanticGapFinding`** *(pydantic model; bases: StrictModel)* - Provider output before provenance: id, type, requirement ids, source refs, description, fix and severity.
  - fields: `finding_id`, `type`, `requirement_ids`, `source_refs`, `description`, `suggested_fix`, `severity`
  - `UntrustedSemanticGapFinding.has_bounded_semantic_shape() -> UntrustedSemanticGapFinding` *(validator)* - Validator: only ambiguity, inconsistency or unstated-assumption types; unique non-empty requirement ids; source refs required; an inconsistency needs two requirements; never CRITICAL.
- **class `_UntrustedSemanticGapAnalysis`** *(pydantic model; bases: StrictModel)* - Private response envelope.
  - fields: `schema_version`, `findings`
- **class `SemanticGapAnalysis`** *(pydantic model; bases: StrictModel)* - Findings stamped with provider, model and a receipt digest. · *Instantiated by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`
  - fields: `schema_version`, `findings`
- **class `OpenAICompatibleSemanticGapAnalyzer`** *(class)* - Asks a selected model for bounded findings over a frozen specification.
  - `OpenAICompatibleSemanticGapAnalyzer.__init__(endpoint: OpenAICompatibleEndpoint, *, provider: str, model: str, transport: JsonHttpTransport | None=None) -> None` - Requires non-empty provider and model.
  - `OpenAICompatibleSemanticGapAnalyzer.analyze(specification: UnifiedSpecification) -> SemanticGapAnalysis` *(async)* - Sends a JSON request (system warning not to follow specification text), parses and validates the response (typed errors keep up to 2,000 characters of the raw content) and attaches a receipt digest binding each finding to the exact request view. · *No in-package callers (public API, entry point, or protocol hook).*
- `_semantic_gap_types() -> tuple[GapType, ...]` - The three allowed semantic gap types. · *Called by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`, `openai_compatible/semantic_gap.py::UntrustedSemanticGapFinding.has_bounded_semantic_shape`
- `_semantic_analysis_receipt_seed(*, provider: str, model: str, version: str, requirements: list[dict[str, Any]]) -> str` - Hash of provider, model, specification version and requirement view. · *Called by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`
- `_semantic_finding_receipt(analysis_seed: str, finding: UntrustedSemanticGapFinding) -> str` - Hash of the seed and one finding. · *Called by:* `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze`

---

### `agent/openai_compatible/transport.py` - JSON HTTP transports and endpoint configuration

*379 lines · depends on: `foundations/errors.py` · used by: `agent/openai_compatible/__init__.py`, `agent/openai_compatible/chat.py`, `agent/openai_compatible/embeddings.py`, `agent/openai_compatible/semantic_gap.py`, `agent/openai_compatible/vision.py` · re-exported at the package root: 6 name(s)*

**Role in the workflow.** Chat, embedding, vision and semantic adapters call `post_json`; streaming calls `stream_json`. HTTP failures are classified by `_http_status_error` into transient (429, 500, 502, 503, 504, retryable by the failover model) or terminal, with a likely-cause hint per status.

**Contents**

- **class `JsonHttpTransport`** *(Protocol; bases: Protocol)* - Protocol for a blocking JSON POST transport a host may replace.
  - `JsonHttpTransport.post_json(url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> Mapping[str, Any]` - POST a payload and return the decoded JSON object.
- **class `StreamingJsonHttpTransport`** *(Protocol; bases: Protocol)* - Protocol for an async transport yielding one decoded object per server-sent chunk.
  - `StreamingJsonHttpTransport.stream_json(url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> AsyncIterator[dict[str, Any]]` - Stream decoded chunks.
- **class `_NoRedirectHandler`** *(class; bases: HTTPRedirectHandler)* - Treats redirects as failures so the bearer token never follows a redirect. · *Instantiated by:* `openai_compatible/transport.py::UrlLibJsonTransport.post_json`
  - `_NoRedirectHandler.redirect_request(req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None` - Returns None, refusing the redirect.
- **class `UrlLibJsonTransport`** *(class)* - Dependency-free default transport (a new connection per call). · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.__init__`, `openai_compatible/embeddings.py::OpenAICompatibleEmbeddingProvider.__init__`, `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.__init__`, `openai_compatible/vision.py::OpenAICompatibleVisionAdapter.__init__`
  - `UrlLibJsonTransport.post_json(url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> Mapping[str, Any]` - POSTs and maps HTTP status, an unreachable endpoint (message names host, exception, root cause and a likely cause such as DNS, refusal, TLS or a dropped connection), a timeout and malformed JSON to typed errors.
- **class `HttpxJsonTransport`** *(class)* - Blocking transport over one persistent, connection-pooled `httpx.Client`.
  - `HttpxJsonTransport.__init__(*, client: httpx.Client | None=None) -> None` - Uses a given client or creates and owns one.
  - `HttpxJsonTransport.post_json(url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> Mapping[str, Any]` - POSTs and maps status codes at or above 300, timeouts, unreachable endpoints (with host, cause and likely cause) and malformed JSON.
  - `HttpxJsonTransport.close() -> None` - Closes the client only if this instance created it.
- **class `HttpxStreamingJsonTransport`** *(class)* - Opt-in async streaming over a new `httpx.AsyncClient` per call. · *Instantiated by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.__init__`
  - `HttpxStreamingJsonTransport.stream_json(url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> AsyncIterator[dict[str, Any]]` *(async)* - Streams `data:` lines as JSON objects, skipping `[DONE]`, blank and malformed lines; maps timeouts and unreachable endpoints like the blocking transports.
- `_provider_target(url: str) -> str` - `host` or `host:port` of a URL (never the path or query), for error messages. · *Called by:* `openai_compatible/transport.py::_timeout_message`, `openai_compatible/transport.py::_transport_failure_detail`
- `_exception_chain(error: BaseException) -> list[BaseException]` - The exception, its `reason` (urllib) or its cause or context, and so on down to the root error, without repeats. · *Called by:* `openai_compatible/transport.py::_transport_failure_detail`
- `_transport_hint(chain: list[BaseException]) -> str` - Likely cause for the first recognised error in a chain: unresolved host name, refused connection, TLS failure, dropped connection or connect timeout; otherwise a generic network-path hint. · *Called by:* `openai_compatible/transport.py::_transport_failure_detail`
- `_transport_failure_detail(url: str, error: BaseException) -> str` - `host (ExceptionType: text; root cause ...); likely cause: ...` for an unreachable provider. · *Called by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`
- `_timeout_message(url: str, timeout_seconds: float) -> str` - Message for a provider that did not answer in time, naming the host and the timeout. · *Called by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`
- `_http_status_error(status_code: int, *, retry_after_seconds: float | None) -> AgentSdkError` - Builds `OpenAI-compatible provider returned HTTP <code> - likely cause: <hint>.` as a transient or terminal error; hints cover 301, 302, 307, 308, 400, 401, 403, 404, 408, 413, 422, 429, 500, 502, 503 and 504. · *Called by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`
- `_parse_retry_after(raw_value: str | None) -> float | None` - Parses a numeric `Retry-After` header; HTTP-date forms are ignored. · *Called by:* `openai_compatible/transport.py::HttpxJsonTransport.post_json`, `openai_compatible/transport.py::HttpxStreamingJsonTransport.stream_json`, `openai_compatible/transport.py::UrlLibJsonTransport.post_json`
- **class `OpenAICompatibleEndpoint`** *(dataclass)* - Host-owned base URL, API key (hidden from repr), timeout and an explicit insecure-HTTP opt-in.
  - fields: `base_url`, `api_key`, `timeout_seconds`, `allow_insecure_http`
  - `OpenAICompatibleEndpoint.__post_init__() -> None` - Requires an HTTP(S) URL, HTTPS unless allowed, a non-empty key and a positive timeout.
  - `OpenAICompatibleEndpoint.url_for(suffix: str) -> str` - Joins the base URL and a path suffix. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`, `openai_compatible/embeddings.py::OpenAICompatibleEmbeddingProvider.embed`, `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze` (+1 more)
  - `OpenAICompatibleEndpoint.headers() -> dict[str, str]` *(property)* - Property: bearer authorisation and JSON content type. · *Called by:* `openai_compatible/chat.py::OpenAICompatibleAgentModel.next_turn`, `openai_compatible/chat.py::OpenAICompatibleAgentModel.stream_turn`, `openai_compatible/embeddings.py::OpenAICompatibleEmbeddingProvider.embed`, `openai_compatible/semantic_gap.py::OpenAICompatibleSemanticGapAnalyzer.analyze` (+7 more)

*Module-level names:* `_RETRYABLE_HTTP_STATUS_CODES`, `_REDIRECT_HINT`, `_HTTP_STATUS_HINTS`

---

### `agent/openai_compatible/vision.py` - image proposals through a verified byte loader

*162 lines · depends on: `agent/openai_compatible/chat.py`, `agent/openai_compatible/transport.py`, `foundations/errors.py`, `specifications/documents.py`, `specifications/vision.py` · used by: `agent/openai_compatible/__init__.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `SpecificationPreprocessor.resolve_images` calls `OpenAICompatibleVisionAdapter.extract` per image node; the loader returns exactly the bytes whose hash is frozen in the node.

**Contents**

- **class `SourceVerifiedImageLoader`** *(class)* - Loads a standalone PNG or JPEG only if it is under the specification root, within a size limit and still hashes to the frozen source hash.
  - `SourceVerifiedImageLoader.__init__(specification_root: str, *, max_image_bytes: int=10000000) -> None` - Requires a positive size limit and an existing root directory.
  - `SourceVerifiedImageLoader.__call__(node: DocumentNode) -> bytes` - Checks node kind, format, path containment, existence, size and hash, then returns the bytes.
- **class `OpenAICompatibleVisionAdapter`** *(class)* - Sends an image to a vision-capable chat model and returns a typed `VisionProposal`.
  - `OpenAICompatibleVisionAdapter.__init__(endpoint: OpenAICompatibleEndpoint, *, model: str, image_loader: ImageBytesLoader, transport: JsonHttpTransport | None=None, max_image_bytes: int=...` - Requires a model identifier and a positive size limit; accepts extra request parameters.
  - `OpenAICompatibleVisionAdapter.extract(node: DocumentNode) -> VisionProposal` *(async)* - Loads and size-checks the bytes, posts a data-URL request asking for JSON confidence, structure and errors, and validates the reply (typed errors name field paths and keep the raw content).
- `_image_data_url(format_: DocumentFormat, image_bytes: bytes) -> str` - Base64 data URL for a PNG or JPEG. · *Called by:* `openai_compatible/vision.py::OpenAICompatibleVisionAdapter.extract`

*Module-level names:* `ImageBytesLoader`

---

### `agent/orchestrator/__init__.py` - public surface of the orchestrator package

*31 lines · depends on: `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `agent/orchestrator/state_store.py` · used by: `mcp/_shared.py`, `mcp/orchestration_tools.py` · not re-exported at the package root*

**Role in the workflow.** Re-exports the policy and record models, the `Orchestrator`, its binding types and the state store.

---

### `agent/orchestrator/models.py` - user-owned orchestration policy, request and record contracts

*170 lines · depends on: `foundations/contracts.py`, `integrations/jev/architecture.py`, `state/orchestration_models.py`, `state/planning.py`, `state/shared_state.py`, `tools/policy.py` · used by: `agent/orchestrator/__init__.py`, `agent/orchestrator/orchestrator.py`, `agent/orchestrator/state_store.py` · re-exported at the package root: 7 name(s)*

**Role in the workflow.** A host defines an `OrchestrationPolicy` once; each request supplies a plan and a skill selection; `OrchestrationRecord` persists the decision and dispatch state.

**Contents**

- **class `OrchestrationStatus`** *(enum; bases: StrEnum)* - prepared, awaiting-plan-approval, approved, dispatched, executed or cancelled.
  - members: `PREPARED`, `AWAITING_PLAN_APPROVAL`, `APPROVED`, `DISPATCHED`, `EXECUTED`, `CANCELLED`
- **class `UserModelSelection`** *(pydantic model; bases: StrictModel)* - A named, user-approved model binding usable for listed tiers.
  - fields: `model_key`, `binding`, `allowed_tiers`
  - `UserModelSelection.tiers_are_unique() -> UserModelSelection` *(validator)* - Validator: each tier appears once.
- **class `AgentExecutionProfile`** *(pydantic model; bases: StrictModel)* - A worker envelope for a stage and role: allowed and required skills, allowed model keys and tools, a capability grant and an instance cap; identifiers only, no executable objects.
  - fields: `profile_id`, `stage`, `role`, `allowed_skill_ids`, `required_skill_ids`, `allowed_model_keys`, `allowed_tool_names`, `capability_grant`, `max_instances`
  - `AgentExecutionProfile.profile_authority_is_consistent() -> AgentExecutionProfile` *(validator)* - Validator: grant role equals profile role, lists are unique and required skills are allowed.
- **class `OrchestrationPolicy`** *(pydantic model; bases: StrictModel)* - Routing rules, skills, models, profiles and limits (total and parallel agents, repair attempts, multi-agent enabled).
  - fields: `policy_id`, `routing_rules`, `skills`, `models`, `profiles`, `max_total_agents`, `max_parallel_agents`, `max_repair_attempts`, `multi_agent_enabled`
  - `OrchestrationPolicy.configuration_ids_are_consistent() -> OrchestrationPolicy` *(validator)* - Validator: unique ids; profiles reference known skills and models; parallel cap not above the total cap.
- **class `OrchestrationRequest`** *(pydantic model; bases: StrictModel)* - Request id, stage, snapshot, gap metadata, plan, selected skills and per-task profile overrides.
  - fields: `request_id`, `stage`, `snapshot`, `gap_metadata`, `plan`, `selected_skill_ids`, `profile_id_by_task_id`
  - `OrchestrationRequest.selected_skill_ids_are_unique() -> OrchestrationRequest` *(validator)* - Validator: unique skills; overrides reference plan tasks.
- **class `WorkerAssignment`** *(pydantic model; bases: StrictModel)* - Auditable authorisation-limited assignment of a node to a profile, model, skills, tools and capabilities. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`
  - fields: `node_id`, `task_id`, `profile_id`, `agent_identity`, `model_key`, `skill_ids`, `allowed_tool_names`, `capability_ids`
- **class `OrchestrationRecord`** *(pydantic model; bases: StrictModel)* - Non-secret record: request, policy id, deterministic and selected architecture, advice, execution plan, assignments, status, controller and run ids. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
  - fields: `schema_version`, `orchestration_id`, `request`, `policy_id`, `deterministic_architecture`, `architecture`, `routing_advice`, `execution_plan`, `assignments`, `status`, `controller_id`, `graph_run_id`

---

### `agent/orchestrator/orchestrator.py` - deterministic composition of policy, plan, controller and graph execution

*637 lines · depends on: `agent/graph_agent_executor.py`, `agent/orchestrator/models.py`, `agent/orchestrator/state_store.py`, `agent/runtime.py`, `foundations/contracts.py`, `integrations/jev/architecture.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `state/controller_runtime.py`, `state/orchestration_models.py`, `state/planning.py`, `state/shared_state.py`, `tools/registry.py` · used by: `agent/orchestrator/__init__.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** prepare (validate the plan, route deterministically, optionally take a Jev lift, compile and assign workers, persist) then submit_for_approval (create the controller and present the plan), approve (human decision), dispatch_and_execute (start the graph, build and validate host bindings, execute waves under the parallelism cap). It never asks a model to plan or to approve.

**Contents**

- **class `GraphAgentBindingContext`** *(dataclass)* - Host-only context given to the binding factory: assignment, execution task, selected skills, model selection, profile and snapshot. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator._build_bindings`
  - fields: `assignment`, `execution_task`, `selected_skills`, `model`, `profile`, `snapshot`
- **class `Orchestrator`** *(class)* - Compiles user configuration into a governed controller and graph execution; routing may only be raised, never lowered, by advice. · *Instantiated by:* `mcp/orchestration_tools.py::register_orchestration_tools.prepare_orchestration`
  - `Orchestrator.__init__(run_root: Path, policy: OrchestrationPolicy, *, controller_runtime: ControllerRuntime | None=None, jev_router: JevArchitectureRouter | None=None, ...` - Persists the policy, creates telemetry and a controller runtime if not given, and validates that every profile tool is registered and granted its capability.
  - `Orchestrator.resume(run_root: Path, orchestration_id: str, *, controller_runtime: ControllerRuntime | None=None, telemetry: TelemetryStore | None=None, tool_registry:...` *(classmethod)* - Rebuilds a policy shell from a persisted orchestration (no Jev router or host bindings). · *Called by:* `mcp/_shared.py::McpContext.orchestration_for`
  - `Orchestrator.prepare(request: OrchestrationRequest) -> OrchestrationRecord` *(async)* - Validates the plan and the skill selection, routes deterministically (policy may forbid multi-agent), applies Jev advice that can only lift single to multi, compiles the execution plan, assigns workers within the total-agent cap, rejects unused selected skills and persists a PREPARED record. · *Called by:* `mcp/orchestration_tools.py::register_orchestration_tools.prepare_orchestration`
  - `Orchestrator.submit_for_approval(orchestration_id: str) -> OrchestrationRecord` - Creates the controller with a profile bound to the snapshot, applies the architecture lift if needed, submits the execution plan and records AWAITING_PLAN_APPROVAL. · *Called by:* `mcp/orchestration_tools.py::register_orchestration_tools.submit_orchestration_for_approval`
  - `Orchestrator.approve(orchestration_id: str, approved: bool, reason: str | None=None) -> OrchestrationRecord` - Forwards a human decision to the controller; approval gives APPROVED, rejection returns to PREPARED. · *Called by:* `mcp/orchestration_tools.py::register_orchestration_tools.approve_orchestration`
  - `Orchestrator.dispatch_and_execute(orchestration_id: str, services: AgentRuntimeServices, binding_factory: GraphAgentBindingFactory) -> OrchestrationRecord` *(async)* - Requires APPROVED status, a dispatch-ready controller and services on the same run root; builds and validates the host bindings first (so an invalid binding leaves the orchestration APPROVED and retryable), then dispatches the graph and executes all waves through `GraphAgentExecutor` with the policy's parallelism cap and records EXECUTED. · *No in-package callers (public API, entry point, or protocol hook).*
  - `Orchestrator.cancel(orchestration_id: str, reason: str) -> OrchestrationRecord` - Cancels the controller and its run if bound and records CANCELLED.
  - `Orchestrator.get(orchestration_id: str) -> OrchestrationRecord` - Loads a record. · *Called within this file by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`, `orchestrator/orchestrator.py::Orchestrator._profile_for`, `orchestrator/orchestrator.py::Orchestrator.approve`, `orchestrator/orchestrator.py::Orchestrator.cancel` (+2 more)
  - `Orchestrator.controller_runtime() -> ControllerRuntime` - Exposes the controller facade for explicit completion, gates and repair after execution. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `orchestrator/orchestrator.py::Orchestrator.resume`, `mcp/_shared.py::McpContext.orchestration_for`, `mcp/controller_tools.py::register_controller_tools.approve_controller_plan` (+14 more)
  - `Orchestrator._validate_request_selection(request: OrchestrationRequest) -> None` - Rejects unknown skills and re-validates the plan. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
  - `Orchestrator._validate_policy_tool_authority() -> None` - Requires every profile tool to be registered with a capability inside the profile's grant. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.__init__`
  - `Orchestrator._assign_workers(request: OrchestrationRequest, execution_plan: Plan) -> list[WorkerAssignment]` - For each execution task chooses a profile, a model by tier, the allowed skills and an identity, counts profile instances against `max_instances` and checks collapsed task ids exist in the original plan. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
  - `Orchestrator._profile_for(task: PlanTask, source_task_ids: list[str], request: OrchestrationRequest) -> AgentExecutionProfile` - Uses the single agreed per-task override (stage, skills and existence checked) or the first stage profile, ordered by id, that has a model for the tier and whose required skills are selected. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`
  - `Orchestrator._model_for(tier: ModelTier, profile: AgentExecutionProfile) -> UserModelSelection` - First model, ordered by key, allowed by the profile and the tier. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`, `orchestrator/orchestrator.py::Orchestrator._profile_for`
  - `Orchestrator._build_bindings(record: OrchestrationRecord, factory: GraphAgentBindingFactory) -> dict[str, GraphAgentBinding]` - Calls the host factory for each assignment, validates the binding and wraps its task adapter so the approved skills are exact. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`
  - `Orchestrator._validate_binding(binding: GraphAgentBinding, context: GraphAgentBindingContext) -> None` *(staticmethod)* - Binding node, identity and model must equal the assignment's and the binding's tools must be within the profile's allowed tools. · *Called within this file by:* `orchestrator/orchestrator.py::Orchestrator._build_bindings`
  - `Orchestrator._next_id() -> str` - Next unused `orchestration-N`. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`, `memory/context_projection.py::FileToolResultJournal.__init__`, `memory/context_projection.py::FileToolResultJournal.record`, `memory/context_projection.py::InMemoryToolResultJournal.__init__` (+7 more)
  - `Orchestrator._emit(record: OrchestrationRecord, event_type: str, status: str, payload: dict[str, Any]) -> None` - Emits a deterministic orchestrator telemetry event keyed by orchestration id. · *Called within this file by:* `orchestrator/orchestrator.py::Orchestrator.approve`, `orchestrator/orchestrator.py::Orchestrator.cancel`, `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`, `orchestrator/orchestrator.py::Orchestrator.prepare` (+1 more)
- `_profile_skills(profile: AgentExecutionProfile, selected_skill_ids: list[str]) -> list[str]` - Allowed skills intersected with the selected skills. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`
- `_worker_identity(task: PlanTask, source_task_ids: list[str], profile: AgentExecutionProfile) -> str` - `role:task` or `role:orchestrated:task` for a collapsed task. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._assign_workers`
- `_routing_state(request: OrchestrationRequest) -> dict[str, Any]` - Bounded non-secret planning metadata for an advisory. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
- `_execution_plan(plan: Plan, architecture: WorkflowArchitecture) -> Plan` - Multi-agent keeps the plan; single-agent collapses all tasks into one task (highest tier, merged instructions and criteria, union of authorised artifacts). · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`
- `_with_selected_skills(binding: GraphAgentBinding, selected_skills: tuple[SkillContext, ...]) -> GraphAgentBinding` - Wraps a binding's task adapter to set the task's skills to the approved selection. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._build_bindings`
  - `_with_selected_skills.scoped_task(node, context)` - Applies the approved skills to the adapted task. · *Called by:* `orchestrator/orchestrator.py::_with_selected_skills`

*Module-level names:* `GraphAgentBindingFactory`

---

### `agent/orchestrator/state_store.py` - atomic local persistence of orchestration records and policies

*57 lines · depends on: `agent/orchestrator/models.py`, `foundations/atomic_io.py` · used by: `agent/orchestrator/__init__.py`, `agent/orchestrator/orchestrator.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `Orchestrator` saves records at each lifecycle step and the policy at construction.

**Contents**

- **class `OrchestrationStateStore`** *(class)* - File store under `<run_root>/.agent-orchestrations/`; credentials and callbacks are never stored. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `orchestrator/orchestrator.py::Orchestrator.resume`
  - `OrchestrationStateStore.__init__(root: Path) -> None` - Creates the record and policy directories.
  - `OrchestrationStateStore.save(record: OrchestrationRecord) -> None` - Atomically writes a record.
  - `OrchestrationStateStore.load(orchestration_id: str) -> OrchestrationRecord` - Reads a record or raises naming the unknown id.
  - `OrchestrationStateStore.exists(orchestration_id: str) -> bool` - True if the record file exists.
  - `OrchestrationStateStore.save_policy(policy: OrchestrationPolicy) -> None` - Writes a policy once; a same-id policy with different content is rejected. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.__init__`
  - `OrchestrationStateStore.load_policy(policy_id: str) -> OrchestrationPolicy` - Reads a policy or raises naming the unknown id. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.resume`

---

### `agent/runtime.py` - composition root for the durable stores around an agent

*100 lines · depends on: `agent/base_agent/__init__.py`, `agent/model.py`, `agent/verification.py`, `foundations/contracts.py`, `memory/context_projection.py`, `memory/episode_store.py`, `observability/audit_log.py`, `observability/profiler.py`, `observability/telemetry_store.py`, `state/project_state_models.py`, `state/project_state_store.py`, `tools/tools.py` · used by: `agent/graph_agent_executor.py`, `agent/orchestrator/orchestrator.py`, `integrations/langgraph.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `AgentRuntimeServices.open(run_root)` creates the stores once; `create_agent` builds a `BaseAgent` bound to them. The model and tool executor stay host choices.

**Contents**

- **class `AgentRuntimeServices`** *(dataclass)* - Bundle of result journal, project-state store, telemetry store and audit store under one run root.
  - fields: `run_root`, `result_journal`, `project_state_store`, `telemetry`, `audit_logs`
  - `AgentRuntimeServices.open(run_root: Path) -> AgentRuntimeServices` *(classmethod)* - Creates the directory and opens the four stores. · *Called by:* `openai_compatible/transport.py::UrlLibJsonTransport.post_json`, `memory/context_projection.py::FileToolResultJournal.record`, `observability/audit_log.py::AuditTranscriptStore._handle_for`, `observability/audit_log.py::AuditTranscriptStore._tail` (+18 more)
  - `AgentRuntimeServices.create_agent(definition: AgentDefinition, model: AgentModel, *, tool_executor: ToolExecutor | None=None, verification_gates: VerificationGateRegistry | None=No...` - Builds a `BaseAgent` using those stores and a fresh profiler per agent; every other dependency is passed through explicitly. · *Called by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `integrations/langgraph.py::LangGraphSdkNode.__call__`

---

### `agent/specialists.py` - declarative agent definitions for the framework's roles

*178 lines · depends on: `foundations/contracts.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 3 name(s)*

**Role in the workflow.** Hosts call these factories to obtain an `AgentDefinition` (tools, schemas, iteration budget, gate) to pass to `BaseAgent` or a graph binding.

**Contents**

- `_object_schema(required: list[str], properties: dict[str, object]) -> dict[str, object]` - Builds a closed JSON-schema object with required fields and no additional properties. · *Called by:* `agent/specialists.py::placeholder_specialist_definition`, `agent/specialists.py::planning_agent_definition`, `agent/specialists.py::rtl_worker_definition`
- `planning_agent_definition(model_binding: ModelBinding) -> AgentDefinition` - Planner role: one read-only `read_spec` tool, output `{status: complete, plan}`, six iterations, no gate (plan validity stays deterministic). · *No in-package callers (public API, entry point, or protocol hook).*
- `rtl_worker_definition(model_binding: ModelBinding) -> AgentDefinition` - RTL worker role: `read_spec`, `read_artifact`, `write_draft`, `run_verilator`, `run_yosys` (both `requires_manifest`) and `diff_declared_artifacts`, ten iterations, gate `validate-rtl-task-result`. · *No in-package callers (public API, entry point, or protocol hook).*
- `placeholder_specialist_definition(role: str, model_binding: ModelBinding) -> AgentDefinition` - A role boundary with no tools and one iteration that returns blocked when capabilities are absent. · *No in-package callers (public API, entry point, or protocol hook).*

---

### `agent/verification.py` - host-registered, bounded output-acceptance gates

*223 lines · depends on: `foundations/contracts.py`, `foundations/errors.py` · used by: `agent/base_agent/agent.py`, `agent/graph_agent_executor.py`, `agent/runtime.py`, `integrations/jev/advisory.py`, `integrations/langgraph.py` · re-exported at the package root: 5 name(s)*

**Role in the workflow.** A definition names a `verification_gate_id`; at a final turn `BaseAgent._accept_final_turn` asks `VerificationGateRegistry.evaluate`; a failed decision ends the run FAILED.

**Contents**

- **class `VerificationDecision`** *(dataclass)* - The only acceptance result: passed flag and reason. · *Instantiated by:* `agent/verification.py::StatusIsCompleteGate.verify`, `agent/verification.py::ValidateRtlTaskResultGate.verify`, `agent/verification.py::_normalize_decision`, `jev/advisory.py::JevAdvisoryVerificationGate._apply_policy` (+2 more)
  - fields: `passed`, `reason`
- **class `VerificationContext`** *(dataclass)* - Read-only input to a gate: output, definition and task. · *Instantiated by:* `agent/verification.py::VerificationGateRegistry.evaluate`
  - fields: `output`, `definition`, `task`
- **class `VerificationGate`** *(Protocol; bases: Protocol)* - Protocol for a gate (sync or async).
  - `VerificationGate.verify(context: VerificationContext) -> VerificationReturn | Awaitable[VerificationReturn]` - Return a decision, bool or (bool, reason).
- **class `CallableVerificationGate`** *(dataclass)* - Adapts a callback plus fixed local arguments into a gate. · *Instantiated by:* `agent/verification.py::VerificationGateRegistry.register_callable`
  - fields: `callback`, `args`, `kwargs`
  - `CallableVerificationGate.verify(context: VerificationContext) -> VerificationReturn` *(async)* - Calls the callback (awaiting it if needed).
- **class `StatusIsCompleteGate`** *(class)* - Built-in `status-is-complete` gate: the output must be an object whose policy status field equals `complete`. · *Instantiated by:* `agent/verification.py::VerificationGateRegistry.__init__`
  - `StatusIsCompleteGate.verify(context: VerificationContext) -> VerificationDecision` - Names the status field in its rejection reason.
- **class `ValidateRtlTaskResultGate`** *(class)* - Built-in `validate-rtl-task-result` gate for the RTL worker. · *Instantiated by:* `agent/verification.py::VerificationGateRegistry.__init__`
  - `ValidateRtlTaskResultGate.verify(context: VerificationContext) -> VerificationDecision` - Requires an object with status `complete`, a `sha256:` draft artifact id, a checks list and a `locked_interface_hash` equal to the hash of the task's locked interface.
- **class `VerificationGateRegistry`** *(class)* - Named gates; the definition selects a name and only the host can register an implementation. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`
  - `VerificationGateRegistry.__init__() -> None` - Pre-registers the two built-in gates.
  - `VerificationGateRegistry.register(gate_id: str, gate: VerificationGate, *, replace: bool=False) -> None` - Adds a gate under a validated id; duplicates need `replace=True`. · *Called by:* `agent/verification.py::VerificationGateRegistry.register_callable`
  - `VerificationGateRegistry.register_callable(gate_id: str, callback: VerificationCallback, *args: Any, replace: bool=False, **kwargs: Any) -> None` - Registers a sync or async callback with fixed arguments. · *No in-package callers (public API, entry point, or protocol hook).*
  - `VerificationGateRegistry.unregister(gate_id: str) -> None` - Removes a custom gate; built-ins and unknown ids raise. · *No in-package callers (public API, entry point, or protocol hook).*
  - `VerificationGateRegistry.resolve(gate_id: str | None) -> VerificationGate | None` - Returns the gate, None for no gate, or raises `VERIFICATION_GATE_UNKNOWN`. · *Called within this file by:* `agent/verification.py::VerificationGateRegistry.evaluate`
  - `VerificationGateRegistry.evaluate(gate_id: str | None, output: Any, definition: AgentDefinition, task: ScopedAgentTask) -> VerificationDecision | None` *(async)* - Runs the gate and normalises its return into a decision.
- `_normalize_decision(result: VerificationReturn) -> VerificationDecision` - Accepts a decision, a bool or a `(bool, reason)` tuple; anything else raises `TypeError`. · *Called by:* `agent/verification.py::VerificationGateRegistry.evaluate`
- `_validate_gate_id(gate_id: str) -> None` - Rejects empty ids and ids containing whitespace. · *Called by:* `agent/verification.py::VerificationGateRegistry.register`

*Module-level names:* `VerificationReturn`, `VerificationCallback`

