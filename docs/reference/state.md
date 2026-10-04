# `state/` - durable run state: plans, the typed graph, the controller and project state

Everything that must survive a restart and be explainable afterwards. There are four independent state machines, each with its own store: (1) the **plan** (`planning.py`) is validated deterministically; (2) the **graph run** (`graph.py` + `harness_coordinator.py` + `run_state_store.py`) schedules approved plan tasks in waves and is the only carrier of lateral data between agents; (3) the **controller** (`orchestration.py` + `controller_runtime.py`) owns plan approval, dispatch, bounded repair and escalation; (4) the **project state** (`project_state_*.py`) is the bounded working memory a `BaseAgent` reads every turn. Persisted records are integrity-hashed: run and controller records use a snapshot plus an append-only sidecar whose prefix hash the snapshot names, and project state uses one hash-chained event file per revision. The stores still assume **one writer at a time per run root** for record contents: the harness coordinator and the project-state store detect that another writer changed a file and refresh or refuse instead of overwriting it, but two writers saving at the same instant can still interleave. Several hazards are closed: ids (`run-N`, `controller-N`, `orchestration-N`) are reserved atomically across processes through exclusive claim files, every caller-supplied id is validated before it becomes a path, approvals are persisted per run, and one project-state store instance is safe across threads.

| File | Lines | Role |
|---|---:|---|
| [`state/__init__.py`](#state__init__py---package-marker-for-durable-run-state) | 3 | package marker for durable run state |
| [`state/controller_runtime.py`](#statecontroller_runtimepy---durable-facade-over-the-controller-the-graph-run-project-state-and-telemetry) | 650 | durable facade over the controller, the graph run, project state and telemetry |
| [`state/coordination_records.py`](#statecoordination_recordspy---durable-run-record-and-its-integrity-hash) | 45 | durable run record and its integrity hash |
| [`state/elastic.py`](#stateelasticpy---typed-elastic-node-requests-specs-spawn-records-and-the-admission-checks) | 275 | typed elastic-node requests, specs, spawn records and the admission checks |
| [`state/graph.py`](#stategraphpy---deterministic-wave-scheduler-and-authoritative-typed-run-state) | 1331 | deterministic wave scheduler and authoritative typed run state |
| [`state/graph_models.py`](#stategraph_modelspy---typed-node-edge-event-and-shared-state-contracts-for-the-run-graph) | 220 | typed node, edge, event and shared-state contracts for the run graph |
| [`state/harness_coordinator.py`](#stateharness_coordinatorpy---run-lifecycle-plan-validation-graph-start-wave-execution-approvals-cancel-and-recovery) | 347 | run lifecycle: plan validation, graph start, wave execution, approvals, cancel and recovery |
| [`state/orchestration.py`](#stateorchestrationpy---controller-state-machine-and-its-crash-safe-store) | 380 | controller state machine and its crash-safe store |
| [`state/orchestration_models.py`](#stateorchestration_modelspy---controller-phase-routing-rule-and-record-contracts) | 93 | controller phase, routing-rule and record contracts |
| [`state/planning.py`](#stateplanningpy---typed-plan-contracts-and-the-deterministic-plan-validator) | 487 | typed plan contracts and the deterministic plan validator |
| [`state/project_state_engine.py`](#stateproject_state_enginepy---mechanical-reducer-and-token-bounded-projector-over-projectstate) | 473 | mechanical reducer and token-bounded projector over `ProjectState` |
| [`state/project_state_models.py`](#stateproject_state_modelspy---bounded-hash-sealed-working-memory-contracts) | 367 | bounded, hash-sealed working-memory contracts |
| [`state/project_state_store.py`](#stateproject_state_storepy---in-memory-and-file-backed-project-state-stores-with-a-hash-chained-audit-trail) | 249 | in-memory and file-backed project-state stores with a hash-chained audit trail |
| [`state/run_state_store.py`](#staterun_state_storepy---crash-safe-run-persistence-as-a-fixed-state-snapshot-plus-a-growing-state-sidecar) | 405 | crash-safe run persistence as a fixed-state snapshot plus a growing-state sidecar |
| [`state/shared_state.py`](#stateshared_statepy---typed-lateral-state-payloads-exact-routing-references-and-provenance-records) | 345 | typed lateral-state payloads, exact routing references and provenance records |
| [`state/stage_gates.py`](#statestage_gatespy---deterministic-stage-completeness-gate) | 94 | deterministic stage-completeness gate |

---

### `state/__init__.py` - package marker for durable run state

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only; the public names are re-exported from the package root.

---

### `state/controller_runtime.py` - durable facade over the controller, the graph run, project state and telemetry

*650 lines · depends on: `observability/metrics.py`, `observability/telemetry_models.py`, `observability/telemetry_store.py`, `state/coordination_records.py`, `state/graph_models.py`, `state/harness_coordinator.py`, `state/orchestration.py`, `state/orchestration_models.py`, `state/planning.py`, `state/project_state_models.py`, `state/project_state_store.py`, `state/shared_state.py`, `state/stage_gates.py` · used by: `agent/orchestrator/orchestrator.py`, `mcp/_shared.py`, `mcp/server.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** The downward control path is create, submit plan, approve, dispatch, execute graph; the upward path is node results, stage failure (bounded repair, then escalation), provenance and completeness gates, and the elastic capacity decisions (grant or decline) that release a run held at its caps. Used by the `Orchestrator` and by the MCP controller tools. It owns no lateral-state store of its own.

**Contents**

- **class `ControllerRuntime`** *(class)* - Joins `ControllerStateMachine`, `HarnessCoordinator`, `FileProjectStateStore` and optional telemetry. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.__init__`, `mcp/server.py::create_mcp_server`
  - `ControllerRuntime.__init__(run_root: Path, *, telemetry: TelemetryStore | None=None, coordinator: HarnessCoordinator | None=None, project_state_store: FileProjectStateStore ...` - Creates the controller store, an in-memory controller cache and a process-local counter starting at 1; uses the given harness coordinator and project-state store or creates its own (the MCP server passes shared ones so there is one writer per run root); registers standard metric definitions when telemetry is given.
  - `ControllerRuntime.create_controller(snapshot: SharedSubstrateSnapshot, profile: SkillToolProfile, routing_rules: ComplexityRoutingRules, gap_metadata: GapMetadata, *, max_repair_atte...` - Allocates the next unused `controller-N` (skipping ids in memory or on disk), ensures a project state keyed by the snapshot id with schema `<stage>-v1`, builds and persists the machine, emits `controller.created`.
  - `ControllerRuntime.get_controller(controller_id: str) -> ControllerRecord` - Current controller record, lazily loaded from disk. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`, `mcp/controller_tools.py::register_controller_tools.get_controller_state`
  - `ControllerRuntime.submit_plan(controller_id: str, plan: Plan) -> ControllerRecord` - Validates and stores a plan, persists, emits `controller.plan-presented`.
  - `ControllerRuntime.apply_advisory_architecture(controller_id: str, architecture: str, reason: str) -> ControllerRecord` - Records a bounded single-to-multi lift before approval.
  - `ControllerRuntime.approve_plan(controller_id: str, approved: bool, reason: str | None=None) -> ControllerRecord` - Records approval or rejection and persists.
  - `ControllerRuntime.dispatch(controller_id: str) -> tuple[ControllerRecord, RunRecord]` - Moves to EXECUTING, starts the graph run with the controller's snapshot as substrate, binds its run id, persists and emits `controller.dispatched`. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`, `mcp/controller_tools.py::register_controller_tools.dispatch_controller`
  - `ControllerRuntime.publish_discovery(controller_id: str, discovery: ExploratoryDiscovery) -> RunRecord` - Requires a dispatched run; publishes a discovery into the graph.
  - `ControllerRuntime.write_shared_value(controller_id: str, state_write: SharedStateWrite) -> RunRecord` - Requires a dispatched run; publishes an immutable value.
  - `ControllerRuntime.record_node_result(controller_id: str, node_id: str, result: GraphNodeResult) -> RunRecord` - Commits a host-supplied terminal node result (spawn requests included), records it as a project-state work item with the provenance or result hash as evidence and emits `graph.node-result` plus one `graph.elastic-spawn` event per spawn record the commit created.
  - `ControllerRuntime.grant_elastic_capacity(controller_id: str, *, max_elastic_depth: int | None=None, max_elastic_nodes: int | None=None, reason: str) -> RunRecord` - Requires a dispatched run and an EXECUTING controller, delegates to the coordinator and emits `graph.elastic-capacity-granted` (the recorded grant) plus a `graph.elastic-spawn` event for every request the grant released. · *Called by:* `mcp/controller_tools.py::register_controller_tools.grant_elastic_capacity`
  - `ControllerRuntime.decline_elastic_requests(controller_id: str, parent_node_id: str, reason: str) -> RunRecord` - Requires a dispatched run and an EXECUTING controller, discards the node's deferred requests and emits `graph.elastic-requests-declined` with the request ids and the reason. · *Called by:* `mcp/controller_tools.py::register_controller_tools.decline_elastic_requests`
  - `ControllerRuntime.execute_graph(controller_id: str, executors: Mapping[GraphNodeKind, NodeExecutor], *, max_parallelism: int | None=None) -> RunRecord` *(async)* - Requires EXECUTING and a run; runs all waves through host executors, emits `graph.elastic-spawn` for each spawn record the execution created, reduces every terminal result into project state, enters repair or escalation if any node FAILED, and emits `graph.executed` with status failed, blocked or completed plus the failed and blocked node ids. A join held for a capacity decision is BLOCKED, so the execution reports blocked and the controller cannot complete until the decision is made. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.dispatch_and_execute`
  - `ControllerRuntime.verify_provenance_contract(controller_id: str, records: list[ProvenanceRecord], required_schema_version: str) -> ProvenanceGateDecision` - Runs the provenance gate against the controller's snapshot id; a rejection while executing records a stage failure.
  - `ControllerRuntime.request_lateral_dependency(controller_id: str, request: LateralDependencyRequest) -> RunRecord` - Requires a dispatched run; records a lateral dependency.
  - `ControllerRuntime.record_stage_failure(controller_id: str, reason: str) -> ControllerRecord` - Explicit bounded repair or escalation request. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.evaluate_stage_completeness`, `state/controller_runtime.py::ControllerRuntime.execute_graph`, `state/controller_runtime.py::ControllerRuntime.verify_provenance_contract`
  - `ControllerRuntime.evaluate_stage_completeness(controller_id: str, policy: StageCompletenessPolicy) -> StageCompletenessDecision` - Evaluates a completeness policy over the project state; an incomplete result while executing records a stage failure with the joined reasons. · *No in-package callers (public API, entry point, or protocol hook).*
  - `ControllerRuntime.complete(controller_id: str) -> ControllerRecord` - Marks the controller completed; while EXECUTING it first requires every node of the bound graph run to be COMPLETED, so a controller whose nodes failed, are blocked or never ran cannot be completed. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.evaluate_stage_completeness`
  - `ControllerRuntime.cancel(controller_id: str, reason: str) -> ControllerRecord` - Cancels the graph run if one exists, then the controller.
  - `ControllerRuntime.shared_state(controller_id: str) -> dict[str, object]` - The persisted graph shared state as JSON.
  - `ControllerRuntime.project_state(controller_id: str) -> ProjectState` - The controller's current project state. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`, `base_agent/agent.py::BaseAgent.run`, `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `openai_compatible/chat.py::OpenAICompatibleAgentModel._chat_payload` (+2 more)
  - `ControllerRuntime.set_artifact_status(controller_id: str, relative_path: str, status: ArtifactStatus, *, reason: str) -> ProjectState` - The only route to a COMPLETE artifact: looks up the artifact recorded for `relative_path`, applies an `ARTIFACT_STATUS_UPDATED` transition under controller authority with a hashed decision as evidence and emits `controller.artifact-status-set`; raises naming the path when the artifact is not recorded with an id.
  - `ControllerRuntime._spawn_record_count(run_id: str) -> int` - Number of spawn records already persisted for a run (0 without telemetry, which skips the read); lets a call report only the records it created. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.execute_graph`, `state/controller_runtime.py::ControllerRuntime.record_node_result`
  - `ControllerRuntime._require_elastic_decision(record: ControllerRecord) -> str` - Returns the run id after checking the controller has a run and is EXECUTING; the errors name the controller and its phase. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.decline_elastic_requests`, `state/controller_runtime.py::ControllerRuntime.grant_elastic_capacity`
  - `ControllerRuntime._emit_spawn_records(record: ControllerRecord, items: list[dict[str, Any]]) -> None` - One `graph.elastic-spawn` event per record (status accepted, refused, deferred or discarded) carrying sequence, requesting node, request id, depth, child and join ids and the refusal code, never free text. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.execute_graph`, `state/controller_runtime.py::ControllerRuntime.grant_elastic_capacity`, `state/controller_runtime.py::ControllerRuntime.record_node_result`
  - `ControllerRuntime._next_controller_id() -> str` - Reserves the next free `controller-N` through the store's exclusive claim files, so two processes never receive the same id. · *Called by:* `state/controller_runtime.py::ControllerRuntime.create_controller` · *Called by:* `state/controller_runtime.py::ControllerRuntime.create_controller`
  - `ControllerRuntime._require_completed_graph(record: ControllerRecord) -> None` - Raises `ValueError` listing every node of the controller's run that is not COMPLETED (`node (status)`), or returns when there is no run. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.complete`
  - `ControllerRuntime._machine(controller_id: str) -> ControllerStateMachine` - Cached machine, rehydrated from the store on first use. · *Called by:* `state/controller_runtime.py::ControllerRuntime.apply_advisory_architecture`, `state/controller_runtime.py::ControllerRuntime.approve_plan`, `state/controller_runtime.py::ControllerRuntime.cancel`, `state/controller_runtime.py::ControllerRuntime.complete` (+13 more)
  - `ControllerRuntime._reduce_graph_result(record: ControllerRecord, node_id: str, result: GraphNodeResult) -> None` - Idempotent upsert of one node result into project state (skipped when the work item already has that status). · *Called by:* `state/controller_runtime.py::ControllerRuntime.execute_graph`
  - `ControllerRuntime._emit(record: ControllerRecord, event_type: str, status: str, payload: dict[str, Any] | None=None) -> None` - Emits a deterministic-authority telemetry event; a `controller.stage-failure` also records `controller.repair_attempt_count` and `controller.escalation_count`. · *Called within this file by:* `state/controller_runtime.py::ControllerRuntime.apply_advisory_architecture`, `state/controller_runtime.py::ControllerRuntime.approve_plan`, `state/controller_runtime.py::ControllerRuntime.cancel`, `state/controller_runtime.py::ControllerRuntime.complete` (+11 more)
- `_node_ids_with_status(results: Mapping[str, GraphNodeResult], status: GraphNodeStatus) -> list[str]` - Sorted ids of the nodes whose result has the given status. · *Called by:* `state/controller_runtime.py::ControllerRuntime.execute_graph`
- `_project_work_item_status(graph_status: str) -> str` - Maps terminal graph statuses to work-item statuses (KeyError for any other). · *Called by:* `state/controller_runtime.py::ControllerRuntime._reduce_graph_result`, `state/controller_runtime.py::ControllerRuntime.record_node_result`
- `_graph_result_hash(result: GraphNodeResult) -> str` - Canonical hash of a node result, the fallback evidence hash. · *Called by:* `state/controller_runtime.py::ControllerRuntime._reduce_graph_result`, `state/controller_runtime.py::ControllerRuntime.record_node_result`

**Algorithms & invariants.** Stage failures raised by `execute_graph`, the provenance gate and the completeness gate emit their own events and do not record the repair-attempt metrics; only the explicit `record_stage_failure` path does.

---

### `state/coordination_records.py` - durable run record and its integrity hash

*45 lines · depends on: `foundations/contracts.py`, `state/planning.py` · used by: `state/controller_runtime.py`, `state/harness_coordinator.py`, `state/run_state_store.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `HarnessCoordinator._save` builds a `RunRecord`; `RunStateStore` splits it into snapshot and history and re-verifies `run_hash` on every load.

**Contents**

- **class `RunRecord`** *(pydantic model; bases: StrictModel)* - Run id, plan id, the full graph snapshot, the plan validation report, the cancelled flag, `run_hash` and the history sidecar markers (`history_entry_count`, `history_integrity_hash`). · *Instantiated by:* `state/harness_coordinator.py::HarnessCoordinator._save`
  - fields: `schema_version`, `run_id`, `plan_id`, `graph`, `plan_validation`, `cancelled`, `run_hash`, `history_entry_count`, `history_integrity_hash`
- `_hash_run(run_id: str, plan_id: str, graph: dict[str, Any], validation: PlanValidationReport, cancelled: bool) -> str` - SHA-256 over canonical JSON of run id, plan id, graph snapshot, validation report and cancelled flag. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator._save`, `state/run_state_store.py::_verify_run_record`

---

### `state/elastic.py` - typed elastic-node requests, specs, spawn records and the admission checks

*275 lines · depends on: `foundations/contracts.py`, `foundations/identifiers.py`, `state/shared_state.py` · used by: `agent/elastic_context.py`, `agent/graph_agent_executor.py`, `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `state/graph.py`, `state/graph_models.py`, `state/planning.py`, `tools/core/definitions.py` (+2 more) · re-exported at the package root: 12 name(s)*

**Role in the workflow.** The elastic vocabulary in one dependency-light module, so the scheduler (`StateGraph`), the agent-side queue (`ElasticRequestBuffer`) and the orchestrator apply the same rules. A node never spawns anything itself: it returns `ElasticSpawnRequest`s in its `GraphNodeResult`, and the scheduler validates and applies the batch when it commits that result. Accepted requests become exploration (child) nodes plus one join node that resumes the requester; a batch the plan cannot hold yet is deferred behind a blocked join until a controller grants capacity or declines.

**Contents**

- **class `ElasticRefusalCode`** *(enum; bases: StrEnum)* - Why a request was refused or deferred: duplicate request id, dependency the requester cannot see, routing references it does not hold, an existing node id, the node or depth cap, a declined batch, or the per-task request limit.
  - members: `REQUEST_ID_DUPLICATE`, `DEPENDENCY_NOT_VISIBLE`, `ROUTING_REFS_NOT_INHERITED`, `NODE_ID_EXISTS`, `NODE_CAP_REACHED`, `DEPTH_CAP_REACHED`, `BATCH_DECLINED`, `REQUEST_LIMIT_REACHED`
- **class `ElasticSpawnRequest`** *(pydantic model; bases: StrictModel)* - One request for an exploration node: a request id unique within the requesting node, the declared scope, instructions and reason (all length-bounded), extra dependencies the requester can already see, and routing references that may only narrow the requester's.
  - fields: `request_id`, `scope`, `instructions`, `reason`, `dependencies`, `routing_refs`
  - `ElasticSpawnRequest.request_id_is_an_identifier(value: str) -> str` *(validator, classmethod)* - Validator: the id is a 1-128 character identifier without a colon, so node ids derived from it cannot collide.
  - `ElasticSpawnRequest.text_is_not_blank(value: str, info: ValidationInfo) -> str` *(validator, classmethod)* - Validator: scope, instructions and reason hold more than whitespace.
  - `ElasticSpawnRequest.dependencies_are_unique_and_named(value: list[str]) -> list[str]` *(validator, classmethod)* - Validator: dependency ids are non-empty and unique.
- **class `ElasticNodeRole`** *(enum; bases: StrEnum)* - child (an exploration node) or join (the continuation that resumes the requester).
  - members: `CHILD`, `JOIN`
- **class `ElasticNodeSpec`** *(pydantic model; bases: StrictModel)* - What an elastic node is: its role, the node that requested it, the planned root it descends from, the request that created it (children) or the children it joins (joins; empty while the join is held for a capacity decision). · *Instantiated by:* `state/graph.py::StateGraph._add_children`, `state/graph.py::StateGraph._add_join`, `state/graph.py::StateGraph._hold_join`
  - fields: `role`, `parent_node_id`, `root_node_id`, `request`, `joins`
  - `ElasticNodeSpec.spec_matches_role() -> ElasticNodeSpec` *(validator)* - Validator: joins are unique; a child carries its request and no joins; a join carries no request.
- **class `GraphSpawnStatus`** *(enum; bases: StrEnum)* - accepted, refused, deferred (held for a capacity decision) or discarded (declined).
  - members: `ACCEPTED`, `REFUSED`, `DEFERRED`, `DISCARDED`
- **class `GraphSpawnRecord`** *(pydantic model; bases: StrictModel)* - The audit row for one request: sequence, position in the scheduler's event log, requesting node, the request, depth, status, child and join node ids, refusal code and reason, and the grant that released it. Persisted with the graph in the same commit that created the nodes. · *Instantiated by:* `state/graph.py::StateGraph._apply_spawn_requests`
  - fields: `sequence`, `event_sequence`, `parent_node_id`, `request`, `status`, `depth`, `child_node_id`, `join_node_id`, `code`, `reason`, `grant_sequence`
  - `GraphSpawnRecord.fields_match_status() -> GraphSpawnRecord` *(validator)* - Validator: an accepted record names child and join and carries no code; a refused one carries a code and reason and no nodes; a deferred or discarded one carries a code, a reason and the held join.
- **class `GraphCapacityGrant`** *(pydantic model; bases: StrictModel)* - A recorded controller decision that raised the caps: sequence, previous and new `max_elastic_depth` and `max_elastic_nodes`, and the reason. · *Instantiated by:* `state/graph.py::StateGraph.grant_elastic_capacity`
  - fields: `sequence`, `previous_max_depth`, `max_depth`, `previous_max_nodes`, `max_nodes`, `reason`
  - `GraphCapacityGrant.grant_only_raises() -> GraphCapacityGrant` *(validator)* - Validator: a non-blank reason, no cap lowered and at least one raised.
- **class `ElasticCapacity`** *(pydantic model; bases: StrictModel)* - What a running node may still ask for: its own elastic depth, the caps and how many exploration nodes the run has used. Handed to every node in its execution context. · *Instantiated by:* `state/graph.py::StateGraph.execution_context`
  - fields: `node_depth`, `max_depth`, `nodes_used`, `max_nodes`
  - `ElasticCapacity.remaining_nodes() -> int` *(property)* - Property: exploration nodes still available (never negative). · *Called by:* `tools/elastic_requests.py::ElasticRequestBuffer.add`
  - `ElasticCapacity.depth_available() -> bool` *(property)* - Property: whether a child of this node would still be within the depth cap. · *No in-package callers (public API, entry point, or protocol hook).*
- **class `ElasticProblem`** *(dataclass)* - A refusal code with its message, returned by the checks instead of raised. · *Instantiated by:* `state/elastic.py::capacity_problem`, `state/elastic.py::check_spawn_request`, `state/graph.py::StateGraph._apply_spawn_requests`, `tools/elastic_requests.py::ElasticRequestBuffer.add`
- `elastic_child_id(parent_node_id: str, request_id: str) -> str` - `elastic:<parent>:<request>`; injective because a request id never contains a colon. · *Called by:* `state/graph.py::StateGraph._add_children`, `state/graph.py::StateGraph._apply_spawn_requests`
- `elastic_join_id(parent_node_id: str) -> str` - `join:<parent>`; its prefix differs from every child id, so a join never collides with a child. · *Called by:* `state/graph.py::StateGraph._apply_deferred_batches`, `state/graph.py::StateGraph._apply_spawn_requests`, `state/graph.py::StateGraph.decline_elastic_requests`
- `validate_elastic_caps(max_depth: int, max_nodes: int) -> None` - Raises naming the cap when depth is outside 0-8 or nodes outside 0-256. · *Called by:* `state/graph.py::StateGraph.__init__`
- `_quoted(values: Collection[str]) -> str` - Sorted, quoted, comma-separated ids for messages. · *Called within this file by:* `state/elastic.py::check_spawn_request`
- `_widened_refs(claimed: DiscoveryRoutingRefs, held: DiscoveryRoutingRefs) -> dict[str, list[str]]` - Per routing field, the ids a request claims beyond what its parent holds. · *Called within this file by:* `state/elastic.py::check_spawn_request`
- `check_spawn_request(request: ElasticSpawnRequest, *, parent_node_id: str, parent_routing_refs: DiscoveryRoutingRefs, visible_dependencies: Collection[str], taken_requ...` - Returns the first problem in a fixed order - duplicate request id, a dependency the requester cannot see (its predecessors plus itself), widened routing references - or None when the request is admissible. The same check serves the scheduler and the agent-side queue. · *Called by:* `state/graph.py::StateGraph._apply_spawn_requests`, `tools/elastic_requests.py::ElasticRequestBuffer.add`
- `capacity_problem(*, node_id: str, node_depth: int, max_depth: int, nodes_used: int, max_nodes: int, requested: int) -> ElasticProblem | None` - Depth cap first (children are one level deeper than the requester), then the node cap for the whole batch; None when it fits. · *Called by:* `state/graph.py::StateGraph._apply_deferred_batches`, `state/graph.py::StateGraph._apply_spawn_requests`, `tools/elastic_requests.py::ElasticRequestBuffer.add`

**Algorithms & invariants.** Hard limits: elastic depth 8 (`MAX_ELASTIC_DEPTH_LIMIT`), 256 exploration nodes per run (`MAX_ELASTIC_NODES_LIMIT`), 32 requests per result and 64 extra dependencies per request. A child may depend only on what its requester can already see and may only narrow the requester's routing references, so a spawn can never widen what a node reads. Refusals are typed and recorded; only a cap the plan could raise defers.

*Module-level names:* `ELASTIC_REQUEST_TOOL_NAME`, `MAX_ELASTIC_DEPTH_LIMIT`, `MAX_ELASTIC_NODES_LIMIT`, `MAX_ELASTIC_REQUESTS_PER_RESULT`, `MAX_ELASTIC_DEPENDENCIES`

---

### `state/graph.py` - deterministic wave scheduler and authoritative typed run state

*1331 lines · depends on: `foundations/dependency_graph.py`, `state/elastic.py`, `state/graph_models.py`, `state/shared_state.py` · used by: `state/harness_coordinator.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** One `StateGraph` per run. The coordinator asks for the runnable wave, marks it RUNNING, persists, runs host executors concurrently (at most 32 per wave unless told otherwise), then commits terminal results in sorted node-id order. Lateral discoveries and values are published only after their producer has completed and are routed by exact reference to consumers that have not started. Hard dependencies must complete: a failed, blocked or cancelled one blocks its dependents, and a node blocked by its own result (for example a pending approval) can be re-opened with `reopen_blocked`, which also re-opens the dependents that were blocked only because of it. **Elastic continuation.** A completed result may carry `spawn_requests`; `mark_terminal` judges them in order and, for the accepted batch, creates the exploration children (`elastic:<parent>:<id>`, one level deeper), one join (`join:<parent>`) that depends on the parent and waits for every child, and puts the parent's not-yet-started dependents behind the join, all in the same commit as the result and recorded in `spawn_records`. Fan-in edges are soft: a failed child lets the join run with its typed failure, while a blocked or cancelled child blocks it. A batch over the depth or node cap is deferred as a whole behind a join that is blocked by its own result until `grant_elastic_capacity` or `decline_elastic_requests` decides it.

**Contents**

- `resolve_parallelism(max_parallelism: int | None) -> int` - Returns `max_parallelism` or the default cap `DEFAULT_MAX_PARALLELISM` (32) when None; rejects values below one. · *Called by:* `state/graph.py::StateGraph.execute`, `state/graph.py::StateGraph.start_runnable_wave`, `state/harness_coordinator.py::HarnessCoordinator.execute_run`
- **class `StateGraph`** *(class)* - Scheduler plus durable state: nodes, edges, statuses, results, events and graph shared state, all serialised together by `snapshot`. · *Instantiated by:* `integrations/langgraph.py::build_langgraph_state_graph`, `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - `StateGraph.__init__(nodes: list[GraphNode], edges: list[GraphEdge] | None=None, *, shared_state: GraphSharedState | None=None, max_elastic_depth: int=1, max_elastic_n...` - Validates the elastic caps, rejects duplicate node ids, validates dependencies and acyclicity and the elastic-node invariants (also on restore), rebuilds the routing index and promotes ready nodes to RUNNABLE.
  - `StateGraph.nodes() -> Mapping[str, GraphNode]` *(property)* - Property: a copy of the node map. · *Called by:* `foundations/dependency_graph.py::deterministic_cycles`, `foundations/dependency_graph.py::reverse_reachable_count`, `foundations/dependency_graph.py::reverse_reachable_nodes`, `memory/context_selection.py::TaskAwareContextSelector.select` (+16 more)
  - `StateGraph.events() -> tuple[GraphEvent, ...]` *(property)* - Property: the ordered status-transition log.
  - `StateGraph.shared_state() -> GraphSharedState` *(property)* - Property: a deep copy of the graph-owned lateral state, so callers cannot mutate scheduler state. · *Called within this file by:* `state/graph.py::StateGraph.__init__`, `state/graph.py::StateGraph._filtered_shared_state_for`, `state/graph.py::StateGraph.execute`, `state/graph.py::StateGraph.execution_context`
  - `StateGraph.spawn_records() -> tuple[GraphSpawnRecord, ...]` *(property)* - Property: the ordered audit rows of every spawn request. · *No in-package callers (public API, entry point, or protocol hook).*
  - `StateGraph.capacity_grants() -> tuple[GraphCapacityGrant, ...]` *(property)* - Property: the recorded cap raises, oldest first. · *No in-package callers (public API, entry point, or protocol hook).*
  - `StateGraph.status(node_id: str) -> GraphNodeStatus` - Current status of one node. · *Called by:* `base_agent/agent.py::BaseAgent._episode_summary_text`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`, `base_agent/agent.py::BaseAgent._execute_tool_batch`, `base_agent/agent.py::BaseAgent._execute_tool_call` (+74 more)
  - `StateGraph.result(node_id: str) -> GraphNodeResult | None` - Terminal result of a node, or None. · *Called by:* `base_agent/agent.py::BaseAgent._apply_tool_outcome_to_project_state`, `base_agent/agent.py::BaseAgent._episode_summary_text`, `base_agent/agent.py::BaseAgent._execute_profiled_tool_call`, `base_agent/agent.py::BaseAgent._execute_tool_batch` (+67 more)
  - `StateGraph.snapshot() -> dict[str, Any]` - JSON-able dump of nodes, edges, shared state, statuses, results, events, elastic caps, spawn records and capacity grants. · *Called within this file by:* `state/graph.py::StateGraph._filtered_shared_state_for`
  - `StateGraph.from_snapshot(payload: dict[str, Any]) -> StateGraph` *(classmethod)* - Rebuilds a graph from a dump (snapshots from before elastic nodes still load); statuses must cover exactly the node ids and every terminal node must carry a recorded result, otherwise it raises naming the nodes; the elastic-node count is recomputed from the nodes.
  - `StateGraph.runnable() -> list[GraphNode]` - Blocks nodes whose dependencies failed, promotes ready nodes and returns the RUNNABLE ones in node-id order. · *Called by:* `integrations/langchain.py::LangChainAgentModelAdapter.__init__`, `integrations/langgraph.py::LangGraphNodeExecutor.__init__`, `state/graph.py::StateGraph.execute`, `state/graph.py::StateGraph.start_runnable_wave` (+1 more)
  - `StateGraph.start_runnable_wave(*, max_parallelism: int | None=None) -> list[GraphNode]` - Atomically moves the next wave (optionally truncated to `max_parallelism`, which must be at least one) to RUNNING so a restart can tell an interrupted node from a never-started one. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.execute_run`
  - `StateGraph.blocked_nodes() -> list[str]` - Sorted ids of BLOCKED nodes that were blocked by their own result, excluding nodes blocked only because a dependency did not complete. · *Called by:* `state/graph.py::StateGraph.reopen_blocked`, `state/harness_coordinator.py::HarnessCoordinator.resume_run`
  - `StateGraph.reopen_blocked(node_ids: Iterable[str] | None=None) -> list[str]` - Returns the requested (default: all) self-blocked nodes to PENDING and drops their results, also re-opening the dependents that were blocked only by them, then recomputes runnability; a node that is not self-blocked raises, listing the nodes that can be re-opened. A join holding deferred elastic requests is never re-opened this way: the default skips it and naming it raises, pointing at `grant_elastic_capacity` and `decline_elastic_requests`. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.reopen_blocked_nodes`, `state/harness_coordinator.py::HarnessCoordinator.resume_run`
  - `StateGraph.execution_context(node_id: str, *, shared_state: GraphSharedState | None=None) -> GraphNodeExecutionContext` - For a RUNNING node returns deep copies of exactly its dependency results (hard and soft) plus a filtered shared-state view, the node's remaining elastic capacity and, for a join, the requests of the children it joins; raises naming the node when a dependency has no recorded result. · *Called by:* `state/graph.py::StateGraph.execute.execute_one`, `state/harness_coordinator.py::HarnessCoordinator.execute_run.execute_one`
  - `StateGraph.recover_interrupted(replayable_node_ids: set[str]=frozenset()) -> list[str]` - After a crash, nodes persisted as RUNNING return to PENDING only if declared idempotent; every other one becomes FAILED (`interrupted-non-idempotent`). · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.recover_interrupted_run`
  - `StateGraph.mark_started(node_id: str) -> None` - RUNNABLE to RUNNING. · *Called by:* `state/graph.py::StateGraph.execute`, `state/graph.py::StateGraph.start_runnable_wave`, `state/harness_coordinator.py::HarnessCoordinator._cancel_runnable_nodes`, `state/harness_coordinator.py::HarnessCoordinator.record_node_result`
  - `StateGraph.mark_terminal(node_id: str, result: GraphNodeResult) -> None` - RUNNING to a terminal status, storing the result; a completed result's spawn requests are applied in the same commit, then dependents of unreachable nodes are blocked and new runnable nodes promoted. · *Called by:* `state/graph.py::StateGraph.execute`, `state/harness_coordinator.py::HarnessCoordinator._cancel_runnable_nodes`, `state/harness_coordinator.py::HarnessCoordinator.execute_run`, `state/harness_coordinator.py::HarnessCoordinator.record_node_result`
  - `StateGraph.execute(executors: Mapping[GraphNodeKind, NodeExecutor], *, max_parallelism: int | None=None) -> dict[str, GraphNodeResult]` *(async)* - In-memory wave loop built on `start_runnable_wave` (at most `max_parallelism` nodes per wave, default 32): runs each wave's executors concurrently (`asyncio.gather`), converting a missing executor or an exception into a FAILED result that names the node, its kind and the exception type; commits results in node-id order.
    - `StateGraph.execute.execute_one(node: GraphNode) -> GraphNodeResult` *(async)* - Runs one node's executor with its context; a missing executor or an exception becomes a FAILED result instead of escaping. · *Called within this file by:* `state/graph.py::StateGraph.execute`
  - `StateGraph.publish_discovery(discovery: ExploratoryDiscovery) -> None` - Requires a closed discovery for the graph's snapshot id and version from a known, COMPLETED producer and an unused episode id; stores it and routes it. · *Called within this file by:* `state/graph.py::StateGraph.try_publish_discovery`
  - `StateGraph.try_publish_discovery(discovery: ExploratoryDiscovery) -> GraphStateConflict | None` - Like publish, but a snapshot-id or version mismatch is recorded as a typed conflict and returned instead of raised. · *No in-package callers (public API, entry point, or protocol hook).*
  - `StateGraph.write_shared_value(state_write: SharedStateWrite) -> None` - Stores an immutable keyed value from a COMPLETED producer; duplicate keys raise. · *Called within this file by:* `state/graph.py::StateGraph.try_write_shared_value`
  - `StateGraph.try_write_shared_value(state_write: SharedStateWrite) -> GraphStateConflict | None` - Like write, but a key collision is recorded as an `immutable-write-collision` conflict. · *No in-package callers (public API, entry point, or protocol hook).*
  - `StateGraph.request_lateral_dependency(request: LateralDependencyRequest) -> None` - Requires a published discovery, a distinct known consumer and no identical request; adds the conditional edge and records the request. · *Called within this file by:* `state/graph.py::StateGraph._route_discovery`
  - `StateGraph.grant_elastic_capacity(*, max_elastic_depth: int | None=None, max_elastic_nodes: int | None=None, reason: str) -> GraphCapacityGrant` - Requires a reason and at least one cap; refuses to lower a cap or pass the hard limits; records the grant, then applies every deferred batch that now fits (children created, held join and its dependents re-opened). A batch that still does not fit keeps its records with the new reason. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.grant_elastic_capacity`
  - `StateGraph.decline_elastic_requests(parent_node_id: str, reason: str) -> list[GraphSpawnRecord]` - Marks the node's deferred requests discarded, completes the held join with a typed `elastic-requests-declined` result without running an agent and re-opens the dependents it held; raises naming the node when it has no deferred requests or the reason is blank. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.decline_elastic_requests`
  - `StateGraph._held_join_ids() -> set[str]` - Join nodes that currently hold deferred requests. · *Called within this file by:* `state/graph.py::StateGraph.reopen_blocked`
  - `StateGraph._apply_spawn_requests(parent_id: str, result: GraphNodeResult) -> None` - Judges every request in order (duplicate id, id already in the graph, visibility, routing references), then the valid ones as one batch against the caps: accepted creates the children and the join and catches up earlier discoveries; over the caps defers all of them behind a held join. Writes the records in request order and appends `elastic-refused:` or `elastic-deferred:` diagnostics to the parent's stored result. · *Called within this file by:* `state/graph.py::StateGraph.mark_terminal`
  - `StateGraph._elastic_root(parent: GraphNode) -> str` - The planned node an elastic lineage descends from. · *Called within this file by:* `state/graph.py::StateGraph._add_children`, `state/graph.py::StateGraph._add_join`, `state/graph.py::StateGraph._hold_join`, `state/graph.py::StateGraph._validate_elastic`
  - `StateGraph._add_children(parent: GraphNode, accepted: Sequence[tuple[ElasticSpawnRequest, int]]) -> list[str]` - Creates the exploration nodes (PENDING, depending on the parent plus the request's visible extras, one level deeper) with a dynamic-fan-out edge each, counts them against the cap and rebuilds the routing index. · *Called within this file by:* `state/graph.py::StateGraph._apply_deferred_batches`, `state/graph.py::StateGraph._apply_spawn_requests`
  - `StateGraph._add_join(parent: GraphNode, join_id: str, child_ids: Sequence[str]) -> None` - Creates the join (hard dependency on the parent), adds a fan-in edge from every child and holds the parent's dependents behind it. · *Called within this file by:* `state/graph.py::StateGraph._apply_spawn_requests`
  - `StateGraph._attach_children_to_join(join_id: str, parent_id: str, child_ids: Sequence[str]) -> None` - Adds the soft fan-in edges from each child to its join. · *Called within this file by:* `state/graph.py::StateGraph._add_join`, `state/graph.py::StateGraph._apply_deferred_batches`
  - `StateGraph._hold_join(parent: GraphNode, join_id: str, problem: ElasticProblem) -> None` - Creates a join that is already BLOCKED by its own result (diagnostic `elastic-capacity-decision-required`) naming the exceeded cap, `grant_elastic_capacity` and `decline_elastic_requests`, and holds the parent's dependents behind it. · *Called within this file by:* `state/graph.py::StateGraph._apply_spawn_requests`
  - `StateGraph._hold_dependents_behind(parent_id: str, join_id: str) -> None` - For every node that has not started and waited on the parent (PENDING, RUNNABLE, or BLOCKED only by a dependency) adds a join-to-node edge: a hard wait stays hard (static edge), a soft one stays soft (fan-in). A RUNNABLE node returns to PENDING; a dependency-blocked node stays blocked until it is re-opened and then waits for the join. · *Called within this file by:* `state/graph.py::StateGraph._add_join`, `state/graph.py::StateGraph._hold_join`
  - `StateGraph._apply_deferred_batches(grant_sequence: int) -> None` - After a grant, re-checks each deferred batch in order: one that fits gets its children, its join's `joins` filled, its records accepted with the grant sequence, and the join and the dependents it held re-opened; one that does not fit keeps its records with the new reason. · *Called within this file by:* `state/graph.py::StateGraph.grant_elastic_capacity`
  - `StateGraph._catch_up_discoveries(new_node_ids: set[str]) -> None` - Routes discoveries published before a node existed to the new nodes whose references match (ACCEPTED decision "matched a node spawned after publication"). · *Called within this file by:* `state/graph.py::StateGraph._apply_deferred_batches`, `state/graph.py::StateGraph._apply_spawn_requests`
  - `StateGraph.add_lateral_dependency(producer_node_id: str, consumer_node_id: str, discovery_episode_id: str) -> None` - Adds one conditional producer-to-consumer edge for a published discovery; the producer must be COMPLETED and the consumer not started; a RUNNABLE consumer returns to PENDING. · *Called within this file by:* `state/graph.py::StateGraph.request_lateral_dependency`
  - `StateGraph._rebuild_routing_index() -> None` - Rebuilds the exact-reference postings (requirement, signal, task, schema to node ids) from node routing refs. · *Called by:* `state/graph.py::StateGraph.__init__`, `state/graph.py::StateGraph.from_snapshot`, `state/graph.py::StateGraph._add_children`
  - `StateGraph._route_discovery(discovery: ExploratoryDiscovery) -> None` - Intersects the non-empty posting lists of the discovery's refs; no refs or no match is a REJECTED decision; a producer cannot consume its own discovery; started or finished consumers get a LATE_DISCOVERY decision and conflict; others get a lateral dependency and an ACCEPTED decision. · *Called by:* `state/graph.py::StateGraph.publish_discovery`
  - `StateGraph._candidate_consumers(discovery: ExploratoryDiscovery) -> set[str] | None` - The nodes that match every non-empty reference list of a discovery; None when it declares no references. · *Called within this file by:* `state/graph.py::StateGraph._catch_up_discoveries`, `state/graph.py::StateGraph._route_discovery`
  - `StateGraph._append_route_decision(discovery: ExploratoryDiscovery, *, consumer_node_id: str, status: DiscoveryRouteStatus, reason: str) -> None` - Appends one ledger row stamped with the snapshot id and schema version. · *Called by:* `state/graph.py::StateGraph._route_discovery`
  - `StateGraph._record_conflict(kind: GraphStateConflictKind, subject_id: str, *, producer_node_id: str | None=None, consumer_node_id: str | None=None, reason: str) -> GraphState...` - Appends a sequence-numbered conflict to the shared state and returns it. · *Called by:* `state/graph.py::StateGraph._route_discovery`, `state/graph.py::StateGraph.try_publish_discovery`, `state/graph.py::StateGraph.try_write_shared_value`
  - `StateGraph._filtered_shared_state_for(node_id: str, snapshot: GraphSharedState | None) -> GraphSharedState` - Returns a deep copy limited to the discoveries and lateral requests addressed to this node. Note: `values`, the routing index, decisions and conflicts remain visible to every node. · *Called by:* `state/graph.py::StateGraph.execution_context`
  - `StateGraph._validate_graph() -> None` - Checks dependency and edge endpoints exist and the dependency graph (node dependencies plus enabled edges) is acyclic. · *Called by:* `state/graph.py::StateGraph.__init__`
  - `StateGraph._validate_elastic() -> None` - Construction and restore invariants: each elastic node's parent exists and is one level shallower, its root matches its ancestry, joins name children of the same parent, every child has an accepted spawn record, deferred records point at held joins, and depths and the child count are within the caps (a join that holds no children yet, held or declined, is a placeholder and exempt from the depth cap); messages name the node and the numbers. · *Called within this file by:* `state/graph.py::StateGraph.__init__`
  - `StateGraph._is_dependency_blocked(node_id: str) -> bool` - True when a node's recorded result is the one `_block_unreachable_nodes` produced (diagnostic `blocked-by-dependency`, or a reason starting `Dependency "`). · *Called within this file by:* `state/graph.py::StateGraph.blocked_nodes`, `state/graph.py::StateGraph._dependency_blocked_descendants`
  - `StateGraph._dependency_blocked_descendants(roots: Iterable[str]) -> list[str]` - Sorted ids of BLOCKED nodes reachable from the given roots through nodes that are blocked only by a dependency. · *Called within this file by:* `state/graph.py::StateGraph.reopen_blocked`
  - `StateGraph._split_dependencies(node_id: str) -> tuple[set[str], set[str]]` - (hard, soft) predecessors: node dependencies and enabled non-fan-in edges are hard; enabled fan-in edges are soft unless the same node is also hard. · *Called within this file by:* `state/graph.py::StateGraph._block_unreachable_nodes`, `state/graph.py::StateGraph._dependencies_for`, `state/graph.py::StateGraph._hold_dependents_behind`, `state/graph.py::StateGraph._refresh_runnable`
  - `StateGraph._dependencies_for(node_id: str) -> set[str]` - Union of a node's hard and soft predecessors (declared dependencies and the parents of its enabled edges). · *Called by:* `state/graph.py::StateGraph._block_unreachable_nodes`, `state/graph.py::StateGraph._refresh_runnable`, `state/graph.py::StateGraph._validate_graph`, `state/graph.py::StateGraph.execution_context`
  - `StateGraph._refresh_runnable() -> None` - Promotes every PENDING node whose hard dependencies are all COMPLETED and whose soft (fan-in) predecessors are all COMPLETED or FAILED. · *Called by:* `state/graph.py::StateGraph.__init__`, `state/graph.py::StateGraph.add_lateral_dependency`, `state/graph.py::StateGraph.mark_terminal`, `state/graph.py::StateGraph.recover_interrupted` (+2 more)
  - `StateGraph._block_unreachable_nodes() -> None` - Repeats until stable: a PENDING or RUNNABLE node with a FAILED, BLOCKED or CANCELLED hard dependency, or a BLOCKED or CANCELLED soft predecessor, becomes BLOCKED with a result naming that dependency and carrying the `blocked-by-dependency` diagnostic. · *Called by:* `state/graph.py::StateGraph.mark_terminal`, `state/graph.py::StateGraph.recover_interrupted`, `state/graph.py::StateGraph.runnable`
  - `StateGraph._set_status(node_id: str, status: GraphNodeStatus, reason: str | None=None) -> None` - Single point that changes status and appends a sequence-numbered event. · *Called by:* `state/graph.py::StateGraph._block_unreachable_nodes`, `state/graph.py::StateGraph._refresh_runnable`, `state/graph.py::StateGraph.add_lateral_dependency`, `state/graph.py::StateGraph.mark_started` (+2 more)
  - `StateGraph._assert_acyclic(dependencies: Mapping[str, set[str]]) -> None` *(staticmethod)* - Delegates to the iterative `deterministic_cycles` and raises `ValueError` naming the first node of the first cycle, so chains of any length are safe. · *Called within this file by:* `state/graph.py::StateGraph._validate_graph`

---

### `state/graph_models.py` - typed node, edge, event and shared-state contracts for the run graph

*220 lines · depends on: `foundations/contracts.py`, `foundations/json_limits.py`, `state/elastic.py`, `state/shared_state.py` · used by: `agent/elastic_context.py`, `agent/graph_agent_executor.py`, `agent/orchestrator/orchestrator.py`, `integrations/langgraph.py`, `mcp/controller_tools.py`, `state/controller_runtime.py`, `state/graph.py`, `state/harness_coordinator.py` (+1 more) · re-exported at the package root: 10 name(s)*

**Role in the workflow.** The vocabulary of `StateGraph`: what a node is, which statuses it can have, what an executor receives and returns, and what lateral state a node may see.

**Contents**

- **class `GraphNodeKind`** *(enum; bases: StrEnum)* - agent-invocation, deterministic-gate, pure-function or elastic-node; selects the host executor. The coordinator creates agent nodes; elastic nodes (explorations and joins) are created only by the scheduler, in the commit of a result that carries spawn requests.
  - members: `AGENT`, `GATE`, `FUNCTION`, `ELASTIC`
- **class `GraphEdgeKind`** *(enum; bases: StrEnum)* - static (also the hard wait of a join's dependents), conditional (lateral discovery edge), dynamic-fan-out (requester to elastic child) or fan-in (child or join to the join that waits for it: soft, so a failed predecessor does not block the join).
  - members: `STATIC`, `CONDITIONAL`, `DYNAMIC_FAN_OUT`, `FAN_IN`
- **class `GraphNodeStatus`** *(enum; bases: StrEnum)* - pending, runnable, running, completed, failed, blocked or cancelled. · *Instantiated by:* `state/graph.py::StateGraph.from_snapshot`
  - members: `PENDING`, `RUNNABLE`, `RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED`, `CANCELLED`
- **class `GraphStateConflictKind`** *(enum; bases: StrEnum)* - immutable-write-collision, discovery-snapshot-mismatch, discovery-version-mismatch or late-discovery.
  - members: `IMMUTABLE_WRITE_COLLISION`, `DISCOVERY_SNAPSHOT_MISMATCH`, `DISCOVERY_VERSION_MISMATCH`, `LATE_DISCOVERY`
- **class `GraphStateConflict`** *(pydantic model; bases: StrictModel)* - A persisted, sequence-numbered conflict record with subject, producer, consumer and reason. · *Instantiated by:* `state/graph.py::StateGraph._record_conflict`
  - fields: `sequence`, `kind`, `subject_id`, `producer_node_id`, `consumer_node_id`, `reason`
- **class `GraphNode`** *(pydantic model; bases: StrictModel)* - One schedulable node: id, kind, task id, dependencies, elastic depth, metadata, routing refs and, for an elastic node, the spec that says what created it. · *Instantiated by:* `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - fields: `node_id`, `kind`, `task_id`, `dependencies`, `elastic_depth`, `metadata`, `routing_refs`, `elastic`
  - `GraphNode.metadata_is_bounded(metadata: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: node metadata nests at most 64 levels.
  - `GraphNode.node_is_well_formed() -> GraphNode` *(validator)* - Validator: dependencies are unique and not self-referential; an elastic-node kind node carries its spec, has elastic depth of at least 1 and depends on its parent; any other node has neither a spec nor a depth.
- **class `GraphEdge`** *(pydantic model; bases: StrictModel)* - A parent-to-child edge with a kind, an enabled flag and metadata (lateral edges carry the discovery episode id). · *Instantiated by:* `state/graph.py::StateGraph.add_lateral_dependency`, `state/graph.py::StateGraph._add_children`, `state/graph.py::StateGraph._attach_children_to_join`, `state/graph.py::StateGraph._hold_dependents_behind`
  - fields: `parent_node_id`, `child_node_id`, `kind`, `enabled`, `metadata`
  - `GraphEdge.metadata_is_bounded(metadata: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: edge metadata nests at most 64 levels.
  - `GraphEdge.edge_has_distinct_endpoints() -> GraphEdge` *(validator)* - Validator: parent differs from child.
- **class `GraphNodeResult`** *(pydantic model; bases: StrictModel)* - Terminal result of a node: status, output, reason, artifact ids, diagnostics, provenance hash and the elastic spawn requests the scheduler should apply with it. · *Instantiated by:* `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `state/graph.py::StateGraph._block_unreachable_nodes`, `state/graph.py::StateGraph.execute.execute_one`, `state/graph.py::StateGraph.recover_interrupted` (+2 more)
  - fields: `status`, `output`, `reason`, `artifact_ids`, `diagnostics`, `provenance_hash`, `spawn_requests`
  - `GraphNodeResult.output_is_bounded(output: Any) -> Any` *(validator, classmethod)* - Validator: a node's output nests at most 64 levels.
  - `GraphNodeResult.terminal_result_only() -> GraphNodeResult` *(validator)* - Validator: status must be completed, failed, blocked or cancelled, and spawn requests are allowed only on a completed result.
- **class `GraphEvent`** *(pydantic model; bases: StrictModel)* - One sequence-numbered status transition. · *Instantiated by:* `state/graph.py::StateGraph._set_status`
  - fields: `sequence`, `node_id`, `status`, `reason`
- **class `GraphSharedState`** *(pydantic model; bases: StrictModel)* - The graph-owned lateral substrate: schema version, source snapshot, discoveries, immutable values, lateral requests, routing index, route decisions and conflicts. · *Instantiated by:* `state/controller_runtime.py::ControllerRuntime.dispatch`
  - fields: `schema_version`, `substrate`, `discoveries`, `values`, `lateral_dependencies`, `routing_index`, `route_decisions`, `conflicts`
  - `GraphSharedState.unbound() -> GraphSharedState` *(classmethod)* - Placeholder state (`graph-unbound`) for graphs built without a snapshot, for example in unit tests. · *Called by:* `state/graph.py::StateGraph.__init__`, `state/graph.py::StateGraph.from_snapshot`
- **class `GraphNodeExecutionContext`** *(pydantic model; bases: StrictModel)* - What an executor receives: results of its declared dependencies, a filtered deep copy of the shared state, its remaining elastic capacity and, for a join, the requests of the children it joins; nothing else. · *Instantiated by:* `state/graph.py::StateGraph.execution_context`
  - fields: `dependencies`, `shared_state`, `elastic_capacity`, `elastic_requests`

**Algorithms & invariants.** `TERMINAL_STATUSES` and the `NodeExecutor` callable type (`async (GraphNode, GraphNodeExecutionContext) -> GraphNodeResult`) are defined here and used by the scheduler and the host.

*Module-level names:* `TERMINAL_STATUSES`, `NodeExecutor`

---

### `state/harness_coordinator.py` - run lifecycle: plan validation, graph start, wave execution, approvals, cancel and recovery

*347 lines · depends on: `foundations/errors.py`, `state/coordination_records.py`, `state/graph.py`, `state/graph_models.py`, `state/planning.py`, `state/run_state_store.py`, `state/shared_state.py`, `tools/approvals.py` · used by: `mcp/_shared.py`, `mcp/server.py`, `state/controller_runtime.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Used by `ControllerRuntime` (dispatch and execution) and directly by the MCP run tools. `start_run` validates the plan and builds one agent node per task; `execute_run` persists after each wave start and after each terminal commit; `recover_interrupted_run` resolves crashed RUNNING nodes. Every mutating method re-saves the whole record through `RunStateStore`.

**Contents**

- `_approvals_resolved(requests: list[ApprovalRequest]) -> bool` - True when a node has at least one approval request, none is pending and at least one was approved. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.resume_run`
- **class `HarnessCoordinator`** *(class)* - Owns plan validation, the in-memory graph cache per run, approvals, cancellation and persistence. · *Instantiated by:* `mcp/server.py::create_mcp_server`, `state/controller_runtime.py::ControllerRuntime.__init__`
  - `HarnessCoordinator.__init__(run_root: Path) -> None` - Creates the store, validator, graph and approval caches, the per-run file fingerprints, the set of cancelled runs and a process-local run counter.
  - `HarnessCoordinator.start_run(plan: Plan, *, shared_state: GraphSharedState | None=None) -> RunRecord` - Validates the plan (an invalid plan raises with the rendered validation errors), reserves the next unused `run-N` through exclusive claim files, builds `node:<task>` agent nodes (dependencies prefixed the same way) with the plan's elastic caps and optional shared state, caches the graph and persists it.
  - `HarnessCoordinator.get_run_state(run_id: str) -> RunRecord` - Loads and verifies the stored record without writing anything. If the run file changed since this coordinator last read or wrote it (another instance or process), the cached graph is rebuilt from the stored snapshot, so a read can no longer revert newer durable state. · *Called within this file by:* `state/harness_coordinator.py::HarnessCoordinator.add_lateral_dependency`, `state/harness_coordinator.py::HarnessCoordinator.cancel_run`, `state/harness_coordinator.py::HarnessCoordinator.execute_run`, `state/harness_coordinator.py::HarnessCoordinator.publish_discovery` (+6 more)
  - `HarnessCoordinator.shared_state(run_id: str) -> GraphSharedState` - Graph-owned shared state after an integrity-checked load. · *Called within this file by:* `state/harness_coordinator.py::HarnessCoordinator.execute_run`, `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - `HarnessCoordinator.cancel_run(run_id: str) -> RunRecord` - Records the run as cancelled (the flag is sticky for this coordinator), cancels every node that is runnable now (pending dependents become BLOCKED) and persists; running nodes are left to finish.
  - `HarnessCoordinator.publish_discovery(run_id: str, discovery: ExploratoryDiscovery) -> RunRecord` - Publishes a discovery into the graph and persists.
  - `HarnessCoordinator.write_shared_value(run_id: str, state_write: SharedStateWrite) -> RunRecord` - Publishes an immutable value into the graph and persists.
  - `HarnessCoordinator.request_lateral_dependency(run_id: str, request: LateralDependencyRequest) -> RunRecord` - Records a lateral dependency and its conditional edge, then persists.
  - `HarnessCoordinator.add_lateral_dependency(run_id: str, producer_node_id: str, consumer_node_id: str, discovery_episode_id: str) -> RunRecord` - Compatibility wrapper for adding the conditional edge directly.
  - `HarnessCoordinator.grant_elastic_capacity(run_id: str, *, max_elastic_depth: int | None=None, max_elastic_nodes: int | None=None, reason: str) -> RunRecord` - Raises the run's elastic caps through the graph, which also applies the deferred batches that now fit, and persists the result. · *Called by:* `state/controller_runtime.py::ControllerRuntime.grant_elastic_capacity`
  - `HarnessCoordinator.decline_elastic_requests(run_id: str, parent_node_id: str, reason: str) -> RunRecord` - Discards a node's deferred elastic requests, releases the dependents they held and persists. · *Called by:* `state/controller_runtime.py::ControllerRuntime.decline_elastic_requests`
  - `HarnessCoordinator.record_node_result(run_id: str, node_id: str, result: GraphNodeResult) -> RunRecord` - Starts and terminally commits one node through the state machine (host-driven execution) and persists.
  - `HarnessCoordinator.execute_run(run_id: str, executors: Mapping[GraphNodeKind, NodeExecutor], *, max_parallelism: int | None=None) -> RunRecord` *(async)* - Returns at once for a cancelled run; otherwise loops waves (at most `max_parallelism` nodes each, default 32): before each wave it stops (cancelling the nodes that became runnable) if the run was cancelled, else starts the wave, persists, runs executors concurrently and commits results in node-id order, persisting after each. A cancel issued mid-run therefore stops later waves. · *Called by:* `state/controller_runtime.py::ControllerRuntime.execute_graph`
    - `HarnessCoordinator.execute_run.execute_one(node: GraphNode) -> GraphNodeResult` *(async)* - Same per-node executor wrapper as `StateGraph.execute`, duplicated here. · *Called within this file by:* `state/harness_coordinator.py::HarnessCoordinator.execute_run`
  - `HarnessCoordinator.recover_interrupted_run(run_id: str, replayable_node_ids: set[str]=frozenset()) -> RunRecord` - Applies `StateGraph.recover_interrupted` and persists. · *No in-package callers (public API, entry point, or protocol hook).*
  - `HarnessCoordinator.submit_approval(run_id: str, approval_id: str, approved: bool, reason: str | None=None) -> ApprovalRequest` - Records an approval decision in the run's persisted approval registry; the run must exist, whichever coordinator started it.
  - `HarnessCoordinator.approvals(run_id: str) -> ApprovalRegistry` - The run's approval registry, loaded from `<run>.approvals.json` when this coordinator did not start the run; raises `ValueError` naming an unknown run. · *Called by:* `tools/registry.py::HarnessToolExecutor._approval_for`, `tools/registry.py::HarnessToolExecutor.execute`
  - `HarnessCoordinator.resume_run(run_id: str) -> RunRecord` - Re-reads and integrity-verifies the run, then re-opens every self-blocked node whose approval requests are all decided with at least one approved, so a following `execute_run` retries it; returns the saved record (unchanged for a cancelled run or when nothing was re-opened).
  - `HarnessCoordinator.reopen_blocked_nodes(run_id: str, node_ids: list[str] | None=None) -> RunRecord` - Explicitly re-opens the given (default: all) self-blocked nodes and saves the run.
  - `HarnessCoordinator._approval_registry(run_id: str) -> ApprovalRegistry` - The run's cached `ApprovalRegistry`, created on first use bound to the run's approvals file. · *Called within this file by:* `state/harness_coordinator.py::HarnessCoordinator.approvals`, `state/harness_coordinator.py::HarnessCoordinator.resume_run`, `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - `HarnessCoordinator._cancel_runnable_nodes(graph: StateGraph) -> None` *(staticmethod)* - Marks each currently runnable node started and then CANCELLED with the reason `Run was cancelled.`. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.cancel_run`, `state/harness_coordinator.py::HarnessCoordinator.execute_run`
  - `HarnessCoordinator._save(run_id: str, plan_id: str, graph: StateGraph, validation: PlanValidationReport, cancelled: bool=False) -> RunRecord` - Refuses to save (typed `RUN_STATE_CONFLICT` naming the run) if the run file changed on disk since this coordinator last read or wrote it; otherwise snapshots the graph, ORs in the sticky cancelled flag, builds the hashed `RunRecord`, writes it through the store and remembers the new file fingerprint. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.add_lateral_dependency`, `state/harness_coordinator.py::HarnessCoordinator.cancel_run`, `state/harness_coordinator.py::HarnessCoordinator.execute_run`, `state/harness_coordinator.py::HarnessCoordinator.publish_discovery` (+5 more)

**Algorithms & invariants.** Approvals are persisted per run, so a restarted coordinator or another process sees the same requests and decisions. Detection of another writer uses the run file's fingerprint (inode, modification time, size); it narrows the race but there is still no lock, so two writers saving at the same instant can interleave.

---

### `state/orchestration.py` - controller state machine and its crash-safe store

*380 lines · depends on: `foundations/atomic_io.py`, `foundations/identifiers.py`, `state/orchestration_models.py`, `state/planning.py`, `state/shared_state.py` · used by: `state/controller_runtime.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `ControllerStateMachine` enforces the legal order planning, plan approval, dispatch, execution, then complete, with bounded repair and escalation. `ControllerStateStore` persists the record with the same snapshot-plus-sidecar pattern as run records.

**Contents**

- **class `ControllerStateMachine`** *(class)* - Owns plan review, dispatch readiness, bounded repair and escalation; every command checks the current phase. · *Instantiated by:* `state/controller_runtime.py::ControllerRuntime.create_controller`
  - `ControllerStateMachine.__init__(controller_id: str, snapshot: SharedSubstrateSnapshot, profile: SkillToolProfile, routing_rules: ComplexityRoutingRules, gap_metadata: GapMetadata...` - Requires the profile to bind the same read-only snapshot, routes the architecture, starts in PLANNING and emits `controller-started`.
  - `ControllerStateMachine.from_record(record: ControllerRecord) -> ControllerStateMachine` *(classmethod)* - Rehydrates a machine from a persisted record without re-emitting events. · *Called by:* `state/controller_runtime.py::ControllerRuntime._machine`
  - `ControllerStateMachine.submit_plan(plan: Plan) -> ControllerRecord` - Valid in PLANNING or REPAIR_REQUIRED; stores the plan with its validation report, clears approval and moves to AWAITING_PLAN_APPROVAL.
  - `ControllerStateMachine.apply_advisory_architecture(architecture: WorkflowArchitecture, *, reason: str) -> ControllerRecord` - In PLANNING only, allows a single-to-multi lift and never lowers a deterministic route.
  - `ControllerStateMachine.approve_plan(approved: bool, reason: str | None=None) -> ControllerRecord` - Valid in AWAITING_PLAN_APPROVAL for a deterministically valid plan: approval moves to DISPATCH_READY, rejection back to PLANNING.
  - `ControllerStateMachine.begin_dispatch() -> ControllerRecord` - DISPATCH_READY to EXECUTING. · *Called by:* `state/controller_runtime.py::ControllerRuntime.dispatch`
  - `ControllerStateMachine.bind_run(run_id: str) -> ControllerRecord` - Valid in EXECUTING; records the graph run id (a second bind is logged as a rebind). · *Called by:* `state/controller_runtime.py::ControllerRuntime.dispatch`
  - `ControllerStateMachine.record_stage_failure(reason: str) -> ControllerRecord` - Valid in EXECUTING; below the repair cap moves to REPAIR_REQUIRED and counts the attempt, above it moves to ESCALATED with the reason.
  - `ControllerStateMachine.complete() -> ControllerRecord` - EXECUTING to COMPLETED.
  - `ControllerStateMachine.cancel(reason: str) -> ControllerRecord` - Any non-terminal phase to CANCELLED; completed or cancelled controllers raise.
  - `ControllerStateMachine._require(*allowed: ControllerPhase) -> None` - Raises naming the current phase and the expected ones. · *Called within this file by:* `state/orchestration.py::ControllerStateMachine.apply_advisory_architecture`, `state/orchestration.py::ControllerStateMachine.approve_plan`, `state/orchestration.py::ControllerStateMachine.begin_dispatch`, `state/orchestration.py::ControllerStateMachine.bind_run` (+3 more)
  - `ControllerStateMachine._emit(type_: str, details: dict[str, Any]) -> None` - Appends a sequence-numbered event stamped with the current phase. · *Called within this file by:* `state/orchestration.py::ControllerStateMachine.__init__`, `state/orchestration.py::ControllerStateMachine.apply_advisory_architecture`, `state/orchestration.py::ControllerStateMachine.approve_plan`, `state/orchestration.py::ControllerStateMachine.begin_dispatch` (+5 more)
- `_replace_with_retry(temporary: Path, target: Path, *, attempts: int=5) -> None` - Atomic replace with bounded retries. · *Called within this file by:* `state/orchestration.py::ControllerStateStore._write_events`, `state/orchestration.py::ControllerStateStore.save`
- **class `ControllerStateStore`** *(class)* - File store under `<run_root>/.agent-controllers/`: `<id>.json` snapshot plus `<id>.events.jsonl` sidecar. · *Instantiated by:* `state/controller_runtime.py::ControllerRuntime.__init__`
  - `ControllerStateStore.__init__(root: Path) -> None` - Creates the directory and the per-controller cursor cache; ids are claimed under `.claims`.
  - `ControllerStateStore.reserve_controller_id(*, start: int=1) -> tuple[str, int]` - Reserves `controller-N` from `start` through exclusive claim files, skipping ids whose record exists; returns the id and the next number to try. · *Called by:* `state/controller_runtime.py::ControllerRuntime._next_controller_id`
  - `ControllerStateStore.save(record: ControllerRecord) -> None` - Discards an uncommitted event suffix, rewrites the sidecar if the record has fewer events than persisted (a reused controller id) or appends the new ones, then atomically writes the snapshot with the event count and hash.
  - `ControllerStateStore.exists(controller_id: str) -> bool` - True if the id is valid and a snapshot file exists for it.
  - `ControllerStateStore.load(controller_id: str) -> ControllerRecord` - Reads the snapshot (retrying transient sharing violations); verifies the named event prefix by count and hash and sequence continuity; legacy one-file records load directly. An id that is not a valid identifier raises before any path is built.
  - `ControllerStateStore._record_path(controller_id: str) -> Path` - Snapshot path for a validated controller id.
  - `ControllerStateStore._events_path(controller_id: str) -> Path` - Sidecar path. · *Called by:* `state/orchestration.py::ControllerStateStore._append_events`, `state/orchestration.py::ControllerStateStore._discard_uncommitted_events`, `state/orchestration.py::ControllerStateStore._read_events`, `state/orchestration.py::ControllerStateStore._write_events` (+1 more)
  - `ControllerStateStore._append_events(controller_id: str, events: list[ControllerEvent]) -> None` - Appends and fsyncs events. · *Called by:* `state/orchestration.py::ControllerStateStore.save`
  - `ControllerStateStore._write_events(controller_id: str, events: list[ControllerEvent]) -> None` - Atomically rewrites the sidecar through a unique temporary file. · *Called by:* `state/orchestration.py::ControllerStateStore._discard_uncommitted_events`, `state/orchestration.py::ControllerStateStore.save`
  - `ControllerStateStore._read_events(controller_id: str, *, entry_limit: int | None=None) -> tuple[list[ControllerEvent], str]` - Reads events up to a limit with their hash. · *Called within this file by:* `state/orchestration.py::ControllerStateStore._committed_event_count`, `state/orchestration.py::ControllerStateStore.load`
  - `ControllerStateStore._committed_event_count(controller_id: str) -> int` - Verified number of events the current snapshot owns. · *Called by:* `state/orchestration.py::ControllerStateStore.save`
  - `ControllerStateStore._discard_uncommitted_events(controller_id: str, committed_count: int) -> None` - Truncates a sidecar longer than the committed count. · *Called by:* `state/orchestration.py::ControllerStateStore.save`
  - `ControllerStateStore._read_events_from_path(path: Path, *, entry_limit: int | None=None) -> tuple[list[ControllerEvent], str]` *(staticmethod)* - Static reader used when discarding. · *Called by:* `state/orchestration.py::ControllerStateStore._discard_uncommitted_events`
- `_event_boundary(path: Path) -> tuple[int, str]` - Count and hash of every event in a sidecar. · *Called by:* `state/orchestration.py::ControllerStateStore.save`
- `_nonempty_event_line_count(path: Path) -> int` - Number of non-blank lines. · *Called by:* `state/orchestration.py::ControllerStateStore._discard_uncommitted_events`
- `_event_hash(events: list[ControllerEvent]) -> str` - SHA-256 over the events' JSON lines. · *Called within this file by:* `state/orchestration.py::ControllerStateStore._read_events`, `state/orchestration.py::ControllerStateStore._read_events_from_path`, `state/orchestration.py::_event_boundary`
- `_validate_event_sequence(events: list[ControllerEvent]) -> None` - Events must be numbered 1..N without gaps. · *Called by:* `state/orchestration.py::ControllerStateStore.load`
- `_write_json_candidate(path: Path, content: str) -> None` - Writes and fsyncs a temporary file. · *Called by:* `state/orchestration.py::ControllerStateStore.save`

---

### `state/orchestration_models.py` - controller phase, routing-rule and record contracts

*93 lines · depends on: `foundations/contracts.py`, `state/planning.py`, `state/shared_state.py` · used by: `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `mcp/controller_tools.py`, `state/controller_runtime.py`, `state/orchestration.py` · re-exported at the package root: 7 name(s)*

**Role in the workflow.** The data that `ControllerStateMachine` mutates and `ControllerStateStore` persists.

**Contents**

- **class `ControllerPhase`** *(enum; bases: StrEnum)* - planning, awaiting-plan-approval, dispatch-ready, executing, repair-required, escalated, completed or cancelled (`intake` exists but is never entered).
  - members: `INTAKE`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `DISPATCH_READY`, `EXECUTING`, `REPAIR_REQUIRED`, `ESCALATED`, `COMPLETED`, `CANCELLED`
- **class `WorkflowArchitecture`** *(enum; bases: StrEnum)* - single-agent or multi-agent. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.prepare`, `state/controller_runtime.py::ControllerRuntime.apply_advisory_architecture`
  - members: `SINGLE_AGENT`, `MULTI_AGENT`
- **class `GapMetadata`** *(pydantic model; bases: StrictModel)* - Categories touched, blast radius and gap types of the specification gaps behind a task; input to routing.
  - fields: `categories_touched`, `blast_radius`, `gap_types`
- **class `ComplexityRoutingRules`** *(pydantic model; bases: StrictModel)* - Thresholds for multi-agent routing: minimum categories, minimum blast radius and escalating gap types.
  - fields: `multi_agent_min_categories`, `multi_agent_min_blast_radius`, `multi_agent_gap_types`
- **class `SkillToolProfile`** *(pydantic model; bases: StrictModel)* - Stage, skill ids, capability ids and the read-only source snapshot the controller is bound to. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.submit_for_approval`
  - fields: `stage`, `skill_ids`, `capability_ids`, `source_snapshot_id`, `source_read_only`
- **class `ControllerEvent`** *(pydantic model; bases: StrictModel)* - One sequence-numbered controller event with phase, type and details. · *Instantiated by:* `state/orchestration.py::ControllerStateMachine._emit`
  - fields: `sequence`, `phase`, `type`, `details`
- **class `ControllerRecord`** *(pydantic model; bases: StrictModel)* - The controller's whole state: phase, architecture, profile, snapshot, repair counters, plan, validation, approval, run id, escalation reason, events and sidecar markers. · *Instantiated by:* `state/orchestration.py::ControllerStateMachine.__init__`
  - fields: `controller_id`, `project_state_id`, `phase`, `architecture`, `profile`, `snapshot`, `repair_attempts`, `max_repair_attempts`, `plan`, `plan_validation`, `plan_approved`, `run_id`, `escalation_reason`, `events`, `events_entry_count`, `events_integrity_hash`
- **class `ComplexityRouter`** *(class)* - Deterministic router from gap metadata to an architecture. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator.prepare`, `state/orchestration.py::ControllerStateMachine.__init__`
  - `ComplexityRouter.__init__(rules: ComplexityRoutingRules) -> None` - Stores the rules.
  - `ComplexityRouter.route(metadata: GapMetadata) -> WorkflowArchitecture` - Multi-agent if distinct categories reach the minimum, or blast radius reaches the minimum, or any gap type is listed as escalating; otherwise single-agent. · *Called by:* `orchestrator/orchestrator.py::Orchestrator.prepare`, `state/orchestration.py::ControllerStateMachine.__init__`

---

### `state/planning.py` - typed plan contracts and the deterministic plan validator

*487 lines · depends on: `foundations/contracts.py`, `foundations/dependency_graph.py`, `state/elastic.py`, `state/shared_state.py` · used by: `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `developer_tools/validate.py`, `mcp/_shared.py`, `mcp/controller_tools.py`, `mcp/orchestration_tools.py`, `mcp/server.py`, `state/controller_runtime.py` (+5 more) · re-exported at the package root: 7 name(s)*

**Role in the workflow.** A planner (a model or a host) proposes a `Plan`; `PlanValidator` recomputes every ordering that shared locked-interface signals require and checks the proposal and its cited proofs against it: each declared dependency must be justified by a derived edge and carry a proof, and every derived ordering must be implied by the declared dependencies, directly or through other tasks. `ControllerStateMachine.submit_plan` and `HarnessCoordinator.start_run` both run it, so an invalid plan can never be approved or scheduled. No model call is involved and an unproven dependency is never inferred.

**Contents**

- **class `ModelTier`** *(enum; bases: StrEnum)* - cheap, standard or strong; the orchestrator picks the model for a task from this.
  - members: `CHEAP`, `STANDARD`, `STRONG`
- **class `SignalRole`** *(enum; bases: StrEnum)* - How a task uses a signal: DEFINE, PRODUCE, CONSUME or CONSTRAINT_REFERENCE.
  - members: `DEFINE`, `PRODUCE`, `CONSUME`, `CONSTRAINT_REFERENCE`
- **class `DependencyRule`** *(enum; bases: StrEnum)* - Why an edge exists: PRODUCER_TO_CONSUMER (data flow) or GENERALITY_FALLBACK (ordering by generality rank).
  - members: `PRODUCER_TO_CONSUMER`, `GENERALITY_FALLBACK`
- **class `TaskSignalUse`** *(pydantic model; bases: StrictModel)* - A task's cited use of one signal, with source spans and a scope pointer.
  - fields: `task_id`, `signal_id`, `role`, `source_spans`, `scope_pointer`
  - `TaskSignalUse.source_spans_are_nonempty(value: list[str]) -> list[str]` *(validator, classmethod)* - Validator: every cited span is a non-blank string.
- **class `PlanTask`** *(pydantic model; bases: StrictModel)* - One bounded work unit copied from the locked specification: scope, locked interface, instructions, dependencies, acceptance criteria, model tier, signal uses, generality rank, authorized artifact ids and routing refs. · *Instantiated by:* `orchestrator/orchestrator.py::_execution_plan`
  - fields: `task_id`, `scope`, `locked_interface`, `instructions`, `dependencies`, `acceptance_criteria`, `model_tier`, `signal_uses`, `generality_rank`, `authorized_artifact_ids`, `routing_refs`
  - `PlanTask.dependencies_are_unique(value: list[str]) -> list[str]` *(validator, classmethod)* - Validator: dependency ids are unique.
  - `PlanTask.signal_uses_belong_to_task() -> PlanTask` *(validator)* - Validator: every `signal_uses` entry names this task.
  - `PlanTask.signal_ids() -> set[str]` - Exact signal identifiers declared in the verbatim `locked_interface['signals']` (plain strings or objects with an `id`). · *Called by:* `state/graph.py::StateGraph._rebuild_routing_index`, `state/graph.py::StateGraph._route_discovery`, `state/planning.py::PlanValidator.validate`, `state/shared_state.py::DiscoveryRoutingRefs.any_identifiers`
- **class `DependencyProof`** *(pydantic model; bases: StrictModel)* - A planner's justification of one edge: parent, child, shared signal ids, the rule applied and source spans.
  - fields: `parent_task_id`, `child_task_id`, `shared_signal_ids`, `applied_rule`, `source_spans`
  - `DependencyProof.proof_has_distinct_endpoints() -> DependencyProof` *(validator)* - Validator: parent differs from child and signal ids are unique.
- **class `Plan`** *(pydantic model; bases: StrictModel)* - Plan id, tasks, dependency proofs and the elastic-node caps (`max_elastic_depth` 0-8, `max_elastic_nodes` 0-256, defaults 1 and 2). · *Instantiated by:* `orchestrator/orchestrator.py::_execution_plan`
  - fields: `plan_id`, `tasks`, `dependency_proofs`, `max_elastic_depth`, `max_elastic_nodes`
  - `Plan.task_ids_are_unique() -> Plan` *(validator)* - Validator: task ids are unique.
- **class `PlanValidationError`** *(pydantic model; bases: StrictModel)* - One finding with a stable code, a message and the task and signal ids involved. · *Instantiated by:* `state/planning.py::PlanValidator._derive_signal_edge`, `state/planning.py::PlanValidator._validate_cycles`, `state/planning.py::PlanValidator._validate_proofs`, `state/planning.py::PlanValidator.validate`
  - fields: `code`, `message`, `task_ids`, `signal_ids`
- **class `PlanValidationReport`** *(pydantic model; bases: StrictModel)* - `valid`, the findings and the recomputed dependency sets per task. · *Instantiated by:* `state/planning.py::PlanValidator.validate`
  - fields: `valid`, `errors`, `recomputed_dependencies`
- **class `PlanValidator`** *(class)* - Recomputes task edges from signal-use evidence; the planner cannot choose dependencies authoritatively. · *Instantiated by:* `orchestrator/orchestrator.py::Orchestrator._validate_request_selection`, `orchestrator/orchestrator.py::Orchestrator.prepare`, `mcp/server.py::create_mcp_server`, `state/harness_coordinator.py::HarnessCoordinator.__init__` (+2 more)
  - `PlanValidator.validate(plan: Plan) -> PlanValidationReport` - Checks unknown dependencies, self-dependencies and signals absent from the locked interface; derives edges for the pairs of tasks that share a locked signal (grouped by signal, so cost follows the shared signals rather than the task count squared); requires each task's declared dependencies to be justified and to imply every derived parent; checks the proofs of the declared edges; checks for cycles. Returns the recomputed dependencies. · *Called within this file by:* `state/planning.py::PlanValidator.assert_valid`
  - `PlanValidator.assert_valid(plan: Plan) -> PlanValidationReport` - Runs `validate` and raises one `ValueError` `Plan validation failed: ...` rendering up to ten `CODE: message (tasks [...], signals [...])` entries and a count of the rest. · *Called by:* `orchestrator/orchestrator.py::Orchestrator._validate_request_selection`, `orchestrator/orchestrator.py::Orchestrator.prepare`
  - `PlanValidator._derive_edges(tasks: list[PlanTask], signal_sets: Mapping[str, set[str]], errors: list[PlanValidationError]) -> dict[_EdgeKey, set[str]]` - Indexes tasks by locked signal, then derives an edge for each pair that shares one (sorted for a stable result) with `_derive_signal_edge`; returns edge key to signal ids. · *Called within this file by:* `state/planning.py::PlanValidator.validate`
  - `PlanValidator._validate_dependency_sets(declared: Mapping[str, set[str]], derived_parents: Mapping[str, set[str]], errors: list[PlanValidationError]) -> None` *(staticmethod)* - Reports `DEPENDENCY_SET_MISMATCH` for a task that declares a dependency no shared signal derives, or does not reach (directly or transitively, via ancestor bitsets) a parent its shared signals require. A declared order that implies the derived one is accepted. · *Called within this file by:* `state/planning.py::PlanValidator.validate`
  - `PlanValidator._derive_signal_edge(left: PlanTask, right: PlanTask, signal_id: str, roles: Mapping[tuple[str, str], tuple[bool, bool]], derived: dict[_EdgeKey, set[str]], errors: li...` - Edge rule for one shared signal: a missing citation, two producers or a producer without a consumer are errors; producer+consumer gives PRODUCER_TO_CONSUMER (producer is the parent); otherwise a GENERALITY_FALLBACK edge ordered by `(generality_rank, task_id)`. Roles come from the precomputed `_signal_roles` table. · *Called by:* `state/planning.py::PlanValidator.validate`
  - `PlanValidator._fallback_order(left: PlanTask, right: PlanTask) -> tuple[PlanTask, PlanTask]` *(staticmethod)* - Returns `(parent, child)` ordered by generality rank, then task id. · *Called by:* `state/planning.py::PlanValidator._derive_signal_edge`
  - `PlanValidator._validate_proofs(proofs: list[DependencyProof], derived: dict[_EdgeKey, set[str]], declared: Mapping[str, set[str]], errors: list[PlanValidationError]) -> None` *(staticmethod)* - Groups the proofs by (parent, child, rule) and compares each group's signal set with the derived one; proofs are required only for declared edges, and every missing proof, unexpected proof or differing signal set is reported as `DEPENDENCY_PROOF_MISMATCH` naming the edge, the rule, the cited signals and the derived signals. · *Called by:* `state/planning.py::PlanValidator.validate`
  - `PlanValidator._validate_cycles(dependencies: dict[str, set[str]], errors: list[PlanValidationError]) -> None` *(staticmethod)* - Runs the shared deterministic cycle finder over the recomputed dependencies and reports each cycle. · *Called by:* `state/planning.py::PlanValidator.validate`
- `render_plan_errors(errors: list[PlanValidationError]) -> str` - Renders the first ten errors with `_render_plan_error`, joined by `; `, plus `<n> more errors omitted`. · *Called by:* `agent/orchestrator/orchestrator.py::Orchestrator.prepare`, `state/harness_coordinator.py::HarnessCoordinator.start_run`, `state/planning.py::PlanValidator.assert_valid`
- `_render_plan_error(error: PlanValidationError) -> str` - `CODE: message` followed by the task and signal ids in parentheses. · *Called by:* `state/planning.py::render_plan_errors`
- `_signal_roles(tasks: list[PlanTask]) -> dict[tuple[str, str], tuple[bool, bool]]` - Maps (task id, signal id) to (produces, consumes) flags from the tasks' signal uses, computed once per validation. · *Called by:* `state/planning.py::PlanValidator._derive_edges`
- `_ancestor_bitsets(dependencies: Mapping[str, set[str]]) -> dict[str, int] | None` - Topological pass over the declared dependencies that gives each task an integer bitset of its transitive ancestors; returns None when the declared graph has a cycle. · *Called by:* `state/planning.py::PlanValidator._validate_dependency_sets`

---

### `state/project_state_engine.py` - mechanical reducer and token-bounded projector over `ProjectState`

*473 lines · depends on: `foundations/contracts.py`, `foundations/errors.py`, `state/project_state_models.py` · used by: `agent/base_agent/agent.py`, `state/project_state_store.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `BaseAgent` calls `ProjectStateReducer.tool_transition` after every governed tool call and `agent_result_transition` at termination; stores apply them with `apply`. Each turn `ProjectStateProjector.project` selects the part of the state that fits the budget.

**Contents**

- **class `ProjectStateProjector`** *(class)* - Selects current-state entries under a token budget, never transcript history. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`
  - `ProjectStateProjector.__init__(policy: ProjectStateProjectionPolicy | None=None) -> None` - Stores the policy (default 2,000 tokens).
  - `ProjectStateProjector.policy() -> ProjectStateProjectionPolicy` *(property)* - Property: the policy. · *Called within this file by:* `state/project_state_engine.py::ProjectStateProjector.__init__`
  - `ProjectStateProjector.project(state: ProjectState) -> ProjectStateView` - Always includes the core (schema, fields, decisions, questions, blockers, last action); then adds artifacts and work items newest-first while they fit, skipping any that do not, and reports omitted counts and over-budget. If the core alone exceeds the budget, no artifacts or work items are included.
- **class `ProjectStateReducer`** *(class)* - Applies only tool outcomes, agent results and authority-checked controller or human events, never model claims.
  - `ProjectStateReducer.tool_transition(call: ToolCall, result: ToolExecutionResult, handle: ToolResultHandle, *, output_summary_max_chars: int=256) -> StateTransition` *(staticmethod)* - Builds a harness `TOOL_OUTCOME` transition from a tool call, its result and result handle, carrying a bounded output summary, a truncated error and any recognised artifact. · *Called by:* `base_agent/agent.py::BaseAgent._apply_tool_outcome_to_project_state`
  - `ProjectStateReducer.agent_result_transition(task_id: str, status: str, result_hash: str) -> StateTransition` *(staticmethod)* - Builds the harness `AGENT_RESULT` transition for a task. · *Called by:* `base_agent/agent.py::BaseAgent._terminate`
  - `ProjectStateReducer.apply(current: ProjectState, transition: StateTransition, *, summary_max_chars: int=2048) -> ProjectState` *(staticmethod)* - Produces the next state: upserts the artifact or blocker (blocked tool results; a tool outcome can never record a COMPLETE artifact), the work item (agent result), decision (human only), question, work item update (controller or harness only), stage change (controller or human only) or artifact status (controller or human only); a missing payload field raises naming the transition, action and field; then enforces per-collection capacity, bumps revision and step count and records the bounded last action.
    - `ProjectStateReducer.apply.required(key: str) -> Any` - Returns a payload value or raises `ValueError` naming the transition kind, the action id and the missing field.
- `_artifact_from_result(call: ToolCall, result: ToolExecutionResult) -> dict[str, Any] | None` - Recognises a succeeded `write_draft`, `edit_draft` or `notebook_edit` result carrying an artifact id and path (nested under `artifact` for the two edit tools) and returns an IN_PROGRESS artifact entry. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.tool_transition`
- `_with_artifact_status(artifacts: list[ProjectArtifactState], relative_path: str, artifact_id: str, status: ArtifactStatus, evidence: list[StateEvidence]) -> list[Projec...` - Returns the artifact list with one artifact's status and evidence replaced; raises when the path is not recorded or the recorded artifact id differs from the one the decision named. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_upsert(items: list[T], key: str, value: T) -> list[T]` - Replaces any entry with the same key and appends the new one last. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_always_closed(_item: object) -> bool` - Eviction predicate for artifacts: all are evictable. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_never_closed(_item: object) -> bool` - Eviction predicate for blockers and questions: none are evictable. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_is_closed_work(item: ProjectWorkItem) -> bool` - Completed, failed and cancelled work items are evictable. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_is_superseded(decision: ProjectDecision) -> bool` - Superseded decisions are evictable. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_within_capacity(project_id: str, collection: str, items: list[T], limit: int, key: str, is_closed: Callable[[T], bool]) -> list[T]` - Evicts the oldest closed entries (never the newest) when a collection exceeds its cap; if too few are evictable raises `PROJECT_STATE_CAPACITY_EXCEEDED` naming the collection, limit and counts. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON. · *Called within this file by:* `state/project_state_engine.py::_bounded_value`, `state/project_state_engine.py::_estimated_tokens`
  - `_canonical_json.default(item: Any) -> Any` - JSON fallback encoder. · *Called within this file by:* `state/project_state_engine.py::_canonical_json`
- `_bounded_value(value: Any, max_chars: int) -> Any` - Returns the value unchanged or a `truncated-state-summary` with a preview of the first N characters. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`, `state/project_state_engine.py::ProjectStateReducer.tool_transition`
- `_bounded_text(value: str | None, max_chars: int) -> str | None` - Truncates text with a marker. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.tool_transition`
- `_estimated_tokens(value: Any) -> int` - Approximate tokens as canonical JSON length divided by four (minimum one). · *Called by:* `state/project_state_engine.py::ProjectStateProjector.project`

**Algorithms & invariants.** Tool outcomes only ever produce IN_PROGRESS artifacts; COMPLETE is reachable only through an `ARTIFACT_STATUS_UPDATED` transition from the controller or a human. No transition closes a blocker or question.

*Module-level names:* `_CLOSED_WORK_STATUSES`

---

### `state/project_state_models.py` - bounded, hash-sealed working-memory contracts

*367 lines · depends on: `foundations/contracts.py`, `foundations/json_limits.py` · used by: `agent/base_agent/agent.py`, `agent/graph_agent_executor.py`, `agent/model.py`, `agent/runtime.py`, `mcp/agent_tools.py`, `mcp/project_state_tools.py`, `state/controller_runtime.py`, `state/project_state_engine.py` (+2 more) · re-exported at the package root: 21 name(s)*

**Role in the workflow.** `ProjectState` is the normal working memory a `BaseAgent` sees each turn (through `ProjectStateProjector`). It is current-state only: events and raw tool output stay as audit evidence and are not replayed to the model.

**Contents**

- **class `StateAuthority`** *(enum; bases: StrEnum)* - Who may change state: harness, controller or human.
  - members: `HARNESS`, `CONTROLLER`, `HUMAN`
- **class `DecisionStatus`** *(enum; bases: StrEnum)* - open, locked or superseded. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - members: `OPEN`, `LOCKED`, `SUPERSEDED`
- **class `WorkItemStatus`** *(enum; bases: StrEnum)* - not-started, in-progress, completed, blocked, failed or cancelled. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - members: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, `BLOCKED`, `FAILED`, `CANCELLED`
- **class `ArtifactStatus`** *(enum; bases: StrEnum)* - not-started, in-progress, complete, invalid or blocked.
  - members: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETE`, `INVALID`, `BLOCKED`
- **class `QuestionOwner`** *(enum; bases: StrEnum)* - human or controller. · *Instantiated by:* `mcp/project_state_tools.py::register_project_state_tools.open_project_question`, `state/project_state_engine.py::ProjectStateReducer.apply`
  - members: `HUMAN`, `CONTROLLER`
- **class `StateEvidence`** *(pydantic model; bases: StrictModel)* - Small provenance reference (id, kind, optional content hash and spans), never raw output. · *Instantiated by:* `mcp/project_state_tools.py::register_project_state_tools.open_project_question`, `mcp/project_state_tools.py::register_project_state_tools.record_human_project_decision`, `state/controller_runtime.py::ControllerRuntime._reduce_graph_result`, `state/controller_runtime.py::ControllerRuntime.record_node_result` (+2 more)
  - fields: `evidence_id`, `kind`, `content_hash`, `source_spans`
- **class `StageStateSchema`** *(pydantic model; bases: StrictModel)* - Declared stage schema with the required field ids; a model cannot invent a stage field. · *Instantiated by:* `base_agent/agent.py::BaseAgent._stage_schema_for`, `state/controller_runtime.py::ControllerRuntime.create_controller`
  - fields: `schema_id`, `stage`, `required_field_ids`
  - `StageStateSchema.field_ids_are_unique(values: list[str]) -> list[str]` *(validator, classmethod)* - Validator: required field ids are unique.
- **class `StageStateField`** *(pydantic model; bases: StrictModel)* - A stage field value with 1-32 evidence references.
  - fields: `field_id`, `value`, `evidence`
  - `StageStateField.value_is_bounded(value: Any) -> Any` *(validator, classmethod)* - Validator: the field value nests at most 64 levels.
- **class `ProjectDecision`** *(pydantic model; bases: StrictModel)* - A decision with authority and status, at most 4,096 characters. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - fields: `decision_id`, `content`, `status`, `authority`, `evidence`
- **class `OpenQuestion`** *(pydantic model; bases: StrictModel)* - An unresolved question with an owner. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - fields: `question_id`, `content`, `owner`, `evidence`
- **class `ProjectBlocker`** *(pydantic model; bases: StrictModel)* - A blocker on a subject with a reason. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - fields: `blocker_id`, `subject_id`, `reason`, `evidence`
- **class `ProjectArtifactState`** *(pydantic model; bases: StrictModel)* - Status of one artifact path.
  - fields: `relative_path`, `status`, `artifact_id`, `evidence`
- **class `ProjectWorkItem`** *(pydantic model; bases: StrictModel)* - Status and owner of one work item. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - fields: `work_item_id`, `status`, `owner`, `evidence`
- **class `StateAction`** *(pydantic model; bases: StrictModel)* - The last recorded action with a bounded summary. · *Instantiated by:* `state/project_state_engine.py::ProjectStateReducer.apply`
  - fields: `action_id`, `kind`, `status`, `evidence`, `summary`
  - `StateAction.summary_is_bounded(value: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the summary nests at most 64 levels and its canonical JSON is at most 4,096 characters.
- **class `ProjectState`** *(pydantic model; bases: StrictModel)* - The whole bounded state: stage schema, fields, artifacts, decisions, questions, blockers, work items, last action, step count and state hash, with per-collection caps.
  - fields: `schema_version`, `project_id`, `revision`, `stage_schema`, `stage_fields`, `artifacts`, `decisions`, `open_questions`, `blocked`, `work_items`, `last_action`, `step_count`, `state_hash`
  - `ProjectState.state_is_well_formed() -> ProjectState` *(validator)* - Validator: unique ids per collection, schema stage matches, required stage fields present and the stored hash equals the recomputed one.
  - `ProjectState.stage() -> str` *(property)* - Property: the stage named by the schema. · *Called by:* `base_agent/agent.py::BaseAgent._stage_schema_for`, `orchestrator/orchestrator.py::Orchestrator._emit`, `orchestrator/orchestrator.py::Orchestrator._profile_for`, `orchestrator/orchestrator.py::Orchestrator.submit_for_approval` (+8 more)
  - `ProjectState.model_view() -> dict[str, Any]` - Full JSON dump (the unbudgeted view). · *Called by:* `base_agent/agent.py::BaseAgent._terminate`
- **class `ProjectStateProjectionPolicy`** *(pydantic model; bases: StrictModel)* - Token budget (default 2,000) and per-action summary size for one model turn. · *Instantiated by:* `mcp/agent_tools.py::register_agent_tools.run_agent_task`, `state/project_state_engine.py::ProjectStateProjector.__init__`
  - fields: `token_budget`, `action_output_summary_chars`
- **class `ProjectStateView`** *(pydantic model; bases: StrictModel)* - Budgeted view with omitted counts and an over-budget flag. · *Instantiated by:* `state/project_state_engine.py::ProjectStateProjector.project`
  - fields: `project_id`, `revision`, `stage_schema`, `stage_fields`, `artifacts`, `decisions`, `open_questions`, `blocked`, `work_items`, `last_action`, `step_count`, `state_hash`, `estimated_tokens`, `token_budget`, `omitted_artifact_count`, `omitted_work_item_count`, ... (+1)
- **class `StateTransitionKind`** *(enum; bases: StrEnum)* - tool-outcome, agent-result, human-decision, question-opened, work-item-updated or stage-changed.
  - members: `TOOL_OUTCOME`, `AGENT_RESULT`, `HUMAN_DECISION`, `QUESTION_OPENED`, `WORK_ITEM_UPDATED`, `STAGE_CHANGED`, `ARTIFACT_STATUS_UPDATED`
- **class `StateTransition`** *(pydantic model; bases: StrictModel)* - A typed mutation request: kind, actor, action id, payload and evidence. · *Instantiated by:* `mcp/project_state_tools.py::register_project_state_tools.open_project_question`, `mcp/project_state_tools.py::register_project_state_tools.record_human_project_decision`, `state/controller_runtime.py::ControllerRuntime._reduce_graph_result`, `state/controller_runtime.py::ControllerRuntime.record_node_result` (+2 more)
  - fields: `kind`, `actor`, `action_id`, `payload`, `evidence`
  - `StateTransition.payload_is_bounded(payload: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the transition payload nests at most 64 levels.
- **class `ProjectStateEvent`** *(pydantic model; bases: StrictModel)* - Hash-chained audit record of one revision: previous and new state hash and its own hash. · *Instantiated by:* `state/project_state_store.py::_make_event`
  - fields: `schema_version`, `project_id`, `revision`, `kind`, `actor`, `action_id`, `evidence`, `previous_state_hash`, `state_hash`, `event_hash`
  - `ProjectStateEvent.event_hash_matches() -> ProjectStateEvent` *(validator)* - Validator: the event hash equals the recomputed canonical hash.
- **class `ProjectStateRepository`** *(Protocol; bases: Protocol)* - Protocol of a project-state store: ensure, load, apply.
  - `ProjectStateRepository.ensure(project_id: str, stage_schema: StageStateSchema) -> ProjectState` - Create the state at revision 0 if it does not exist.
  - `ProjectStateRepository.load(project_id: str) -> ProjectState` - Return the current state.
  - `ProjectStateRepository.apply(project_id: str, transition: StateTransition) -> ProjectState` - Apply a transition and return the new state.
- `make_project_state(*, project_id: str, revision: int, stage_schema: StageStateSchema, stage_fields: list[StageStateField] | None=None, artifacts: list[ProjectArtifac...` - Builds a state and seals it by computing its hash. · *Called by:* `state/project_state_engine.py::ProjectStateReducer.apply`, `state/project_state_store.py::InMemoryProjectStateStore.ensure`
- `project_state_hash(state: ProjectState) -> str` - Hash of a state's canonical fields (the stored hash excluded). · *Called by:* `state/project_state_models.py::ProjectState.state_is_well_formed`
- `project_state_hash_from_payload(payload: dict[str, Any]) -> str` - Same, from a plain dictionary. · *Called by:* `state/project_state_models.py::make_project_state`, `state/project_state_models.py::project_state_hash`
- `_event_hash(project_id: str, revision: int, kind: StateTransitionKind, actor: StateAuthority, action_id: str, evidence: list[StateEvidence], previous_state_ha...` - Hash of an event's canonical fields. · *Called within this file by:* `state/project_state_models.py::ProjectStateEvent.event_hash_matches`
- `_ensure_unique(items: list[T], key: str, label: str) -> None` - Raises `<label> must be unique` if a key repeats. · *Called by:* `state/project_state_models.py::ProjectState.state_is_well_formed`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON, with pydantic models dumped and unknown objects stringified. · *Called within this file by:* `state/project_state_models.py::StateAction.summary_is_bounded`, `state/project_state_models.py::_event_hash`, `state/project_state_models.py::project_state_hash_from_payload`
  - `_canonical_json.default(item: Any) -> Any` - JSON fallback encoder for models and other objects. · *Called within this file by:* `state/project_state_models.py::_canonical_json`

**Algorithms & invariants.** `_canonical_json` is duplicated in this file, `project_state_engine.py` and `project_state_store.py`.

*Module-level names:* `MAX_STAGE_FIELDS`, `MAX_ARTIFACTS`, `MAX_DECISIONS`, `MAX_OPEN_QUESTIONS`, `MAX_BLOCKERS`, `MAX_WORK_ITEMS`

---

### `state/project_state_store.py` - in-memory and file-backed project-state stores with a hash-chained audit trail

*249 lines · depends on: `foundations/atomic_io.py`, `state/project_state_engine.py`, `state/project_state_models.py` · used by: `agent/base_agent/agent.py`, `agent/runtime.py`, `developer_tools/inspect.py`, `mcp/_shared.py`, `mcp/server.py`, `state/controller_runtime.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** `BaseAgent` (through the harness), `ControllerRuntime` and the MCP project-state tools call `ensure`, `load` and `apply`; `FileProjectStateStore` writes one event file per revision and one state file per project.

**Contents**

- **class `InMemoryProjectStateStore`** *(class)* - Dictionary-backed repository for tests and bounded tasks. · *Instantiated by:* `base_agent/agent.py::BaseAgent.__init__`
  - `InMemoryProjectStateStore.__init__() -> None` - Starts with empty state and event maps and a re-entrant lock that every public method holds, so one instance is safe across threads.
  - `InMemoryProjectStateStore.ensure(project_id: str, stage_schema: StageStateSchema) -> ProjectState` - Creates a revision-0 state if the project is unknown and returns it.
  - `InMemoryProjectStateStore.load(project_id: str) -> ProjectState` - Returns the cached state or raises `Project state "<id>" is unknown.`. · *Called within this file by:* `state/project_state_store.py::InMemoryProjectStateStore.apply`, `state/project_state_store.py::InMemoryProjectStateStore.events`
  - `InMemoryProjectStateStore.apply(project_id: str, transition: StateTransition, *, summary_max_chars: int=2048) -> ProjectState` - Runs the reducer, builds the hash-chained event, stores both.
  - `InMemoryProjectStateStore.events(project_id: str) -> tuple[ProjectStateEvent, ...]` - The project's events so far.
- **class `FileProjectStateStore`** *(class; bases: InMemoryProjectStateStore)* - Persistent store under `<run_root>/.agent-project-state/`: `<sha256(project_id)>.json` and `<sha256>/<revision>.json`. · *Instantiated by:* `agent/runtime.py::AgentRuntimeServices.open`, `mcp/server.py::create_mcp_server`, `state/controller_runtime.py::ControllerRuntime.__init__`
  - `FileProjectStateStore.__init__(run_root: Path, *, read_only: bool=False) -> None` - Creates the directory; with `read_only=True` it creates nothing and `ensure` (for a missing state) and `apply` raise.
  - `FileProjectStateStore.ensure(project_id: str, stage_schema: StageStateSchema) -> ProjectState` - Loads an existing file, else creates and writes revision 0.
  - `FileProjectStateStore.load(project_id: str) -> ProjectState` - Returns the cached state while the state file's fingerprint is unchanged; if another instance or process wrote it, reads the file, loads the events and verifies the history before returning. · *Called within this file by:* `state/project_state_store.py::FileProjectStateStore.apply`, `state/project_state_store.py::FileProjectStateStore.ensure`
  - `FileProjectStateStore.apply(project_id: str, transition: StateTransition, *, summary_max_chars: int=2048) -> ProjectState` - Under the store lock: loads the current state (refreshing a stale cache), reduces, writes the event file, then the state file, then updates its cache and fingerprint. Refuses on a read-only store. There is no cross-process lock.
  - `FileProjectStateStore._require_writable(operation: str) -> None` - Raises `RuntimeError` naming the store root and the operation on a read-only store.
  - `FileProjectStateStore._fingerprint(project_id: str) -> tuple[int, int, int] | None` - Identity of the project's state file (inode, modification time, size) or None. · *Called by:* `state/project_state_store.py::FileProjectStateStore.apply`, `state/project_state_store.py::FileProjectStateStore.ensure`, `state/project_state_store.py::FileProjectStateStore.load`
  - `FileProjectStateStore._state_path(project_id: str) -> Path` - State file path (project id hashed). · *Called by:* `memory/episode_store.py::FileEpisodeStore.__init__`, `memory/episode_store.py::FileEpisodeStore.persist`, `state/project_state_store.py::FileProjectStateStore._fingerprint`, `state/project_state_store.py::FileProjectStateStore.apply` (+2 more)
  - `FileProjectStateStore._event_path(project_id: str, revision: int) -> Path` - Per-revision event file path (creating the folder). · *Called by:* `state/project_state_store.py::FileProjectStateStore.apply`
  - `FileProjectStateStore._read_events(project_id: str, *, through_revision: int) -> list[ProjectStateEvent]` - Reads and validates the event files up to `through_revision` in revision order (a file whose name is not a revision number raises); an event beyond the state's revision, left by an interrupted write, is ignored. · *Called within this file by:* `state/project_state_store.py::FileProjectStateStore.load`
  - `FileProjectStateStore._verify_history(project_id: str) -> None` - Requires revision equal to the event count, contiguous revisions, an unbroken previous-hash chain and a last event hash equal to the state hash. · *Called by:* `state/project_state_store.py::FileProjectStateStore.load`
  - `FileProjectStateStore._write_json(path: Path, value: dict[str, Any]) -> None` *(staticmethod)* - Atomic canonical JSON write through a unique temporary file. · *Called by:* `state/project_state_store.py::FileProjectStateStore.apply`, `state/project_state_store.py::FileProjectStateStore.ensure`
- `_make_event(previous: ProjectState, current: ProjectState, transition: StateTransition) -> ProjectStateEvent` - Builds the sealed event linking the previous and new state hashes. · *Called by:* `state/project_state_store.py::FileProjectStateStore.apply`, `state/project_state_store.py::InMemoryProjectStateStore.apply`
- `_safe_id(value: str) -> str` - SHA-256 of the project id, used as a filename. · *Called by:* `state/project_state_store.py::FileProjectStateStore._event_path`, `state/project_state_store.py::FileProjectStateStore._read_events`, `state/project_state_store.py::FileProjectStateStore._state_path`
- `_canonical_json(value: Any) -> str` - Sorted-key compact JSON. · *Called within this file by:* `state/project_state_store.py::FileProjectStateStore._write_json`
  - `_canonical_json.default(item: Any) -> Any` - JSON fallback encoder. · *Called within this file by:* `state/project_state_store.py::_canonical_json`

**Algorithms & invariants.** The event file is written before the state file. A crash between them leaves one event beyond the state's revision; `load` reads only the events up to the state's revision, so it still succeeds, and the next `apply` overwrites the orphan. A re-entrant lock makes one store instance safe across threads; there is still no cross-process lock.

---

### `state/run_state_store.py` - crash-safe run persistence as a fixed-state snapshot plus a growing-state sidecar

*405 lines · depends on: `foundations/atomic_io.py`, `foundations/identifiers.py`, `state/coordination_records.py` · used by: `state/harness_coordinator.py` · re-exported at the package root: 1 name(s)*

**Role in the workflow.** Each save appends only the new history entries (events, route decisions, conflicts, discoveries, values, lateral requests) to `<run>.history.jsonl` with fsync, then atomically replaces `<run>.json`, which names the exact sidecar prefix it owns by entry count and hash. A crash between the two leaves an unpublished suffix that the next save truncates (by committed byte length) and that a load ignores, so recovery lands on the last complete generation. Loading replays the named prefix, merges it back and re-verifies `run_hash`. Run ids are validated before any path is built, and `<run>.approvals.json` holds the run's approval requests.

**Contents**

- `_replace_with_retry(temporary: Path, target: Path, *, attempts: int=5) -> None` - Atomic replace with bounded retries for transient Windows handle errors. · *Called within this file by:* `state/run_state_store.py::RunStateStore.save`
- **class `RunStateStore`** *(class)* - File store under `<run_root>/.agent-runs/`. · *Instantiated by:* `state/harness_coordinator.py::HarnessCoordinator.__init__`
  - `RunStateStore.__init__(root: Path) -> None` - Creates the directory and the per-run cursor cache.
  - `RunStateStore.reserve_run_id(*, start: int=1) -> tuple[str, int]` - Reserves `run-N` from `start` through exclusive claim files, skipping ids whose record exists; returns the id and the next number to try. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator.start_run`
  - `RunStateStore.save(record: RunRecord) -> None` - Truncates any uncommitted sidecar suffix to the committed byte length, restarts the sidecar if the in-memory history shrank (a reused run id), appends the new entries (fsync) while extending the running hash, writes the emptied snapshot atomically through a unique temporary file and updates the cursors.
  - `RunStateStore.load(run_id: str) -> RunRecord` - Reads the snapshot; legacy snapshots are verified directly; otherwise replays exactly the named sidecar prefix, checks count and hash, merges and verifies `run_hash`.
  - `RunStateStore.exists(run_id: str) -> bool` - True if a snapshot file exists.
  - `RunStateStore.fingerprint(run_id: str) -> tuple[int, int, int] | None` - Cheap identity of a run's snapshot file (inode, modification time, size) or None; callers compare it to detect that another writer replaced the file. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator._save`, `state/harness_coordinator.py::HarnessCoordinator.get_run_state`, `state/project_state_store.py::FileProjectStateStore.load`
  - `RunStateStore.approvals_path(run_id: str) -> Path` - Path of `<run>.approvals.json` for a validated run id. · *Called by:* `state/harness_coordinator.py::HarnessCoordinator._approval_registry`
  - `RunStateStore._record_path(run_id: str) -> Path` - Snapshot path for a validated run id; an invalid id raises before any path is built.
  - `RunStateStore._history_path(run_id: str) -> Path` - Sidecar path for a run. · *Called by:* `state/run_state_store.py::RunStateStore._counts_for`, `state/run_state_store.py::RunStateStore.load`, `state/run_state_store.py::RunStateStore.save`
  - `RunStateStore._counts_for(run_id: str) -> _PersistedHistoryCounts` - Cursor for a run: the cached one, else rebuilt from the verified durable boundary. · *Called by:* `state/run_state_store.py::RunStateStore.save`
  - `RunStateStore._discard_uncommitted_history(run_id: str, path: Path, counts: _PersistedHistoryCounts) -> None` - Truncates the sidecar to the committed byte length when it is longer; raises naming the run when it is shorter. · *Called by:* `state/run_state_store.py::RunStateStore.save`
- **class `_PersistedHistoryCounts`** *(dataclass)* - Cursor describing how much of each growing collection is already in the sidecar. · *Instantiated by:* `state/run_state_store.py::RunStateStore._counts_for`, `state/run_state_store.py::RunStateStore.load`, `state/run_state_store.py::RunStateStore.save`, `state/run_state_store.py::_diff_history` (+1 more)
  - fields: `entry_count`, `events`, `route_decisions`, `conflicts`, `discovery_keys`, `value_keys`, `lateral_dependency_counts`, `byte_length`, `digest`
- `_emptied_history(graph: dict[str, Any]) -> dict[str, Any]` - Copy of the graph with every growing collection cleared (shallow copy: nodes, edges, statuses and results are shared by reference). · *Called by:* `state/run_state_store.py::RunStateStore.save`
- `_history_shrunk(graph: dict[str, Any], counts: _PersistedHistoryCounts) -> bool` - True when any growing collection is now smaller than what was persisted. · *Called by:* `state/run_state_store.py::RunStateStore.save`
- `_diff_history(graph: dict[str, Any], counts: _PersistedHistoryCounts) -> tuple[list[dict[str, Any]], _PersistedHistoryCounts]` - New sidecar entries since a cursor, with the updated cursor. · *Called by:* `state/run_state_store.py::RunStateStore.save`
- `_replay_history(path: Path, *, entry_limit: int | None=None) -> tuple[dict[str, Any], _PersistedHistoryCounts, str]` - Rebuilds the growing collections from a bounded sidecar prefix, returning them, the cursor (including byte length and running digest) and the prefix hash. · *Called by:* `state/run_state_store.py::RunStateStore._counts_for`, `state/run_state_store.py::RunStateStore.load`
- `_encode_entry(entry: dict[str, Any]) -> bytes` - Canonical (sorted-key, compact) JSON line for one history entry. · *Called by:* `state/run_state_store.py::_append_history`, `state/run_state_store.py::_replay_history`
- `_append_history(path: Path, entries: list[dict[str, Any]], counts: _PersistedHistoryCounts) -> None` - Appends the encoded entries to the sidecar, fsyncs, and advances the cursor's running digest and byte length. · *Called by:* `state/run_state_store.py::RunStateStore.save`
- `_read_history_entries(path: Path, *, entry_limit: int | None=None) -> tuple[list[dict[str, Any]], int]` - Reads up to `entry_limit` non-blank JSON lines and returns them with the byte offset where reading stopped; a line that is not valid JSON raises naming the entry. · *Called by:* `state/run_state_store.py::RunStateStore._discard_uncommitted_history`, `state/run_state_store.py::_replay_history`
- `_write_json_atomic_candidate(path: Path, content: str) -> None` - Writes and fsyncs a temporary file ready for atomic replacement. · *Called by:* `state/run_state_store.py::RunStateStore.save`
- `_verify_run_record(record: RunRecord) -> None` - Recomputes `run_hash` and raises `Run "<id>" failed integrity verification.`. · *Called by:* `state/run_state_store.py::RunStateStore.load`
- `_merge_history(snapshot_graph: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]` - Puts replayed growing collections back into a history-emptied graph. · *Called by:* `state/run_state_store.py::RunStateStore.load`

**Algorithms & invariants.** Each cursor keeps the sidecar's committed byte length and a running SHA-256, so a save appends and hashes only the new entries instead of re-reading the file; the cursor is rebuilt once from the verified prefix after a restart. A sidecar shorter than its committed length raises (it was truncated outside the store). There is no lock: `HarnessCoordinator` detects a change made by another writer through `fingerprint` and refuses to overwrite it, but two store instances saving at the same instant can still truncate each other's sidecar suffix. Remaining save cost is the snapshot itself, which grows with the number of nodes.

---

### `state/shared_state.py` - typed lateral-state payloads, exact routing references and provenance records

*345 lines · depends on: `foundations/atomic_io.py`, `foundations/contracts.py`, `foundations/identifiers.py`, `foundations/json_limits.py` · used by: `agent/orchestrator/models.py`, `agent/orchestrator/orchestrator.py`, `mcp/controller_tools.py`, `state/controller_runtime.py`, `state/elastic.py`, `state/graph.py`, `state/graph_models.py`, `state/harness_coordinator.py` (+3 more) · re-exported at the package root: 13 name(s)*

**Role in the workflow.** Workers never exchange conversation. A producer publishes a closed, source-backed `ExploratoryDiscovery` (or an immutable `SharedStateWrite`) into the graph; routing references on the discovery are matched exactly against the routing refs of graph nodes. `ProvenanceRecord`s hash what a node consumed and produced, and `ProvenanceContractGate` checks them at fan-in. The legacy `RunSharedState` and `SharedStateStore` remain for compatibility only; `StateGraph` now carries this state.

**Contents**

- **class `DiscoveryKind`** *(enum; bases: StrEnum)* - Currently only `exploratory`.
  - members: `EXPLORATORY`
- **class `SharedSubstrateSnapshot`** *(pydantic model; bases: StrictModel)* - Read-only source snapshot (id, version, content hash, artifact ids) that all lateral data must reference. · *Instantiated by:* `state/graph_models.py::GraphSharedState.unbound`
  - fields: `snapshot_id`, `version`, `content_hash`, `artifact_ids`, `read_only`
- **class `DiscoveryRoutingRefs`** *(pydantic model; bases: StrictModel)* - Exact requirement, signal, task and schema identifiers used for deterministic routing.
  - fields: `requirement_ids`, `signal_ids`, `task_ids`, `schema_ids`
  - `DiscoveryRoutingRefs.identifiers_are_unique() -> DiscoveryRoutingRefs` *(validator)* - Validator: each list has no blank or duplicate entries.
  - `DiscoveryRoutingRefs.any_identifiers() -> set[str]` - Union of all four identifier lists. · *No in-package callers (public API, entry point, or protocol hook).*
- **class `DiscoveryRouteStatus`** *(enum; bases: StrEnum)* - accepted, rejected or late-discovery.
  - members: `ACCEPTED`, `REJECTED`, `LATE_DISCOVERY`
- **class `DiscoveryRouteDecision`** *(pydantic model; bases: StrictModel)* - Ledger row recording where a discovery was routed (or why not) under which snapshot and policy version. · *Instantiated by:* `state/graph.py::StateGraph._append_route_decision`
  - fields: `discovery_episode_id`, `consumer_node_id`, `status`, `reason`, `routing_snapshot_id`, `routing_policy_version`
- **class `DiscoveryRoutingIndex`** *(pydantic model; bases: StrictModel)* - Postings from each exact reference to the node ids that declare it, rebuilt from node metadata. · *Instantiated by:* `state/graph.py::StateGraph._rebuild_routing_index`
  - fields: `requirement_postings`, `signal_postings`, `task_postings`, `schema_postings`
- **class `ExploratoryDiscovery`** *(pydantic model; bases: StrictModel)* - A closed discovery: producer node, owner, snapshot id and version, source spans, description, payload, provenance hash and affected refs.
  - fields: `episode_id`, `producer_node_id`, `owner_id`, `snapshot_id`, `snapshot_version`, `source_spans`, `description`, `payload`, `provenance_hash`, `affected_refs`, `closed`, `kind`
  - `ExploratoryDiscovery.payload_is_bounded(payload: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the discovery payload nests at most 64 levels.
- **class `LateralDependencyRequest`** *(pydantic model; bases: StrictModel)* - A consumer node/action asking to depend on a published discovery. · *Instantiated by:* `state/graph.py::StateGraph._route_discovery`
  - fields: `consumer_node_id`, `consumer_action_id`, `discovery_episode_id`, `reason`
- **class `SharedStateWrite`** *(pydantic model; bases: StrictModel)* - An immutable keyed value published by a completed producer node.
  - fields: `schema_version`, `producer_node_id`, `key`, `value`, `provenance_hash`
  - `SharedStateWrite.value_is_bounded(value: dict[str, Any]) -> dict[str, Any]` *(validator, classmethod)* - Validator: the written value nests at most 64 levels.
- **class `ProvenanceRecord`** *(pydantic model; bases: StrictModel)* - Hash-sealed record of one node's inputs, source snapshots and spans, tool-record hashes, artifacts, and result hash and schema version. · *Instantiated by:* `state/shared_state.py::make_provenance_record`
  - fields: `schema_version`, `node_id`, `input_hashes`, `source_snapshot_ids`, `source_spans`, `tool_record_hashes`, `artifact_ids`, `result_schema_version`, `result_hash`, `hash`
  - `ProvenanceRecord.hash_matches_payload() -> ProvenanceRecord` *(validator)* - Validator: the stored hash equals the hash recomputed from the canonical fields.
- **class `ProvenanceGateDecision`** *(pydantic model; bases: StrictModel)* - Accepted flag plus the rejection reasons. · *Instantiated by:* `state/shared_state.py::ProvenanceContractGate.verify`
  - fields: `accepted`, `reasons`
- **class `ProvenanceContractGate`** *(class)* - Mechanical fan-in acceptance test. · *Instantiated by:* `state/controller_runtime.py::ControllerRuntime.verify_provenance_contract`
  - `ProvenanceContractGate.verify(records: list[ProvenanceRecord], *, expected_snapshot_ids: set[str], required_schema_version: str) -> ProvenanceGateDecision` - Rejects duplicate producer node ids, wrong result schema versions and records that lack an expected source snapshot id; accepts only when there are no reasons.
- **class `RunSharedState`** *(class)* - Legacy standalone container for discoveries, writes and lateral requests; superseded by `GraphSharedState`.
  - `RunSharedState.__init__(snapshot: SharedSubstrateSnapshot) -> None` - Starts empty for one substrate snapshot.
  - `RunSharedState.publish_discovery(discovery: ExploratoryDiscovery) -> None` - Accepts a closed discovery for the same snapshot id and version that is not already published. · *Called within this file by:* `state/shared_state.py::RunSharedState.from_snapshot`
  - `RunSharedState.request_lateral_dependency(request: LateralDependencyRequest) -> ExploratoryDiscovery` - Requires a published discovery and a consumer other than its producer; records the request. · *Called within this file by:* `state/shared_state.py::RunSharedState.from_snapshot`
  - `RunSharedState.write(state_write: SharedStateWrite) -> None` - Stores a keyed value; keys are immutable once written. · *Called by:* `memory/context_projection.py::FileToolResultJournal.record`, `observability/audit_log.py::AuditTranscriptStore.append`, `state/orchestration.py::ControllerStateStore._append_events`, `state/orchestration.py::ControllerStateStore._write_events` (+7 more)
  - `RunSharedState.get_discovery(episode_id: str) -> ExploratoryDiscovery | None` - Looks a discovery up by episode id. · *No in-package callers (public API, entry point, or protocol hook).*
  - `RunSharedState.snapshot_state() -> dict[str, Any]` - Sorted JSON-able dump of substrate, discoveries, writes and lateral requests. · *Called by:* `state/shared_state.py::SharedStateStore.save`
  - `RunSharedState.from_snapshot(payload: dict[str, Any]) -> RunSharedState` *(classmethod)* - Rebuilds the container by replaying the dump through the validating methods.
- **class `SharedStateStore`** *(class)* - Legacy file persistence under `.agent-shared-state/`.
  - `SharedStateStore.__init__(root: Path) -> None` - Creates the directory.
  - `SharedStateStore.save(run_id: str, state: RunSharedState) -> None` - Atomic JSON write of a run's shared state (validated run id, unique temporary file).
  - `SharedStateStore.load(run_id: str) -> RunSharedState` - Reads and validates a run's shared state; an invalid run id, an unknown run or malformed content raises `ValueError`.
- `make_provenance_record(*, node_id: str, input_hashes: list[str], source_snapshot_ids: list[str], source_spans: list[str], tool_record_hashes: list[str], artifact_ids: li...` - Builds a record, computing the result hash and the record hash. · *No in-package callers (public API, entry point, or protocol hook).*
- `canonical_hash(value: Any) -> str` - SHA-256 of canonical JSON (sorted keys, no spaces, `default=str`); the SDK's general-purpose content hash for state records. · *Called by:* `state/controller_runtime.py::_graph_result_hash`, `state/shared_state.py::make_provenance_record`, `state/shared_state.py::provenance_hash`
- `provenance_hash(node_id: str, input_hashes: list[str], source_snapshot_ids: list[str], source_spans: list[str], tool_record_hashes: list[str], artifact_ids: list[...` - Hash of the record's fields with every list sorted, so list order never changes identity. · *Called by:* `state/controller_runtime.py::ControllerRuntime._reduce_graph_result`, `state/controller_runtime.py::ControllerRuntime.record_node_result`, `state/shared_state.py::ProvenanceRecord.hash_matches_payload`, `state/shared_state.py::make_provenance_record`

**Algorithms & invariants.** `RunSharedState` and `SharedStateStore` are only reachable through the package root re-export; nothing in the SDK instantiates them.

---

### `state/stage_gates.py` - deterministic stage-completeness gate

*94 lines · depends on: `foundations/contracts.py`, `state/project_state_models.py` · used by: `state/controller_runtime.py` · re-exported at the package root: 3 name(s)*

**Role in the workflow.** `ControllerRuntime.evaluate_stage_completeness` calls it; a negative result sends the controller into bounded repair.

**Contents**

- **class `StageCompletenessPolicy`** *(pydantic model; bases: StrictModel)* - Policy id, stage, required artifact paths, required work-item ids and whether open questions and blockers reject.
  - fields: `policy_id`, `stage`, `required_artifact_paths`, `required_work_item_ids`, `reject_open_questions`, `reject_blockers`
- **class `StageCompletenessDecision`** *(pydantic model; bases: StrictModel)* - Complete flag, reasons and the ids that failed each check. · *Instantiated by:* `state/stage_gates.py::StageCompletenessGate.evaluate`
  - fields: `complete`, `policy_id`, `stage`, `reasons`, `missing_field_ids`, `incomplete_artifact_paths`, `incomplete_work_item_ids`, `open_question_ids`, `blocker_ids`
- **class `StageCompletenessGate`** *(class)* - Checks finite declared requirements without interpreting free text. · *Instantiated by:* `state/controller_runtime.py::ControllerRuntime.evaluate_stage_completeness`
  - `StageCompletenessGate.evaluate(state: ProjectState, policy: StageCompletenessPolicy) -> StageCompletenessDecision` - Fails on a stage mismatch; otherwise reports missing required stage fields, required artifacts that are not COMPLETE, required work items that are not COMPLETED and, if configured, any open question or blocker.

**Algorithms & invariants.** Nothing in the SDK ever sets an artifact to COMPLETE or removes a question or blocker, so a policy that requires them can only pass for states built by hand.
