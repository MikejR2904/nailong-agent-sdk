# `mcp/` - the MCP server that exposes the SDK to a host, and the client that consumes external MCP tools

Two directions. **Server** (`server.py` plus eight `*_tools.py` groups): `create_mcp_server` builds one `McpContext` of durable stores and registers 53 tools that let a TypeScript or other host drive the Python runtime: validate and run agent tasks on their declared models, manage project state, validate plans and start graph runs, drive the controller lifecycle, run the specification pipeline and Gate 1, inspect telemetry and audit logs, and create Git version locks. Every tool is a plain `async` function that returns `{ok: true, ...}` or `{ok: false, errors: [...]}` (pydantic field errors as `{location, message, type}`, other failures as `{message, type}` naming the exception class); only `run_agent_task` calls a model, through the provider the operator configured for the definition's binding. The server listens on loopback only and has no authentication, so the authority a caller claims (for example `human`) is the host's responsibility. **Client** (`client.py`, `client_types.py`, `client_bridge.py`): `McpClientManager` connects this SDK to external MCP servers and `mcp_tools_as_extensions` opts chosen external tools into the governed tool registry under capability and approval control.

| File | Lines | Role |
|---|---:|---|
| [`mcp/__init__.py`](#mcp__init__py---package-marker-for-the-mcp-surface) | 3 | package marker for the MCP surface |
| [`mcp/_shared.py`](#mcp_sharedpy---shared-context-and-error-formatting-for-every-tool-group) | 112 | shared context and error formatting for every tool group |
| [`mcp/agent_tools.py`](#mcpagent_toolspy---mcp-tools-for-agent-contract-validation-context-assembly-and-scripted-runs) | 219 | MCP tools for agent contract validation, context assembly and scripted runs |
| [`mcp/client.py`](#mcpclientpy---outbound-mcp-client-lifecycle) | 400 | outbound MCP client lifecycle |
| [`mcp/client_bridge.py`](#mcpclient_bridgepy---opt-in-bridge-from-external-mcp-tools-to-governed-harness-tools) | 55 | opt-in bridge from external MCP tools to governed harness tools |
| [`mcp/client_types.py`](#mcpclient_typespy---typed-configuration-and-status-for-the-outbound-mcp-client) | 83 | typed configuration and status for the outbound MCP client |
| [`mcp/controller_tools.py`](#mcpcontroller_toolspy---mcp-tools-for-the-deterministic-controller) | 217 | MCP tools for the deterministic controller |
| [`mcp/git_tools.py`](#mcpgit_toolspy---mcp-tools-for-local-git-inspection-and-specification-version-locks) | 109 | MCP tools for local Git inspection and specification version locks |
| [`mcp/orchestration_tools.py`](#mcporchestration_toolspy---mcp-tools-for-plan-validation-graph-runs-and-orchestration-compilation) | 115 | MCP tools for plan validation, graph runs and orchestration compilation |
| [`mcp/project_state_tools.py`](#mcpproject_state_toolspy---mcp-tools-for-project-working-memory) | 221 | MCP tools for project working memory |
| [`mcp/run_tools.py`](#mcprun_toolspy---mcp-tools-for-graph-run-state-cancellation-approvals-and-resumption) | 59 | MCP tools for graph run state, cancellation, approvals and resumption |
| [`mcp/server.py`](#mcpserverpy---mcp-server-factory-and-loopback-entry-point) | 159 | MCP server factory and loopback entry point |
| [`mcp/specification_tools.py`](#mcpspecification_toolspy---mcp-tools-for-the-specification-pipeline-and-gate-1) | 132 | MCP tools for the specification pipeline and Gate 1 |
| [`mcp/telemetry_tools.py`](#mcptelemetry_toolspy---mcp-tools-for-telemetry-audit-logs-and-metrics) | 145 | MCP tools for telemetry, audit logs and metrics |

---

### `mcp/__init__.py` - package marker for the MCP surface

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `mcp/_shared.py` - shared context and error formatting for every tool group

*112 lines · depends on: `agent/orchestrator/__init__.py`, `observability/audit_log.py`, `observability/telemetry_store.py`, `specifications/gate.py`, `specifications/git_versioning.py`, `specifications/preprocessing.py`, `state/controller_runtime.py`, `state/harness_coordinator.py`, `state/planning.py`, `state/project_state_store.py` · used by: `mcp/agent_tools.py`, `mcp/controller_tools.py`, `mcp/git_tools.py`, `mcp/orchestration_tools.py`, `mcp/project_state_tools.py`, `mcp/run_tools.py`, `mcp/server.py`, `mcp/specification_tools.py` (+1 more) · not re-exported at the package root*

**Role in the workflow.** `create_mcp_server` builds one `McpContext`; each `register_*_tools` function closes over it, so all tool groups share the same stores.

**Contents**

- `_validation_errors(error: ValidationError) -> list[dict[str, Any]]` - Converts a pydantic `ValidationError` into `{location, message, type}` entries naming each failing field path. · *Called by:* `mcp/agent_tools.py::register_agent_tools.assemble_initial_context_tool`, `mcp/agent_tools.py::register_agent_tools.run_agent_task`, `mcp/agent_tools.py::register_agent_tools.validate_agent_definition`, `mcp/controller_tools.py::register_controller_tools.create_controller` (+19 more)
- `default_capability_policy() -> CapabilityPolicy` - The default grant for MCP-run agents: role `agent` may read files and artifacts, diff artifacts, read evidence, brief, wait, and write drafts (approval-gated) under the task workspace.

*Module-level names:* `DEFAULT_AGENT_ROLE`
- **class `McpContext`** *(dataclass)* - Dataclass of the shared services: run root, harness coordinator, telemetry, audit logs, project-state store, controller runtime, specification services, plan validator, version service, a cache of orchestrators, the `model_resolver`, the `capability_policy` and `supervisor` for agent tasks, the durable `agent_task_approvals` registry and an optional `search_client`. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - fields: `run_root`, `coordinator`, `telemetry`, `audit_logs`, `project_states`, `controller_runtime`, `specification_root`, `preprocessor`, `specification_gate`, `gate_store`, `plan_validator`, `versioning`, `orchestrators`
  - `McpContext.repository_for(relative_path: str) -> GitRepositoryAdapter` - Resolves a relative Git repository path inside the run root (absolute paths and escapes are rejected) and returns an adapter for it. · *Called by:* `mcp/git_tools.py::register_git_tools.classify_specification_version`, `mcp/git_tools.py::register_git_tools.create_specification_git_lock`, `mcp/git_tools.py::register_git_tools.create_variant_worktree`, `mcp/git_tools.py::register_git_tools.get_git_repository_state`
  - `McpContext.orchestration_for(orchestration_id: str) -> Orchestrator` - Returns the cached orchestrator for an id, otherwise resumes a policy shell from the persisted record using the shared controller runtime and telemetry. · *Called by:* `mcp/orchestration_tools.py::register_orchestration_tools.approve_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.cancel_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.get_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.submit_orchestration_for_approval`

---

### `mcp/agent_tools.py` - MCP tools for agent contract validation, context assembly and scripted runs

*219 lines · depends on: `agent/base_agent/__init__.py`, `agent/model.py`, `foundations/contracts.py`, `mcp/_shared.py`, `memory/context.py`, `memory/context_projection.py`, `observability/telemetry_models.py`, `state/project_state_models.py`, `tools/tools.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Contract checks and a deterministic dry run for hosts that embed the SDK over MCP.

**Contents**

- `register_agent_tools(server: MCPServer, ctx: McpContext) -> None` - Registers five tools on the server. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_agent_tools.validate_agent_definition(definition: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: validates an `AgentDefinition` and returns its field errors or the normalised definition and tool names (always `ok: true`; `valid` carries the verdict).
  - `register_agent_tools.assemble_initial_context_tool(definition: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool `assemble_initial_context`: validates a definition and task and returns the deterministic initial prompt sections.
  - `register_agent_tools.run_agent_task(definition: dict[str, Any], task: dict[str, Any], runtime_options: dict[str, Any] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: resolves the definition's `model_binding` (with fallbacks) through `ctx.model_resolver` (an unconfigured provider returns `MODEL_PROVIDER_NOT_CONFIGURED`), then runs the task through `BaseAgent` with a `HarnessToolExecutor` in its own workspace `run_root/agent-tasks/<task_id>` under `ctx.capability_policy` for `runtime_options.role`. An approval-gated tool ends the run `blocked`; the operator decides it with `submit_agent_task_approval` and passes the id back in `runtime_options.approval_ids`. Returns `{ok, result, workspace}`.
  - `register_agent_tools.list_agent_task_approvals(task_id: str | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: lists agent-task approval requests, all or one task's.
  - `register_agent_tools.submit_agent_task_approval(approval_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: records an operator decision in the durable registry.
- `_task_workspace(run_root: Path, task_id: str) -> Path` - `run_root/agent-tasks/<task_id>`, refusing ids that are not safe path segments (`TASK_ID_INVALID`).
- `_plan_task_for(task: ScopedAgentTask) -> PlanTask` - The scoped `PlanTask` the harness executor checks tool use against.

---

### `mcp/client.py` - outbound MCP client lifecycle

*400 lines · depends on: `foundations/logging.py`, `mcp/client_types.py` · used by: `mcp/client_bridge.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** A host builds a manager from explicit configs, connects, lists tools and calls them (usually through `mcp_tools_as_extensions`). Nothing discovers or dials a server by itself.

**Contents**

- **class `McpClientError`** *(exception; bases: RuntimeError)* - Base of every outbound MCP failure. Carries `kind` (`McpErrorKind`), `server_name` and `target` (`server::tool` or `server::uri`).
- **class `McpServerNotConnectedError`** *(exception; bases: McpClientError)* - `kind=not-connected`: the server is not configured or not connected; the message carries the recorded status detail. · *Instantiated by:* `mcp/client.py::McpClientManager._require_session`
- **class `McpTimeoutError`** *(exception; bases: McpClientError)* - `kind=timeout`: a tool call or resource read exceeded the server's `call_timeout_seconds`. · *Instantiated by:* `mcp/client.py::McpClientManager._bounded`
- **class `McpTransportError`** *(exception; bases: McpClientError)* - `kind=transport`: the session or transport raised during a call. · *Instantiated by:* `mcp/client.py::McpClientManager._bounded`
- **class `McpToolError`** *(exception; bases: McpClientError)* - `kind=tool-error`: the server ran the tool and reported `is_error`. · *Instantiated by:* `mcp/client.py::McpClientManager.call_tool`
- **class `McpProtocolError`** *(exception; bases: McpClientError)* - `kind=protocol`: the server returned a result type this client does not support. · *Instantiated by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
- **class `_Connection`** *(class)* - One server session owned end to end by a dedicated task: a `ready` future (the session, or the connect error) and a `closing` event. The MCP transports are anyio context managers whose cancel scopes must be entered and exited by the same task; a task per connection satisfies that and lets servers connect concurrently.
- **class `McpClientManager`** *(class)* - Owns a fixed set of outbound connections.
  - `McpClientManager.__init__(server_configs: list[McpServerConfig]) -> None` - Requires unique server names and starts every status PENDING.
  - `McpClientManager.connect_all() -> None` *(async)* - Connects every server concurrently. Each has its own `connect_timeout_seconds`; a failure or timeout marks that server FAILED with an `error_kind` (`timeout` or `transport`) and does not stop the rest. · *No in-package callers (public API, entry point, or protocol hook).*
  - `McpClientManager.reconnect(name: str) -> None` *(async)* - Closes and reconnects one named server. · *No in-package callers (public API, entry point, or protocol hook).*
  - `McpClientManager.close() -> None` *(async)* - Signals every connection task to close and waits for them (bounded; a task that does not finish is cancelled); safe to repeat.
  - `McpClientManager.list_statuses() -> list[McpConnectionStatus]` - Statuses sorted by name. · *Called by:* `mcp/client.py::McpClientManager.list_resources`, `mcp/client.py::McpClientManager.list_tools`
  - `McpClientManager.list_tools() -> list[McpToolInfo]` - All discovered tools. · *Called by:* `mcp/client_bridge.py::mcp_tools_as_extensions`
  - `McpClientManager.list_resources() -> list[McpResourceInfo]` - All discovered resources.
  - `McpClientManager.call_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> str` *(async)* - Calls a tool within the call timeout and flattens its content to text; failures raise the matching `McpClientError` subclass. A timed-out call leaves the session usable. · *Called by:* `mcp/client_bridge.py::_bind_handler.handler`
  - `McpClientManager.read_resource(server_name: str, uri: str) -> str` *(async)* - Reads a resource's text or blob content within the call timeout. · *No in-package callers (public API, entry point, or protocol hook).*
  - `McpClientManager._bounded(server_name, target, operation, call) -> T` *(async)* - Applies the server's call timeout and maps exceptions to `McpTimeoutError` / `McpTransportError`. · *Called by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
  - `McpClientManager._require_session(server_name: str) -> ClientSession` - Returns the live session or raises `McpServerNotConnectedError` naming the server and its recorded detail. · *Called by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
  - `McpClientManager._connect(name: str) -> None` *(async)* - Starts the connection task and waits for it to become ready within the connect timeout; on timeout the task is cancelled. · *Called by:* `mcp/client.py::McpClientManager.connect_all`, `mcp/client.py::McpClientManager.reconnect`
  - `McpClientManager._run_connection(name, config, connection) -> None` *(async)* - The connection task: opens the stdio or streamable-HTTP transport, registers the session, signals ready and holds the session open until `closing` is set. · *Called by:* `mcp/client.py::McpClientManager._connect`
  - `McpClientManager._register_connected_session(*, name, config, stack, read_stream, write_stream, auth_configured) -> ClientSession` *(async)* - Initialises the session, lists tools and resources and records a CONNECTED status. · *Called by:* `mcp/client.py::McpClientManager._run_connection`
  - `McpClientManager._list_resources_best_effort(name: str, session: ClientSession) -> tuple[list[McpResourceInfo], str | None]` *(async, staticmethod)* - Lists resources; only `Method not found` is treated as no resources, any other failure is surfaced in the status detail. · *Called by:* `mcp/client.py::McpClientManager._register_connected_session`
  - `McpClientManager._mark_failed(name: str, config: McpServerConfig, kind: McpErrorKind, detail: str) -> None` - Records a FAILED status with its `error_kind`. · *Called by:* `mcp/client.py::McpClientManager._connect`
  - `McpClientManager._close_one(name: str, *, cancel: bool = False) -> None` *(async)* - Closes one connection gracefully, or cancels it when it is still connecting. · *Called by:* `mcp/client.py::McpClientManager.close`, `mcp/client.py::McpClientManager.reconnect`, `mcp/client.py::McpClientManager._connect`
- `_auth_configured(config: McpServerConfig) -> bool` - True when a stdio config sets `env` or an HTTP config sets `headers`.
- `_transport_kind(config: McpServerConfig) -> McpTransportKind` - Maps a config to its transport kind.

*Module-level names:* `_logger`

---

### `mcp/client_bridge.py` - opt-in bridge from external MCP tools to governed harness tools

*55 lines · depends on: `mcp/client.py`, `tools/policy.py`, `tools/registry.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 2 name(s)*

**Role in the workflow.** A host connects specific servers and passes `mcp_tools_as_extensions(manager)` to `HarnessToolRegistry.with_extensions`; the policy then gates each call by capability and approval.

**Contents**

- `registered_tool_name(server_name: str, tool_name: str) -> str` - `mcp__<server>__<tool>`. · *Called by:* `mcp/client_bridge.py::mcp_tools_as_extensions`
- `mcp_tools_as_extensions(manager: McpClientManager, *, capability_prefix: str='mcp') -> tuple[list[RegisteredTool], dict[str, HarnessToolHandler]]` - One registered tool and handler per connected MCP tool, each behind the server-scoped capability `<prefix>.<server>` and classified as a PROCESS side effect because the external effect is unknown. · *No in-package callers (public API, entry point, or protocol hook).*
- `_bind_handler(manager: McpClientManager, server_name: str, tool_name: str) -> HarnessToolHandler` - Builds the async handler that forwards to `McpClientManager.call_tool`. · *Called by:* `mcp/client_bridge.py::mcp_tools_as_extensions`
  - `_bind_handler.handler(_context: HarnessExecutionContext, arguments: dict[str, Any]) -> str` *(async)* - Calls the bound server tool with the supplied arguments. · *Called by:* `mcp/client_bridge.py::_bind_handler`, `tools/registry.py::HarnessToolExecutor._execute_registered`

---

### `mcp/client_types.py` - typed configuration and status for the outbound MCP client

*83 lines · depends on: `foundations/contracts.py` · used by: `mcp/client.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Hosts build server configs; `McpClientManager` reports a status with discovered tools and resources per server.

**Contents**

- **class `McpTransportKind`** *(enum; bases: StrEnum)* - stdio or http.
  - members: `STDIO`, `HTTP`
- **class `McpErrorKind`** *(enum; bases: StrEnum)* - Why an outbound operation failed: not-connected, timeout, transport, tool-error or protocol.
  - members: `NOT_CONNECTED`, `TIMEOUT`, `TRANSPORT`, `TOOL_ERROR`, `PROTOCOL`
- **class `McpConnectionState`** *(enum; bases: StrEnum)* - pending, connected or failed.
  - members: `PENDING`, `CONNECTED`, `FAILED`
- **class `McpStdioServerConfig`** *(pydantic model; bases: StrictModel)* - A local server launched as a child process: command, args, optional environment and working directory.
  - fields: `name`, `command`, `args`, `env`, `cwd`, `connect_timeout_seconds` (default 30), `call_timeout_seconds` (default 120)
- **class `McpHttpServerConfig`** *(pydantic model; bases: StrictModel)* - A remote streamable-HTTP server: URL and optional headers.
  - fields: `name`, `url`, `headers`, `connect_timeout_seconds` (default 30), `call_timeout_seconds` (default 120)
- **class `McpToolInfo`** *(pydantic model; bases: StrictModel)* - A discovered tool: server, name, description and input schema. · *Instantiated by:* `mcp/client.py::McpClientManager._register_connected_session`
  - fields: `server_name`, `name`, `description`, `input_schema`
- **class `McpResourceInfo`** *(pydantic model; bases: StrictModel)* - A discovered resource: server, name, URI and description. · *Instantiated by:* `mcp/client.py::McpClientManager._list_resources_best_effort`
  - fields: `server_name`, `name`, `uri`, `description`
- **class `McpConnectionStatus`** *(pydantic model; bases: StrictModel)* - State, transport, whether credentials are configured, a detail message and the discovered tools and resources. · *Instantiated by:* `mcp/client.py::McpClientManager.__init__`, `mcp/client.py::McpClientManager._mark_failed`, `mcp/client.py::McpClientManager._register_connected_session`, `mcp/client.py::McpClientManager.reconnect`
  - fields: `name`, `state`, `transport`, `auth_configured`, `detail`, `error_kind`, `tools`, `resources`

**Algorithms & invariants.** `McpServerConfig` is the union of the two config types.

*Module-level names:* `McpServerConfig`

---

### `mcp/controller_tools.py` - MCP tools for the deterministic controller

*217 lines · depends on: `mcp/_shared.py`, `state/graph_models.py`, `state/orchestration_models.py`, `state/planning.py`, `state/shared_state.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** One-to-one wrappers over `ControllerRuntime`, in lifecycle order: create, submit plan, approve, dispatch, record node results, publish discoveries, check provenance, repair or complete, cancel.

**Contents**

- `register_controller_tools(server: MCPServer, ctx: McpContext) -> None` - Registers thirteen tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_controller_tools.create_controller(snapshot: dict[str, Any], profile: dict[str, Any], routing_rules: dict[str, Any], gap_metadata: dict[str, Any], max_repair_attempts: int=1) -> dic...` *(async, mcp-tool)* - MCP tool: creates a controller bound to a read-only snapshot, profile, routing rules and gap metadata.
  - `register_controller_tools.submit_controller_plan(controller_id: str, plan: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: presents a validated plan for approval.
  - `register_controller_tools.approve_controller_plan(controller_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: records the designer's decision.
  - `register_controller_tools.dispatch_controller(controller_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: starts the graph run with the lateral substrate.
  - `register_controller_tools.record_controller_node_result(controller_id: str, node_id: str, result: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: commits a typed node result into graph state and project state.
  - `register_controller_tools.publish_exploratory_discovery(controller_id: str, discovery: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: publishes a closed discovery (the response key is named `controller` but holds the run record).
  - `register_controller_tools.request_lateral_dependency(controller_id: str, request: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: attaches a consumer to a discovery.
  - `register_controller_tools.get_controller_state(controller_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the controller record and events.
  - `register_controller_tools.get_shared_state(controller_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the graph's typed lateral state.
  - `register_controller_tools.verify_provenance_contract(controller_id: str, records: list[dict[str, Any]], required_schema_version: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: checks provenance records at a fan-in.
  - `register_controller_tools.record_controller_stage_failure(controller_id: str, reason: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: requests a bounded repair or escalates.
  - `register_controller_tools.complete_controller(controller_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: marks the controller completed.
  - `register_controller_tools.cancel_controller(controller_id: str, reason: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: cancels the run and the controller.

---

### `mcp/git_tools.py` - MCP tools for local Git inspection and specification version locks

*109 lines · depends on: `mcp/_shared.py`, `specifications/gate_models.py`, `specifications/git_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Repository paths must be relative and inside the run root; locks and worktrees need a recorded approval.

**Contents**

- `register_git_tools(server: MCPServer, ctx: McpContext) -> None` - Registers four tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_git_tools.get_git_repository_state(repository_path: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: reads HEAD, branch, cleanliness and version tags without mutating.
  - `register_git_tools.classify_specification_version(repository_path: str, version: str, specification: dict[str, Any], dependency_graph: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: recommends a major, minor or patch bump from the structural diff against the previous snapshot.
  - `register_git_tools.create_specification_git_lock(repository_path: str, specification: dict[str, Any], dependency_graph: dict[str, Any], gap_report: dict[str, Any], metadata: dict[str, Any], appro...` *(async, mcp-tool)* - MCP tool: creates the annotated tag plus snapshot and lock record after the approval and consistency checks.
  - `register_git_tools.create_variant_worktree(repository_path: str, name: str, branch: str, base_ref: str, specification_tag: str, purpose: str, approval: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: creates an approved variant worktree from a specification tag.

---

### `mcp/orchestration_tools.py` - MCP tools for plan validation, graph runs and orchestration compilation

*115 lines · depends on: `agent/orchestrator/__init__.py`, `mcp/_shared.py`, `state/planning.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** `validate_plan` and `start_run` expose the planner contract; the orchestration tools prepare, submit, approve, inspect and cancel host-configured orchestrations from data-only policies (no models or callbacks cross MCP, so dispatch and execution stay host-local).

**Contents**

- `register_orchestration_tools(server: MCPServer, ctx: McpContext) -> None` - Registers seven tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_orchestration_tools.validate_plan(plan: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the deterministic validation report for a plan.
  - `register_orchestration_tools.start_run(plan: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: validates a plan and starts a persisted graph run through the server's coordinator.
  - `register_orchestration_tools.prepare_orchestration(policy: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: builds an orchestrator from a policy and compiles a request into an approval-gated record, caching the orchestrator.
  - `register_orchestration_tools.submit_orchestration_for_approval(orchestration_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: creates the controller and presents the plan.
  - `register_orchestration_tools.approve_orchestration(orchestration_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: records the designer's decision.
  - `register_orchestration_tools.get_orchestration(orchestration_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the persisted record.
  - `register_orchestration_tools.cancel_orchestration(orchestration_id: str, reason: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: cancels the orchestration and its controller.

---

### `mcp/project_state_tools.py` - MCP tools for project working memory

*221 lines · depends on: `mcp/_shared.py`, `state/project_state_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Initialise a project's state, read it, and record human decisions and open questions as authority-checked transitions.

**Contents**

- `register_project_state_tools(server: MCPServer, ctx: McpContext) -> None` - Registers seven tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_project_state_tools.initialize_project_state(project_id: str, stage_schema: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: creates or loads a project state for a stage schema.
  - `register_project_state_tools.get_project_state(project_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the current state (history stays audit evidence).
  - `register_project_state_tools.record_human_project_decision(project_id: str, decision_id: str, content: str, status: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: applies a `HUMAN_DECISION` transition with evidence; the server cannot verify the caller is human.
  - `register_project_state_tools.open_project_question(project_id: str, question_id: str, content: str, owner: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: applies a `QUESTION_OPENED` transition; `resolve_project_question` closes it.
  - `register_project_state_tools._apply_human(project_id, kind, action_id, payload, evidence_id, evidence_kind, source_spans) -> dict[str, Any]` - Applies one human-authority transition with a single evidence reference and returns the state or typed errors.
  - `register_project_state_tools.resolve_project_question(project_id: str, question_id: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: applies `QUESTION_RESOLVED` on human authority.
  - `register_project_state_tools.resolve_project_blocker(project_id: str, blocker_id: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: applies `BLOCKER_RESOLVED` on human authority.
  - `register_project_state_tools.set_project_artifact_status(project_id: str, relative_path: str, status: str, artifact_id: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: applies `ARTIFACT_STATUS_CHANGED` for the reviewed version; refused if the artifact was rewritten since.

---

### `mcp/run_tools.py` - MCP tools for graph run state, cancellation, approvals and resumption

*59 lines · depends on: `mcp/_shared.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Operates on the server's `HarnessCoordinator` (the one not owned by the controller runtime).

**Contents**

- `register_run_tools(server: MCPServer, ctx: McpContext) -> None` - Registers four tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_run_tools.get_run_state(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns the hash-verified run record; read-only, and refreshes the coordinator's cache if another writer changed the run.
  - `register_run_tools.cancel_run(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: cancels the currently runnable nodes and sets the cancelled flag.
  - `register_run_tools.submit_approval(run_id: str, approval_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: records an approval decision in the in-memory registry of a run this coordinator started or loaded.
  - `register_run_tools.resume_run(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: re-reads and integrity-verifies the persisted run.

---

### `mcp/server.py` - MCP server factory and loopback entry point

*159 lines · depends on: `mcp/_shared.py`, `mcp/agent_tools.py`, `mcp/controller_tools.py`, `mcp/git_tools.py`, `mcp/orchestration_tools.py`, `mcp/project_state_tools.py`, `mcp/run_tools.py`, `mcp/specification_tools.py`, `mcp/telemetry_tools.py`, `observability/audit_log.py`, `observability/metrics.py`, `observability/telemetry_store.py`, `specifications/gate.py`, `specifications/git_versioning.py`, `specifications/preprocessing.py`, `state/controller_runtime.py`, `state/harness_coordinator.py`, `state/planning.py`, `state/project_state_store.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `create_mcp_server` wires the stores and tool groups; `main` serves it over streamable HTTP on loopback (`AGENT_RUNTIME_HOST`, default `127.0.0.1`, and `AGENT_RUNTIME_PORT`, default `8001`, path `/mcp`, JSON responses, stateless).

**Contents**

- `create_mcp_server(run_root: Path | None=None, *, model_resolver: ModelResolver | None=None, capability_policy: CapabilityPolicy | None=None, supervisor: ProcessSupervisor | None=None, search_client: WebSearchClient | None=None) -> MCPServer` - Resolves the run root (`AGENT_RUNTIME_RUN_ROOT` or `.agent-runtime`) and the specification root (`AGENT_SPECIFICATION_ROOT`), builds telemetry, one harness coordinator and one project-state store (shared by the controller runtime and the tool groups, so there is a single writer per run root), audit logs, the specification and versioning services, the model resolver (default `ModelResolver.from_environment()`), the capability policy (default `default_capability_policy()`), a `ProcessSupervisor` and the durable agent-task approval registry (`.agent-approvals/agent-tasks.json`) into an `McpContext`, then registers the eight tool groups. · *Called by:* `mcp/server.py::__getattr__`, `mcp/server.py::main`
- `__getattr__(name: str) -> Any` - Module hook that builds the default server only when `mcp` is accessed.
- `__dir__() -> list[str]` - Advertises the lazy `mcp` attribute without constructing stores.
- `main() -> None` - Runs the default server on loopback. · *Called within this file by:* `mcp/server.py::<module>`

**Algorithms & invariants.** The controller runtime receives the same coordinator and project-state store as the tool groups; before this was fixed the server held two of each over one run root, and a `get_run_state` poll could revert a run the controller had advanced. `SERVER_NAME` and `SERVER_VERSION` come from `foundations/version.py`, the same single source the package version is built from.

*Module-level names:* `SERVER_NAME`, `SERVER_VERSION`, `_default_server`

---

### `mcp/specification_tools.py` - MCP tools for the specification pipeline and Gate 1

*132 lines · depends on: `mcp/_shared.py`, `memory/context_selection.py`, `specifications/documents.py`, `specifications/gate_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Parse a manifest, select task context, run Gate 1 with source-bound semantic admissions, and persist the soft-lock artifacts only after explicit approval.

**Contents**

- `register_specification_tools(server: MCPServer, ctx: McpContext) -> None` - Registers four tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_specification_tools.process_specification_manifest(manifest_path: str='specification-manifest.yaml') -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: loads a manifest, parses every document into trees, persists them and returns them with their paths.
  - `register_specification_tools.select_task_context(trees: list[dict[str, Any]], stage: str, task_text: str, scope_pointers: list[str] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: prunes trees by design stage and matches scope pointers and task keywords.
  - `register_specification_tools.validate_gate_one(specification: dict[str, Any], required_categories: list[str], semantic_findings: list[dict[str, Any]] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: runs the deterministic Gate 1 checks plus admission of host-proposed semantic findings; returns the graph, report and summary.
  - `register_specification_tools.soft_lock_specification(specification: dict[str, Any], dependency_graph: dict[str, Any], gap_report: dict[str, Any], metadata: dict[str, Any], user_approved: bool, procee...` *(async, mcp-tool)* - MCP tool: applies the soft-lock decision and, only if accepted, persists the Gate 1 artifacts.

---

### `mcp/telemetry_tools.py` - MCP tools for telemetry, audit logs and metrics

*145 lines · depends on: `mcp/_shared.py`, `observability/telemetry_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Read-mostly access to the evidence ledgers. Chain checks return `integrity_chain_valid` plus an `integrity_failure` object (sequence, kind, message) when a chain is broken.

**Contents**

- `register_telemetry_tools(server: MCPServer, ctx: McpContext) -> None` - Registers nine tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_telemetry_tools.list_telemetry_runs(limit: int=100) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: lists runs.
  - `register_telemetry_tools.get_telemetry_events(run_id: str, after_sequence: int=0, limit: int=250) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns a page of events, whether the run's chain verifies and, when it does not, the first failure (sequence, kind and message).
  - `register_telemetry_tools.get_audit_log(run_id: str, limit: int=1000) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns bounded audit entries, chain validity and the first integrity failure when broken.
  - `register_telemetry_tools.render_audit_transcript(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: renders the Markdown transcript and returns its relative path, chain validity and the first failure when broken.
  - `register_telemetry_tools.register_metric_definition(definition: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: registers a versioned metric definition.
  - `register_telemetry_tools.list_metric_definitions() -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: lists definitions.
  - `register_telemetry_tools.record_metric_observation(observation: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: records an observed or explicitly unavailable metric.
  - `register_telemetry_tools.get_telemetry_metrics(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: returns a run's observations.
  - `register_telemetry_tools.create_telemetry_report(run_id: str) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: builds the reproducible run report.

