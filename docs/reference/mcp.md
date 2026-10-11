# `mcp/` - the MCP server that exposes the SDK to a host, and the client that consumes external MCP tools

Two directions. **Server** (`server.py` plus seven `*_tools.py` groups): `create_mcp_server` builds one `McpContext` of durable stores and registers 53 tools that let a TypeScript or other host drive the Python runtime: validate agent definitions, run an agent task with the model, tools, permissions and limits the caller supplies in the request (cancel it, even in the middle of a model call, decide the approvals it asks for while it runs, and read the files it wrote), manage project state, validate plans and start graph runs, drive the controller lifecycle, parse specification manifests, inspect telemetry and audit logs, and prune finished runs. Every tool returns `{ok: true, ...}` or `{ok: false, errors: [...]}` (pydantic field errors as `{location, message, type}`, other failures as `{message, type}` naming the exception class); only `run_agent_task` calls a model, through the endpoint and key given in its own request. Tool bodies run on worker threads, so one slow tool cannot stall other clients; tools that share the coordinator, controller and orchestration stores also hold one lock and never interleave. The HTTP service (`main`) refuses to start without `AGENT_RUNTIME_AUTH_TOKEN` (at least 32 characters) and answers every request that lacks `Authorization: Bearer <token>` with HTTP 401 before any tool runs; it checks the Host and Origin headers for every loopback bind and requires an explicit `AGENT_RUNTIME_ALLOWED_HOSTS` list for any other bind address. It serves HTTPS when `AGENT_RUNTIME_TLS_CERTFILE` and `AGENT_RUNTIME_TLS_KEYFILE` name a certificate and key, and refuses a non-loopback bind without them unless `AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1` says a proxy terminates TLS in front of it. The authority a caller claims (for example `human`) is still the authenticated host's responsibility: a holder of the token can approve. **Client** (`client.py`, `client_types.py`, `client_bridge.py`): `McpClientManager` connects this SDK to external MCP servers (each connection lives in its own task, with a connect timeout, optional per-call timeouts and an optional liveness ping, and a cancelled or failed connect never leaves a child process or a cancel scope behind) and `mcp_tools_as_extensions` opts chosen external tools into the governed tool registry under capability and approval control, marking their output as untrusted.

| File | Lines | Role |
|---|---:|---|
| [`mcp/__init__.py`](#mcp__init__py---package-marker-for-the-mcp-surface) | 3 | package marker for the MCP surface |
| [`mcp/_shared.py`](#mcp_sharedpy---shared-context-and-error-formatting-for-every-tool-group) | 117 | shared context and error formatting for every tool group |
| [`mcp/agent_tools.py`](#mcpagent_toolspy---mcp-tools-for-agent-contract-validation-context-assembly-and-scripted-runs) | 159 | MCP tools for agent contract validation, context assembly and scripted runs |
| [`mcp/client.py`](#mcpclientpy---outbound-mcp-client-lifecycle) | 541 | outbound MCP client lifecycle |
| [`mcp/client_bridge.py`](#mcpclient_bridgepy---opt-in-bridge-from-external-mcp-tools-to-governed-harness-tools) | 92 | opt-in bridge from external MCP tools to governed harness tools |
| [`mcp/client_types.py`](#mcpclient_typespy---typed-configuration-and-status-for-the-outbound-mcp-client) | 89 | typed configuration and status for the outbound MCP client |
| [`mcp/controller_tools.py`](#mcpcontroller_toolspy---mcp-tools-for-the-deterministic-controller) | 269 | MCP tools for the deterministic controller |
| [`mcp/orchestration_tools.py`](#mcporchestration_toolspy---mcp-tools-for-plan-validation-graph-runs-and-orchestration-compilation) | 126 | MCP tools for plan validation, graph runs and orchestration compilation |
| [`mcp/project_state_tools.py`](#mcpproject_state_toolspy---mcp-tools-for-project-working-memory) | 192 | MCP tools for project working memory |
| [`mcp/run_tools.py`](#mcprun_toolspy---mcp-tools-for-graph-run-state-cancellation-approvals-and-resumption) | 59 | MCP tools for graph run state, cancellation, approvals and resumption |
| [`mcp/security.py`](#mcpsecuritypy---bearer-token-authentication-and-host-checks-for-the-http-service) | 253 | bearer-token authentication and host checks for the HTTP service |
| [`mcp/server.py`](#mcpserverpy---mcp-server-factory-and-loopback-entry-point) | 177 | MCP server factory and loopback entry point |
| [`mcp/specification_tools.py`](#mcpspecification_toolspy---mcp-tool-for-specification-parsing) | 40 | MCP tool for specification parsing |
| [`mcp/telemetry_tools.py`](#mcptelemetry_toolspy---mcp-tools-for-telemetry-audit-logs-and-metrics) | 176 | MCP tools for telemetry, audit logs and metrics |

---

### `mcp/__init__.py` - package marker for the MCP surface

*3 lines · depends on: nothing in the package · used by: no other module (entry point or re-exported only) · not re-exported at the package root*

**Role in the workflow.** Docstring only.

---

### `mcp/_shared.py` - shared context and error formatting for every tool group

*117 lines · depends on: `agent/orchestrator/__init__.py`, `agent/retention.py`, `agent/task_runner.py`, `observability/audit_log.py`, `observability/telemetry_store.py`, `specifications/preprocessing.py`, `state/controller_runtime.py`, `state/harness_coordinator.py`, `state/planning.py`, `state/project_state_store.py` · used by: `mcp/agent_tools.py`, `mcp/controller_tools.py`, `mcp/orchestration_tools.py`, `mcp/project_state_tools.py`, `mcp/run_tools.py`, `mcp/server.py`, `mcp/specification_tools.py`, `mcp/telemetry_tools.py` · not re-exported at the package root*

**Role in the workflow.** `create_mcp_server` builds one `McpContext`; each `register_*_tools` function closes over it, so all tool groups share the same stores. Tools register through `McpContext.tool`, which runs the body on a worker thread, serializes the tools that touch the shared coordinator, controller and orchestration stores behind `state_lock`, and turns an escaping exception into the standard error envelope naming the tool.

**Contents**

- `_validation_errors(error: ValidationError) -> list[dict[str, Any]]` - Converts a pydantic `ValidationError` into `{location, message, type}` entries naming each failing field path. · *Called by:* `mcp/_shared.py::McpContext._offloaded.run`, `mcp/agent_tools.py::register_agent_tools.assemble_initial_context_tool`, `mcp/agent_tools.py::register_agent_tools.run_agent_task`, `mcp/agent_tools.py::register_agent_tools.validate_agent_definition` (+17 more)
- **class `McpContext`** *(dataclass)* - Dataclass of the shared services: run root, harness coordinator, telemetry, audit logs, project-state store, controller runtime, specification preprocessor, plan validator, the task runner, the run retention, a cache of orchestrators and the `state_lock` that serializes stateful tools. · *Instantiated by:* `mcp/server.py::create_mcp_server`
  - fields: `run_root`, `coordinator`, `telemetry`, `audit_logs`, `project_states`, `controller_runtime`, `specification_root`, `preprocessor`, `plan_validator`, `task_runner`, `retention`, `orchestrators`, `state_lock`
  - `McpContext.tool(server: MCPServer, name: str, *, exclusive: bool=True) -> Callable[[Callable[..., Any]], Callable[..., Any]]` - Returns a decorator that registers a function as an MCP tool named `name` (structured output) after wrapping it with `_offloaded`; `exclusive=False` is for tools that only touch thread-safe or local state. · *Called by:* `base_agent/agent.py::BaseAgent._execute_tool_call`, `base_agent/agent.py::BaseAgent._record_executed_result`, `agent/graph_agent_executor.py::GraphAgentExecutor.execute`, `orchestrator/orchestrator.py::Orchestrator._validate_binding` (+17 more)
    - `McpContext.tool.decorate(function: Callable[..., Any]) -> Callable[..., Any]` - Registers the wrapped function with the server.
  - `McpContext._offloaded(function: Callable[..., Any], name: str, exclusive: bool) -> Callable[..., Any]` - Wraps a tool body so it runs off the event loop and publishes the original signature (annotations evaluated) for schema generation.
    - `McpContext._offloaded.call(*args: Any, **kwargs: Any) -> Any` - Runs the body in the worker thread; an `async` body gets its own event loop there.
    - `McpContext._offloaded.run(*args: Any, **kwargs: Any) -> Any` *(async)* - Awaits `call` in a thread; a `ValidationError` becomes `{ok: false, errors: [{location, message, type}]}` and any other exception `{ok: false, errors: [{message: 'MCP tool "<name>" failed: ...', type}]}`.
    - `McpContext._offloaded.wrapper(*args: Any, **kwargs: Any) -> Any` *(async)* - The registered coroutine: awaits `run` directly, or inside `state_lock` for an exclusive tool. · *Called within this file by:* `mcp/_shared.py::McpContext._offloaded`
  - `McpContext.orchestration_for(orchestration_id: str) -> Orchestrator` - Returns the cached orchestrator for an id, otherwise resumes a policy shell from the persisted record using the shared controller runtime and telemetry. · *Called by:* `mcp/orchestration_tools.py::register_orchestration_tools.approve_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.cancel_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.get_orchestration`, `mcp/orchestration_tools.py::register_orchestration_tools.reconcile_orchestration` (+1 more)

---

### `mcp/agent_tools.py` - MCP tools for agent contract validation, context assembly and scripted runs

*159 lines · depends on: `agent/__init__.py`, `agent/task_files.py`, `agent/task_runner.py`, `foundations/contracts.py`, `mcp/_shared.py`, `memory/context.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Contract checks and a deterministic dry run for hosts that embed the SDK over MCP. A backend also steers a running task (cancel it, answer its approvals) and reads back the files it wrote.

**Contents**

- `register_agent_tools(server: MCPServer, ctx: McpContext) -> None` - Registers eight tools on the server. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_agent_tools.validate_agent_definition(definition: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: validates an `AgentDefinition` and returns its field errors or the normalised definition and tool names (always `ok: true`; `valid` carries the verdict).
  - `register_agent_tools.assemble_initial_context_tool(definition: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool `assemble_initial_context`: validates a definition and task and returns the deterministic initial prompt sections.
  - `register_agent_tools.run_agent_task(definition: dict[str, Any], task: dict[str, Any], runtime_options: dict[str, Any] | None=None) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: parses `TaskRunOptions` and hands the definition, task and options to `AgentTaskRunner.run`, which runs `BaseAgent` with the model endpoint, permissions, built-in tools, workspace, commands and MCP servers the request names (or replays `scripted_turns` without an endpoint), emits `run.created` and `run.completed` or `run.terminated` and returns the result; with `permissions.approval_mode` `wait` a call that needs approval pauses the run until `decide_task_approval` answers it or `approval_timeout_seconds` passes; `traceparent` continues the caller's W3C trace (its trace id stamps the run's events and spans and appears in `profile.trace_id`) and `model_endpoint.propagate_trace_context` also sends it to the provider; a refused option is returned as `{ok: false, errors: [{message, type}]}`.
  - `register_agent_tools.cancel_agent_task(task_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: stops the running task with that id, interrupting its model call, tool call or approval wait, and the run ends CANCELLED; an id that is not running is reported as `No agent task "<id>" is running.`
  - `register_agent_tools.list_task_approvals(task_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: lists the approval requests (id, capability, reason, status, decision reason) of a running task, which a run in `wait` mode is paused on; an id that is not running gives `No agent task "<id>" is running.`
  - `register_agent_tools.decide_task_approval(task_id: str, approval_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: approves or rejects one pending request of a running task with an optional reason and returns it; an unknown request, a task that is not running and a request that already has a decision are reported by name. The decision is recorded as an `approval.decided` telemetry event.
  - `register_agent_tools.list_task_files(task_id: str, path: str='.', workspace: str | None=None, offset: int=0, limit: int=task_files.DEFAULT_LISTING_LIMIT) -> dict[str, Any]` *(mcp-tool)* - MCP tool: lists one directory of the task's workspace (`workspace` is the value given in its run options, default `workspaces/<task id>`), a page at a time; credential files and SDK-internal state are never listed.
  - `register_agent_tools.read_task_file(task_id: str, path: str, workspace: str | None=None, offset: int=0, max_bytes: int=task_files.DEFAULT_READ_BYTES, encoding: str='utf-8') -> dict[s...` *(mcp-tool)* - MCP tool: reads one page of a file in the task's workspace as UTF-8 text or base64 (see `agent/task_files.py`); follow `next_offset` until it is null.

---

### `mcp/client.py` - outbound MCP client lifecycle

*541 lines · depends on: `foundations/errors.py`, `foundations/identifiers.py`, `foundations/logging.py`, `mcp/client_types.py` · used by: `agent/task_runner.py`, `mcp/client_bridge.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** A host builds a manager from explicit configs, connects, lists tools and calls them (usually through `mcp_tools_as_extensions`). Nothing discovers or dials a server by itself. Each connection is owned by one dedicated task that opens the transport and session, performs the handshake under the config's `connect_timeout_seconds` and then waits to be told to stop; every cleanup happens in that same task, so a timeout, a failure or a cancellation of the caller closes the child process or HTTP client and leaves no cancel scope attached to the caller. A call can be bounded (`call_timeout_seconds` on the config, or `timeout_seconds` on the call): the wait ends with `McpCallTimeoutError` and the session stays usable. A server with `ping_interval_seconds` is pinged on that interval from `connect_all`; one that does not answer within `ping_timeout_seconds` is marked FAILED with a detail naming the command or endpoint, and is reconnected when `reconnect_on_ping_failure` is set.

**Contents**

- **class `McpClientError`** *(exception; bases: RuntimeError)* - Base class of the client's errors.
- **class `McpServerNotConnectedError`** *(exception; bases: McpClientError)* - The server has no live session: never connected, failed, closed, or its connection closed during a call. Safe to treat as "reconnect". · *Instantiated by:* `mcp/client.py::McpClientManager._require_session`, `mcp/client.py::McpClientManager._session_failure`
- **class `McpToolCallError`** *(exception; bases: McpClientError)* - A tool call failed on a live connection: the server returned an error, the tool reported one, or the result type is unsupported. Not a reason to reconnect. · *Instantiated by:* `mcp/client.py::McpClientManager._session_failure`, `mcp/client.py::McpClientManager.call_tool`
- **class `McpResourceReadError`** *(exception; bases: McpClientError)* - A resource read failed on a live connection. · *Instantiated by:* `mcp/client.py::McpClientManager._session_failure`, `mcp/client.py::McpClientManager.read_resource`
- **class `McpCallTimeoutError`** *(exception; bases: McpToolCallError, McpResourceReadError)* - A call or read got no answer within its timeout; the message names the server, the action (`tool call to "server::tool"` or `resource read "server::uri"`) and the seconds, and says the connection stays open. Catching either parent class catches it. · *Instantiated by:* `mcp/client.py::McpClientManager._session_failure`, `mcp/client.py::McpClientManager.read_resource`
  - `McpCallTimeoutError.__init__(server_name: str, action: str, seconds: float) -> None` - Builds the message from the server name, the action and the seconds and keeps `server_name` and `seconds`.
- **class `_Connection`** *(dataclass)* - Bookkeeping for one server: the stop event, the ready future the handshake resolves, the owner task, whether the handshake finished and the HTTP error statuses seen while connecting.
  - fields: `stop`, `ready`, `task`, `established`, `http_statuses`
- **class `McpClientManager`** *(class)* - Owns a fixed set of outbound connections. · *Instantiated by:* `agent/task_runner.py::AgentTaskRunner._connect_mcp`
  - `McpClientManager.__init__(server_configs: list[McpServerConfig]) -> None` - Requires unique server names and starts every status PENDING.
  - `McpClientManager.connect_all() -> None` *(async)* - Connects every configured server that has no connection yet, concurrently; a per-server failure or timeout is recorded in its status and does not stop the rest. Then starts a ping monitor for each server with `ping_interval_seconds` (for one that failed to connect only when `reconnect_on_ping_failure` is set, so the monitor can retry). · *Called by:* `agent/task_runner.py::AgentTaskRunner._connect_mcp`
  - `McpClientManager.reconnect(name: str) -> None` *(async)* - Closes one named server (raising `ValueError` for an unknown name) and connects it again. Restarts the server's ping monitor. · *No in-package callers (public API, entry point, or protocol hook).*
  - `McpClientManager.close() -> None` *(async)* - Stops every connection (also one still handshaking) and waits for its owner task to finish; statuses become CLOSED. Safe to repeat and callable from any task. Also stops the ping monitors.
  - `McpClientManager.list_statuses() -> list[McpConnectionStatus]` - Statuses sorted by name. · *Called by:* `agent/task_runner.py::AgentTaskRunner._connect_mcp`, `mcp/client.py::McpClientManager.list_resources`, `mcp/client.py::McpClientManager.list_tools`
  - `McpClientManager.list_tools() -> list[McpToolInfo]` - All discovered tools. · *Called by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `mcp/client.py::McpClientManager._establish`, `mcp/client_bridge.py::mcp_tools_as_extensions`
  - `McpClientManager.list_resources() -> list[McpResourceInfo]` - All discovered resources. · *Called by:* `mcp/client.py::McpClientManager._list_resources_best_effort`
  - `McpClientManager.call_tool(server_name: str, tool_name: str, arguments: dict[str, Any], *, timeout_seconds: float | None=None) -> str` *(async)* - Calls a tool and flattens its content to text. A call that fails on a live connection raises `McpToolCallError` naming `server::tool` and the cause; a connection that closed during the call marks the server FAILED and raises `McpServerNotConnectedError`. The wait is bounded by `timeout_seconds` or else the config's `call_timeout_seconds` (none by default) through the session's per-request timeout: an unanswered call raises `McpCallTimeoutError`, the session stays usable and a late answer is ignored; a non-positive `timeout_seconds` raises `ValueError`. · *Called by:* `mcp/client_bridge.py::_bind_handler.handler`
  - `McpClientManager.read_resource(server_name: str, uri: str, *, timeout_seconds: float | None=None) -> str` *(async)* - Reads a resource's text or blob content; failures raise `McpResourceReadError` (or `McpServerNotConnectedError` when the connection closed). Bounded the same way (with `asyncio.timeout`, since the session has no per-read timeout): expiry raises `McpCallTimeoutError`. · *No in-package callers (public API, entry point, or protocol hook).*
  - `McpClientManager._call_timeout(server_name: str, override: float | None) -> float | None` - The per-call override when given, else the server config's `call_timeout_seconds`. · *Called by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
  - `McpClientManager._require_session(server_name: str) -> ClientSession` - Returns the live session or raises `McpServerNotConnectedError` naming the server and its recorded detail or state. · *Called by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
  - `McpClientManager._session_failure(server_name: str, action: str, error: Exception, failure: type[McpClientError], timeout_seconds: float | None=None) -> McpClientError` - Classifies a session exception: a JSON-RPC `Connection closed` error drops the session, marks the server FAILED and returns `McpServerNotConnectedError`; anything else becomes the requested error type with `Type: message` as the cause. With a timeout, a JSON-RPC request-timeout error becomes `McpCallTimeoutError`. · *Called within this file by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
  - `McpClientManager._connect(name: str) -> None` *(async)* - Marks the server PENDING, starts its owner task and waits for the handshake; on failure or timeout releases the connection and records a FAILED status; on cancellation of the caller releases it (killing the child) and re-raises. · *Called by:* `mcp/client.py::McpClientManager._ping_loop`, `mcp/client.py::McpClientManager.connect_all`, `mcp/client.py::McpClientManager.reconnect`
  - `McpClientManager._hold_connection(name: str, config: McpServerConfig, connection: _Connection) -> None` *(async)* - Owner task body: opens everything in one exit stack, signals the handshake result through the ready future and then waits for the stop event; the stack unwinds in this same task, so teardown never depends on the caller. Teardown errors are logged at debug level.
  - `McpClientManager._establish(stack: AsyncExitStack, name: str, config: McpServerConfig, connection: _Connection) -> None` *(async)* - Under `asyncio.timeout(connect_timeout_seconds)`: opens the transport, starts the session, initializes it and lists tools and resources, then records the CONNECTED status; a missed deadline raises `TimeoutError: no initialize response within <n>s`.
  - `McpClientManager._list_resources_best_effort(name: str, session: ClientSession) -> tuple[list[McpResourceInfo], str | None]` *(async, staticmethod)* - Lists resources; only the JSON-RPC `method not found` response is treated as no resources, any other failure is surfaced in the status detail. · *Called by:* `mcp/client.py::McpClientManager._establish`
  - `McpClientManager._mark_failed(name: str, config: McpServerConfig, error: BaseException, http_statuses: list[int] | None=None) -> None` - Records a FAILED status whose detail names the stdio command or HTTP endpoint (credentials stripped), every underlying exception (exception groups are flattened) and, for HTTP, the last error status the endpoint answered; the detail is redacted and logged at warning level. · *Called by:* `mcp/client.py::McpClientManager._connect`
  - `McpClientManager._start_ping(name: str) -> None` - Starts the server's ping task when it has `ping_interval_seconds` and is connected (or will be reconnected on failure); a monitor already running is kept. · *Called by:* `mcp/client.py::McpClientManager.connect_all`, `mcp/client.py::McpClientManager.reconnect`
  - `McpClientManager._stop_ping(name: str) -> None` *(async)* - Cancels and awaits the server's ping task; a task never cancels itself. · *Called by:* `mcp/client.py::McpClientManager._close_one`
  - `McpClientManager._ping_loop(name: str, config: McpServerConfig, interval: float) -> None` *(async)* - Every interval pings the live session (`_ping_failure`); a failure marks the server unresponsive and either ends the monitor or, with `reconnect_on_ping_failure`, releases the connection, connects again and keeps going, retrying each interval while the server stays down. · *Called by:* `mcp/client.py::McpClientManager._start_ping`
  - `McpClientManager._declare_unresponsive(name: str, config: McpServerConfig, failure: BaseException) -> None` *(async)* - Logs a warning, drops the session, records FAILED with the `_liveness_detail` text and releases the connection so no child process is left running. · *Called by:* `mcp/client.py::McpClientManager._ping_loop`
  - `McpClientManager._close_one(name: str) -> None` *(async)* - Releases one connection (if any) and marks the server CLOSED. Stops the server's ping monitor first. · *Called by:* `mcp/client.py::McpClientManager.close`, `mcp/client.py::McpClientManager.reconnect`
  - `McpClientManager._release(name: str, connection: _Connection) -> None` *(async)* - Removes the session, sets the stop event, cancels the owner task if the handshake had not finished, waits for the task to end and only then forgets the connection, so a `close` that interrupts the release still finds the task to wait for. · *Called by:* `mcp/client.py::McpClientManager._close_one`, `mcp/client.py::McpClientManager._connect`, `mcp/client.py::McpClientManager._declare_unresponsive`, `mcp/client.py::McpClientManager._ping_loop`
- `_open_transport(stack: AsyncExitStack, config: McpServerConfig, connection: _Connection) -> tuple[Any, Any]` *(async)* - Enters the stdio client or, for HTTP, an httpx client built by the MCP library's own factory (with the config headers and a hook that records error statuses) plus the streamable-HTTP transport, and returns the read and write streams whichever way the library yields them.
  - `_open_transport.record_error_status(response: Any) -> None` *(async)* - Response hook that appends the status code of every HTTP response of 400 or above to the connection's `http_statuses`.
- `_transport_kind(config: McpServerConfig) -> McpTransportKind` - Maps a config to its transport kind. · *Called by:* `mcp/client.py::McpClientManager.__init__`, `mcp/client.py::McpClientManager._close_one`, `mcp/client.py::McpClientManager._connect`, `mcp/client.py::McpClientManager._declare_unresponsive` (+3 more)
- `_auth_configured(config: McpServerConfig) -> bool` - True when a stdio config sets an environment or an HTTP config sets headers. · *Called by:* `mcp/client.py::McpClientManager._close_one`, `mcp/client.py::McpClientManager._connect`, `mcp/client.py::McpClientManager._declare_unresponsive`, `mcp/client.py::McpClientManager._establish` (+2 more)
- `_target(config: McpServerConfig) -> str` - `stdio command "x"` or `HTTP endpoint "scheme://host:port/path"` (credentials stripped), the way statuses and errors name a server. · *Called within this file by:* `mcp/client.py::_failure_detail`, `mcp/client.py::_liveness_detail`
- `_failure_detail(config: McpServerConfig, error: BaseException, http_statuses: list[int]) -> str` - `<stdio command "x" | HTTP endpoint "scheme://host:port/path"> failed to connect: <causes>[; the endpoint answered HTTP <status>]`, redacted.
- `_liveness_detail(config: McpServerConfig, failure: BaseException) -> str` - `<target> stopped answering: no answer to a ping within <n>s` (or the exception chain), redacted. · *Called by:* `mcp/client.py::McpClientManager._declare_unresponsive`
- `_require_positive(timeout_seconds: float | None) -> None` - Raises `ValueError` naming the value when a timeout argument is not positive. · *Called by:* `mcp/client.py::McpClientManager.call_tool`, `mcp/client.py::McpClientManager.read_resource`
- `_ping_failure(session: ClientSession, timeout_seconds: float) -> BaseException | None` *(async)* - Sends one ping within the timeout; returns the failure (including an exception group) or None, and lets cancellation through. · *Called by:* `mcp/client.py::McpClientManager._ping_loop`
- `_leaf_descriptions(error: BaseException) -> list[str]` - Flattens exception groups into `Type: message` strings (`MCPError(<code>): <message>` for protocol errors). · *Called by:* `mcp/client.py::_failure_detail`, `mcp/client.py::_liveness_detail`

*Module-level names:* `_logger`

---

### `mcp/client_bridge.py` - opt-in bridge from external MCP tools to governed harness tools

*92 lines · depends on: `mcp/client.py`, `tools/policy.py`, `tools/registry.py` · used by: `agent/task_runner.py` · re-exported at the package root: 2 name(s)*

**Role in the workflow.** A host connects specific servers and passes `mcp_tools_as_extensions(manager)` to `HarnessToolRegistry.with_extensions`; the policy then gates each call by capability and approval.

**Contents**

- `registered_tool_name(server_name: str, tool_name: str) -> str` - `mcp__<server>__<tool>` when that already matches `[A-Za-z0-9_-]{1,64}`; otherwise the invalid characters become `_`, the name is cut to fit and an 8-hex SHA-256 of the pair is appended, so the name is always valid for an LLM tool call and distinct pairs stay distinct. · *Called by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`, `mcp/client_bridge.py::mcp_tools_as_extensions`
- `mcp_tools_as_extensions(manager: McpClientManager, *, capability_prefix: str='mcp') -> tuple[list[RegisteredTool], dict[str, HarnessToolHandler]]` - One registered tool and handler per connected MCP tool, each behind the server-scoped capability `<prefix>.<server>` and classified as a PROCESS side effect because the external effect is unknown; refuses two tools that map to one registered name (naming both) and a server that lists a tool twice. · *Called by:* `agent/task_runner.py::AgentTaskRunner._prepare_tools`
- `_bind_handler(manager: McpClientManager, server_name: str, tool_name: str) -> HarnessToolHandler` - Builds the async handler that forwards to `McpClientManager.call_tool`. · *Called by:* `mcp/client_bridge.py::mcp_tools_as_extensions`
  - `_bind_handler.handler(_context: HarnessExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]` *(async)* - Calls the bound server tool and returns its text as untrusted content: `{source: "mcp:<server>::<tool>", untrusted_content: true, content, safety_notice}`. · *Called by:* `mcp/client_bridge.py::_bind_handler`, `tools/registry.py::HarnessToolExecutor._execute_registered`

---

### `mcp/client_types.py` - typed configuration and status for the outbound MCP client

*89 lines · depends on: `foundations/contracts.py` · used by: `agent/task_runner.py`, `mcp/client.py` · re-exported at the package root: 8 name(s)*

**Role in the workflow.** Hosts build server configs; `McpClientManager` reports a status with discovered tools and resources per server. Both config types share `McpServerOptions`: the connect timeout, an optional per-call timeout and an optional liveness ping with an optional reconnect.

**Contents**

- **class `McpTransportKind`** *(enum; bases: StrEnum)* - stdio or http.
  - members: `STDIO`, `HTTP`
- **class `McpConnectionState`** *(enum; bases: StrEnum)* - pending, connected, failed or closed (a connection that `close` or `reconnect` ended).
  - members: `PENDING`, `CONNECTED`, `FAILED`, `CLOSED`
- **class `McpServerOptions`** *(pydantic model; bases: StrictModel)* - The settings every outbound connection takes, whatever its transport: `name`; `connect_timeout_seconds` (default 30, up to an hour); `call_timeout_seconds` (default none, so a call waits as long as the server takes; up to a day); `ping_interval_seconds` (default none, so no liveness check; up to an hour); `ping_timeout_seconds` (default 10) and `reconnect_on_ping_failure` (default False).
  - fields: `name`, `connect_timeout_seconds`, `call_timeout_seconds`, `ping_interval_seconds`, `ping_timeout_seconds`, `reconnect_on_ping_failure`
- **class `McpStdioServerConfig`** *(pydantic model; bases: McpServerOptions)* - A local server launched as a child process: command, args, optional environment and working directory, plus the shared options.
  - fields: `command`, `args`, `env`, `cwd` (and the fields of `McpServerOptions`)
- **class `McpHttpServerConfig`** *(pydantic model; bases: McpServerOptions)* - A remote streamable-HTTP server: URL and optional headers, plus the shared options.
  - fields: `url`, `headers` (and the fields of `McpServerOptions`)
- **class `McpToolInfo`** *(pydantic model; bases: StrictModel)* - A discovered tool: server, name, description and input schema. · *Instantiated by:* `mcp/client.py::McpClientManager._establish`
  - fields: `server_name`, `name`, `description`, `input_schema`
- **class `McpResourceInfo`** *(pydantic model; bases: StrictModel)* - A discovered resource: server, name, URI and description. · *Instantiated by:* `mcp/client.py::McpClientManager._list_resources_best_effort`
  - fields: `server_name`, `name`, `uri`, `description`
- **class `McpConnectionStatus`** *(pydantic model; bases: StrictModel)* - State, transport, whether credentials are configured, a detail message and the discovered tools and resources. · *Instantiated by:* `mcp/client.py::McpClientManager.__init__`, `mcp/client.py::McpClientManager._close_one`, `mcp/client.py::McpClientManager._connect`, `mcp/client.py::McpClientManager._declare_unresponsive` (+3 more)
  - fields: `name`, `state`, `transport`, `auth_configured`, `detail`, `tools`, `resources`

**Algorithms & invariants.** `McpServerConfig` is the union of the two config types.

*Module-level names:* `McpServerConfig`

---

### `mcp/controller_tools.py` - MCP tools for the deterministic controller

*269 lines · depends on: `mcp/_shared.py`, `state/elastic.py`, `state/graph_models.py`, `state/orchestration_models.py`, `state/planning.py`, `state/shared_state.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** One-to-one wrappers over `ControllerRuntime`, in lifecycle order: create, submit plan, approve, dispatch, record node results, publish discoveries, check provenance, repair or complete, cancel.

**Contents**

- `register_controller_tools(server: MCPServer, ctx: McpContext) -> None` - Registers sixteen tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_controller_tools.create_controller(snapshot: dict[str, Any], profile: dict[str, Any], routing_rules: dict[str, Any], gap_metadata: dict[str, Any], max_repair_attempts: int=1, elasti...` *(mcp-tool)* - MCP tool: creates a controller bound to a read-only snapshot, profile, routing rules and gap metadata; the optional `elastic_depth_ceiling` and `elastic_nodes_ceiling` (default: the hard limits 8 and 256) bound the plans it accepts and the capacity grants it can make.
  - `register_controller_tools.submit_controller_plan(controller_id: str, plan: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: presents a validated plan for approval.
  - `register_controller_tools.approve_controller_plan(controller_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: records the designer's decision.
  - `register_controller_tools.dispatch_controller(controller_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: starts the graph run with the lateral substrate.
  - `register_controller_tools.record_controller_node_result(controller_id: str, node_id: str, result: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: commits a typed node result into graph state and project state.
  - `register_controller_tools.publish_exploratory_discovery(controller_id: str, discovery: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: publishes a closed discovery (the response key is named `controller` but holds the run record).
  - `register_controller_tools.request_lateral_dependency(controller_id: str, request: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: attaches a consumer to a discovery.
  - `register_controller_tools.grant_elastic_capacity(controller_id: str, reason: str, max_elastic_depth: int | None=None, max_elastic_nodes: int | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: raises a run's elastic caps by a recorded decision, up to the controller's ceilings, and runs the deferred requests that fit.
  - `register_controller_tools.decline_elastic_requests(controller_id: str, parent_node_id: str, reason: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: discards one node's deferred elastic requests and releases the dependents they held.
  - `register_controller_tools.get_controller_state(controller_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the controller record and events.
  - `register_controller_tools.get_shared_state(controller_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the graph's typed lateral state.
  - `register_controller_tools.verify_provenance_contract(controller_id: str, records: list[dict[str, Any]], required_schema_version: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: checks provenance records at a fan-in.
  - `register_controller_tools.record_controller_stage_failure(controller_id: str, reason: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: requests a bounded repair or escalates.
  - `register_controller_tools.complete_controller(controller_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: marks the controller completed.
  - `register_controller_tools.reconcile_controller(controller_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: runs `ControllerRuntime.reconcile`: project-state work items from the run's results, and the controller cancelled when its run was; returns the report of changes.
  - `register_controller_tools.cancel_controller(controller_id: str, reason: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: cancels the run and the controller.

---

### `mcp/orchestration_tools.py` - MCP tools for plan validation, graph runs and orchestration compilation

*126 lines · depends on: `agent/orchestrator/__init__.py`, `mcp/_shared.py`, `state/planning.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** `validate_plan` and `start_run` expose the planner contract; the orchestration tools prepare, submit, approve, inspect and cancel host-configured orchestrations from data-only policies (no models or callbacks cross MCP, so dispatch and execution stay host-local).

**Contents**

- `register_orchestration_tools(server: MCPServer, ctx: McpContext) -> None` - Registers eight tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_orchestration_tools.validate_plan(plan: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the deterministic validation report for a plan.
  - `register_orchestration_tools.start_run(plan: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: validates a plan and starts a persisted graph run through the server's coordinator.
  - `register_orchestration_tools.prepare_orchestration(policy: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]` *(async, mcp-tool)* - MCP tool: builds an orchestrator from a policy and compiles a request into an approval-gated record, caching the orchestrator.
  - `register_orchestration_tools.submit_orchestration_for_approval(orchestration_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: creates the controller and presents the plan.
  - `register_orchestration_tools.approve_orchestration(orchestration_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: records the designer's decision.
  - `register_orchestration_tools.get_orchestration(orchestration_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the persisted record.
  - `register_orchestration_tools.reconcile_orchestration(orchestration_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: runs `Orchestrator.reconcile` for the orchestration, its controller, graph run and project state; returns the report of changes.
  - `register_orchestration_tools.cancel_orchestration(orchestration_id: str, reason: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: cancels the orchestration and its controller.

---

### `mcp/project_state_tools.py` - MCP tools for project working memory

*192 lines · depends on: `mcp/_shared.py`, `state/project_state_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Initialise a project's state, read it, and record human decisions, open questions and their closure as authority-checked transitions.

**Contents**

- `register_project_state_tools(server: MCPServer, ctx: McpContext) -> None` - Registers six tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_project_state_tools.initialize_project_state(project_id: str, stage_schema: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: creates or loads a project state for a stage schema.
  - `register_project_state_tools.get_project_state(project_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the current state (history stays audit evidence).
  - `register_project_state_tools.record_human_project_decision(project_id: str, decision_id: str, content: str, status: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: applies a `HUMAN_DECISION` transition with evidence; the server cannot verify the caller is human.
  - `register_project_state_tools.resolve_project_question(project_id: str, question_id: str, resolution: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: applies a `QUESTION_RESOLVED` transition under human authority with the recorded answer and its source evidence; fails naming the question when it is not open.
  - `register_project_state_tools.clear_project_blocker(project_id: str, blocker_id: str, reason: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: applies a `BLOCKER_CLEARED` transition under human authority with the reason and its source evidence; fails naming the blocker when it does not exist.
  - `register_project_state_tools.open_project_question(project_id: str, question_id: str, content: str, owner: str, evidence_id: str, source_spans: list[str] | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: applies a `QUESTION_OPENED` transition; `resolve_project_question` closes it.

---

### `mcp/run_tools.py` - MCP tools for graph run state, cancellation, approvals and resumption

*59 lines · depends on: `mcp/_shared.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Operates on the server's `HarnessCoordinator` (the one not owned by the controller runtime).

**Contents**

- `register_run_tools(server: MCPServer, ctx: McpContext) -> None` - Registers four tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_run_tools.get_run_state(run_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns the hash-verified run record; read-only, and refreshes the coordinator's cache if another writer changed the run.
  - `register_run_tools.cancel_run(run_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: cancels the currently runnable nodes and sets the cancelled flag.
  - `register_run_tools.submit_approval(run_id: str, approval_id: str, approved: bool, reason: str | None=None) -> dict[str, Any]` *(mcp-tool)* - MCP tool: records an approval decision in the in-memory registry of a run this coordinator started or loaded.
  - `register_run_tools.resume_run(run_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: re-reads and integrity-verifies the persisted run.

---

### `mcp/security.py` - bearer-token authentication and host checks for the HTTP service

*253 lines · depends on: nothing in the package · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** `main` in `server.py` reads its settings with `load_http_service_settings`, builds the MCP app with `HttpServiceSettings.transport_security()` (Host and Origin allow-lists, DNS-rebinding protection on) and wraps it in `BearerTokenMiddleware`, so every HTTP request must carry the shared secret before any tool is reached. The same settings decide whether the service speaks TLS and how many run transcripts the audit store keeps open.

**Contents**

- **class `ServerConfigurationError`** *(exception; bases: ValueError)* - An environment setting that makes the service refuse to start; the message names the variable and the problem. · *Instantiated by:* `mcp/security.py::_parse_allowed_hosts`, `mcp/security.py::_parse_audit_max_open_handles`, `mcp/security.py::_parse_port`, `mcp/security.py::_parse_tls` (+1 more)
- **class `HttpServiceSettings`** *(dataclass)* - Validated host, port, token (excluded from `repr`), Host-header allow-list, the optional TLS certificate, key and key password (the password is excluded from `repr`) and the optional audit handle budget. · *Instantiated by:* `mcp/security.py::load_http_service_settings`
  - fields: `host`, `port`, `token`, `allowed_hosts`, `tls_certfile`, `tls_keyfile`, `tls_key_password`, `audit_max_open_handles`
  - `HttpServiceSettings.scheme() -> str` *(property)* - Property: `https` when a certificate is configured, else `http`. · *Called by:* `mcp/client.py::_target`, `mcp/security.py::BearerTokenMiddleware._authorized`, `mcp/security.py::HttpServiceSettings.transport_security`, `core/helpers.py::_assert_public_http_url` (+1 more)
  - `HttpServiceSettings.ssl_options() -> dict[str, str | None]` - The uvicorn keyword arguments (`ssl_certfile`, `ssl_keyfile`, `ssl_keyfile_password`) of a TLS service, none for plain HTTP. · *Called by:* `mcp/server.py::main`
  - `HttpServiceSettings.transport_security() -> TransportSecuritySettings` - Builds the MCP `TransportSecuritySettings`: DNS-rebinding protection on, the allowed Host patterns, and allowed Origins (`<scheme>://<pattern>`, so `https` with TLS) only for loopback binds; any browser request with another Origin is refused.
- `is_loopback_host(host: str) -> bool` - True for `localhost` (any case) and any loopback IP address (`127.0.0.0/8`, `::1`). · *Called by:* `mcp/security.py::HttpServiceSettings.transport_security`, `mcp/security.py::load_http_service_settings`
- `load_http_service_settings(environ: Mapping[str, str]) -> HttpServiceSettings` - Reads `AGENT_RUNTIME_HOST` (default `127.0.0.1`), `AGENT_RUNTIME_PORT` (default 8001, 1 to 65535), `AGENT_RUNTIME_AUTH_TOKEN` (required, at least 32 characters, no whitespace) and, for a non-loopback host, `AGENT_RUNTIME_ALLOWED_HOSTS`; a loopback bind allows `127.0.0.1:*`, `localhost:*`, `[::1]:*` and its own address. Every failure raises `ServerConfigurationError` naming the variable (never the token). Also `AGENT_RUNTIME_TLS_CERTFILE` and `AGENT_RUNTIME_TLS_KEYFILE` (both or neither; each must be a file and the pair must load, with `AGENT_RUNTIME_TLS_KEY_PASSWORD` for an encrypted key), `AGENT_RUNTIME_ALLOW_PLAIN_HTTP` (`1` permits a non-loopback bind without TLS) and `AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES` (a whole number from 1); a non-loopback bind with neither TLS nor the plain-HTTP allowance is refused, after the allowed-hosts check, with a message that names the variables to set. · *Called by:* `mcp/server.py::main`
- `_parse_audit_max_open_handles(environ: Mapping[str, str]) -> int | None` - `AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES` as an integer of at least 1, None when unset, or `... must be a whole number of at least 1, got "<value>"`. · *Called by:* `mcp/security.py::load_http_service_settings`
- `_parse_tls(environ: Mapping[str, str]) -> tuple[str | None, str | None, str | None]` - Reads the certificate, key and key password variables: none set means plain HTTP; one without the other, a path that is not a file, or a pair `ssl` cannot load (the message carries the exception and, for an encrypted key, a hint to set the password variable) raises `ServerConfigurationError` naming the variable or the files. The password is never echoed. · *Called by:* `mcp/security.py::load_http_service_settings`
- `_is_encrypted(key_file: str) -> bool` - True when the key file's first bytes contain `ENCRYPTED`, to choose the hint. · *Called by:* `mcp/security.py::_parse_tls`
- `_parse_port(raw: str) -> int` - Integer 1 to 65535 or `AGENT_RUNTIME_PORT must be an integer from 1 to 65535, got "<value>"`. · *Called by:* `mcp/security.py::load_http_service_settings`
- `_parse_allowed_hosts(host: str, raw: str) -> tuple[str, ...]` - Splits the comma list; an empty list for a non-loopback bind, or an entry with a scheme, path or whitespace, raises with the expected form. · *Called by:* `mcp/security.py::load_http_service_settings`
- **class `BearerTokenMiddleware`** *(class)* - ASGI middleware: HTTP requests pass only with `Authorization: Bearer <token>` (scheme case-insensitive, constant-time comparison); others get a JSON 401 `{ok: false, errors: [{message, type: "Unauthorized"}]}` with `WWW-Authenticate: Bearer`, and nothing reaches the app. The refused request's body is read first (`_discard_request_body`) so the 401 reaches a client that closes the connection after each request. A websocket scope is held to the same token (an unauthorized one is closed with code 1008); the lifespan scope passes through. · *Instantiated by:* `mcp/server.py::main`
  - `BearerTokenMiddleware.__init__(app: ASGIApp, token: str) -> None` - Stores the wrapped app and the token bytes.
  - `BearerTokenMiddleware.__call__(scope: Scope, receive: Receive, send: Send) -> None` *(async)* - Rejects an unauthorized HTTP request with the 401 response, after reading and discarding its body, and closes an unauthorized websocket with code 1008; anything else (the lifespan scope) goes to the app.
  - `BearerTokenMiddleware._authorized(scope: Scope) -> bool` - Splits the `Authorization` header into scheme and value and compares the value with the token using `hmac.compare_digest`. · *Called within this file by:* `mcp/security.py::BearerTokenMiddleware.__call__`
- `_discard_request_body(receive: Receive) -> None` *(async)* - Reads and drops the request body of a refused request, stopping at the end of the body, at a disconnect, after `MAX_DISCARDED_BODY_BYTES` (64 KiB) or after `DISCARD_BODY_SECONDS` (1 second), whichever comes first. · *Called by:* `mcp/security.py::BearerTokenMiddleware.__call__`

**Algorithms & invariants.** The token authenticates the caller, not an approver: a holder can use every tool, including those that record human decisions and approvals, so the token belongs to the trusted host and must not be handed to an agent; a second credential for approvers would need the TypeScript backend to hold two secrets and is still open. A `run_agent_task` request carries the caller's model API key as a secret string: it is sent only to the endpoint named in the same request and never written to telemetry, audit logs or results. Options that start processes (`command_templates`, stdio `mcp_servers`) are refused unless the operator starts the service with `AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS=1`. Without a certificate the service speaks plain HTTP, which the settings accept only on a loopback address or when `AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1` records that a proxy terminates TLS in front of it: the bearer token and every approval decision would otherwise cross the network in clear text. A refusal is sent only after the request body was read (within the limits above): a client that closes the connection after every request, as Python's `urllib` does, otherwise loses the 401 to a TCP reset when its body arrives after the server closed, which Windows reports as `ConnectionAbortedError` with no response (a test client hit it once under load).

---

### `mcp/server.py` - MCP server factory and loopback entry point

*177 lines · depends on: `agent/retention.py`, `agent/runtime.py`, `agent/task_runner.py`, `foundations/version.py`, `mcp/_shared.py`, `mcp/agent_tools.py`, `mcp/controller_tools.py`, `mcp/orchestration_tools.py`, `mcp/project_state_tools.py`, `mcp/run_tools.py`, `mcp/security.py`, `mcp/specification_tools.py`, `mcp/telemetry_tools.py`, `memory/context_projection.py`, `observability/audit_log.py`, `observability/metrics.py`, `observability/telemetry_store.py`, `specifications/preprocessing.py`, `state/controller_runtime.py`, `state/harness_coordinator.py`, `state/planning.py`, `state/project_state_store.py` · used by: no other module (entry point or re-exported only) · re-exported at the package root: 1 name(s)*

**Role in the workflow.** `create_mcp_server` wires the stores and tool groups (no network listener, no authentication: the embedding host or the stdio parent is the authority). `main` serves it over streamable HTTP (path `/mcp`, JSON responses, stateless) after validating the environment: `AGENT_RUNTIME_HOST` (default `127.0.0.1`), `AGENT_RUNTIME_PORT` (default `8001`), `AGENT_RUNTIME_AUTH_TOKEN` (required) and `AGENT_RUNTIME_ALLOWED_HOSTS` (required for a non-loopback host). `main` also reads `AGENT_RUNTIME_TLS_CERTFILE`, `AGENT_RUNTIME_TLS_KEYFILE` and `AGENT_RUNTIME_TLS_KEY_PASSWORD` (HTTPS), `AGENT_RUNTIME_ALLOW_PLAIN_HTTP` and `AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES`.

**Contents**

- `create_mcp_server(run_root: Path | None=None, *, audit_max_open_handles: int | None=None) -> MCPServer` - Resolves the run root (`AGENT_RUNTIME_RUN_ROOT` or `.agent-runtime`) and the specification root (`AGENT_SPECIFICATION_ROOT`), builds telemetry, the durable run services and the `AgentTaskRunner` (process options allowed only when `AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS=1`), one harness coordinator and one project-state store (shared by the controller runtime and the tool groups, so there is a single writer per run root), audit logs, the specification preprocessor and the `RunRetention` of the shared stores into an `McpContext`, then registers the seven tool groups (53 tools). `audit_max_open_handles` (default: the store's 32) is the audit store's append-handle budget. · *Called by:* `mcp/server.py::__getattr__`, `mcp/server.py::main`
- `__getattr__(name: str) -> Any` - Module hook that builds the default server only when `mcp` is accessed.
- `__dir__() -> list[str]` - Advertises the lazy `mcp` attribute without constructing stores.
- `main() -> None` - Loads and validates the HTTP settings; a configuration error prints `nailong-agent-sdk MCP server cannot start: <reason>` to stderr and exits with status 2. Otherwise builds the server's streamable-HTTP app with explicit Host/Origin protection, wraps it in `BearerTokenMiddleware` and serves it with uvicorn, over TLS when the settings carry a certificate, with the settings' audit handle budget. · *Called within this file by:* `mcp/server.py::<module>`

**Algorithms & invariants.** The controller runtime receives the same coordinator and project-state store as the tool groups; before this was fixed the server held two of each over one run root, and a `get_run_state` poll could revert a run the controller had advanced. `SERVER_NAME` and `SERVER_VERSION` come from `foundations/version.py`, so the server reports the name and version of the installed distribution.

*Module-level names:* `SERVER_NAME`, `SERVER_VERSION`, `_default_server`

---

### `mcp/specification_tools.py` - MCP tool for specification parsing

*40 lines · depends on: `mcp/_shared.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Parse a manifest into persisted source-referenced document trees.

**Contents**

- `register_specification_tools(server: MCPServer, ctx: McpContext) -> None` - Registers one tool. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_specification_tools.process_specification_manifest(manifest_path: str='specification-manifest.yaml') -> dict[str, Any]` *(mcp-tool)* - MCP tool: loads a manifest, parses every document into trees, persists them and returns them with their paths.

---

### `mcp/telemetry_tools.py` - MCP tools for telemetry, audit logs and metrics

*176 lines · depends on: `agent/retention.py`, `mcp/_shared.py`, `observability/telemetry_models.py` · used by: `mcp/server.py` · not re-exported at the package root*

**Role in the workflow.** Read-mostly access to the evidence ledgers. Chain checks return `integrity_chain_valid` plus an `integrity_failure` object (sequence, kind, message) when a chain is broken. `prune_runs` is the one tool here that deletes; it reports by default.

**Contents**

- `register_telemetry_tools(server: MCPServer, ctx: McpContext) -> None` - Registers ten tools. · *Called by:* `mcp/server.py::create_mcp_server`
  - `register_telemetry_tools.list_telemetry_runs(limit: int=100) -> dict[str, Any]` *(mcp-tool)* - MCP tool: lists runs.
  - `register_telemetry_tools.get_telemetry_events(run_id: str, after_sequence: int=0, limit: int=250) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns a page of events, whether the run's chain verifies and, when it does not, the first failure (sequence, kind and message).
  - `register_telemetry_tools.get_audit_log(run_id: str, limit: int=1000) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns bounded audit entries, chain validity and the first integrity failure when broken.
  - `register_telemetry_tools.render_audit_transcript(run_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: renders the Markdown transcript and returns its relative path, chain validity and the first failure when broken.
  - `register_telemetry_tools.register_metric_definition(definition: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: registers a versioned metric definition.
  - `register_telemetry_tools.list_metric_definitions() -> dict[str, Any]` *(mcp-tool)* - MCP tool: lists definitions.
  - `register_telemetry_tools.record_metric_observation(observation: dict[str, Any]) -> dict[str, Any]` *(mcp-tool)* - MCP tool: records an observed or explicitly unavailable metric.
  - `register_telemetry_tools.get_telemetry_metrics(run_id: str) -> dict[str, Any]` *(mcp-tool)* - MCP tool: returns a run's observations.
  - `register_telemetry_tools.prune_runs(older_than_seconds: float, dry_run: bool=True, keep_most_recent: int=0, max_runs: int | None=None, include_unfinished: bool=False, export_reports:...` *(mcp-tool)* - MCP tool: runs `RunRetention.prune` with a `RetentionPolicy` built from `older_than_seconds` (required), `keep_most_recent`, `max_runs`, `include_unfinished` and `export_reports`. `dry_run` defaults to true: the report lists the runs that would be pruned and nothing is written; with `dry_run` false each qualifying run's telemetry, audit transcript and tool-result files are deleted after a tombstone records them. Not exclusive: the stores lock themselves.
  - `register_telemetry_tools.create_telemetry_report(run_id: str, include_event_hashes: bool=False) -> dict[str, Any]` *(mcp-tool)* - MCP tool: builds the reproducible run report (`run-report-v2`); `include_event_hashes` adds the hash of every event.
