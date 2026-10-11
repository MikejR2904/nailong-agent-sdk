# Copyright (c) 2026 David Michael Indraputra

"""Run one scoped agent task from parameters the caller supplies."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from ..foundations.contracts import (
    AgentDefinition,
    AgentResult,
    EpisodeKind,
    RuntimeOptions,
    ScopedAgentTask,
    StrictModel,
    ToolDefinition,
    ToolExecutionResult,
)
from ..foundations.identifiers import file_safe_name, require_unique
from ..foundations.paths import relative_to_base
from ..mcp.client_types import McpServerConfig, McpStdioServerConfig
from ..memory.context_projection import ContextProjectionPolicy
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..observability.trace_context import new_trace_id, parse_traceparent
from ..state.planning import ModelTier, PlanTask
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..tools.approvals import ApprovalRegistry, ApprovalRequest, ApprovalStatus
from ..tools.artifacts import ArtifactStore
from ..tools.core import DuckDuckGoHtmlClient, core_tool_definitions
from ..tools.policy import CapabilityGrant, CapabilityPolicy
from ..tools.registry import HarnessExecutionContext, HarnessToolExecutor, HarnessToolRegistry
from ..tools.supervisor import CommandTemplate, ProcessSupervisor
from ..tools.tools import InMemoryTaskToolExecutor, ToolExecutor, ToolInvocationContext
from .base_agent import AgentWatchdogPolicy, CancellationToken, PostToolHook, PreToolHook
from .model import AgentModel, FailoverAgentModel, ScriptedModel
from .openai_compatible import (
    JsonHttpTransport,
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
    StreamingJsonHttpTransport,
)
from .runtime import AgentRuntimeServices
from .verification import VerificationGateRegistry


class TaskOptionError(ValueError):
    """A run option cannot be honoured; raised before the agent starts."""


class ModelEndpointOptions(StrictModel):
    """Where and how to reach an OpenAI-compatible chat endpoint."""

    base_url: str = Field(min_length=1)
    api_key: SecretStr
    timeout_seconds: float = Field(default=120.0, gt=0, le=3_600)
    allow_insecure_http: bool = False
    propagate_trace_context: bool = False


class PermissionOptions(StrictModel):
    """The capabilities a run may use, and which of them the caller approves in advance."""

    role: str = Field(default="agent", min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=lambda: ["."])
    approved_capabilities: list[str] = Field(default_factory=list)
    approval_mode: Literal["block", "wait"] = "block"
    approval_timeout_seconds: float = Field(default=300.0, gt=0, le=86_400)

    @model_validator(mode="after")
    def lists_are_consistent(self) -> PermissionOptions:
        require_unique(self.capabilities, "permissions.capabilities")
        require_unique(self.allowed_paths, "permissions.allowed_paths")
        require_unique(self.approved_capabilities, "permissions.approved_capabilities")
        ungranted = sorted(set(self.approved_capabilities) - set(self.capabilities))
        if ungranted:
            raise ValueError(
                f"permissions.approved_capabilities {ungranted} must also be listed in "
                "permissions.capabilities."
            )
        return self


class TaskRunOptions(RuntimeOptions):
    """Everything a run takes from the caller: model, tools, permissions, workspace, limits."""

    model_endpoint: ModelEndpointOptions | None = None
    permissions: PermissionOptions | None = None
    builtin_tools: list[str] = Field(default_factory=list)
    workspace: str | None = None
    declared_output_paths: list[str] = Field(default_factory=list)
    command_templates: list[CommandTemplate] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    web_search: Literal["duckduckgo-html"] | None = None
    traceparent: str | None = None

    @field_validator("traceparent")
    @classmethod
    def traceparent_is_a_w3c_header_value(cls, value: str | None) -> str | None:
        if value is not None:
            parse_traceparent(value)
        return value

    @model_validator(mode="after")
    def options_are_consistent(self) -> TaskRunOptions:
        if self.model_endpoint is not None and self.scripted_turns:
            raise ValueError(
                "model_endpoint and scripted_turns are mutually exclusive: a live run takes its "
                "turns from the model."
            )
        if self.model_endpoint is not None and self.mode == "deterministic":
            raise ValueError('mode "deterministic" conflicts with model_endpoint.')
        require_unique(self.builtin_tools, "builtin_tools")
        require_unique([template.name for template in self.command_templates], "command_templates")
        require_unique([server.name for server in self.mcp_servers], "mcp_servers")
        needing_permissions = {
            "builtin_tools": bool(self.builtin_tools),
            "declared_output_paths": bool(self.declared_output_paths),
            "command_templates": bool(self.command_templates),
            "mcp_servers": bool(self.mcp_servers),
            "web_search": self.web_search is not None,
        }
        if self.permissions is None:
            given = [name for name, present in needing_permissions.items() if present]
            if given:
                raise ValueError(
                    f"{', '.join(given)} require permissions: without a capability grant every "
                    "tool call is blocked."
                )
            return self
        if self.permissions.approval_mode == "wait":
            wait = self.permissions.approval_timeout_seconds
            for name in ("tool_call_timeout_seconds", "run_deadline_seconds"):
                limit = getattr(self, name)
                if limit is not None and limit < wait:
                    raise ValueError(
                        f"{name} ({limit:g}) is shorter than permissions.approval_timeout_seconds "
                        f"({wait:g}), so the watchdog would end the run before the approval wait "
                        f"does; raise {name} or lower permissions.approval_timeout_seconds."
                    )
        granted = set(self.permissions.capabilities)
        for server in self.mcp_servers:
            if f"mcp.{server.name}" not in granted:
                raise ValueError(
                    f'mcp_servers lists "{server.name}" but permissions.capabilities does not '
                    f'grant "mcp.{server.name}".'
                )
        return self

    @property
    def is_live(self) -> bool:
        return self.model_endpoint is not None


class _RunToken:
    def __init__(self, outer: CancellationToken | None) -> None:
        self._event = threading.Event()
        self._outer = outer
        self.approvals = ApprovalRegistry()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set() or (self._outer is not None and self._outer.is_cancelled())


class AgentTaskRunner:
    """Build a governed agent from caller parameters, run one task and report the result."""

    def __init__(
        self,
        services: AgentRuntimeServices,
        *,
        allow_process_options: bool = True,
        process_options_hint: str = "",
        transport: JsonHttpTransport | None = None,
        streaming_transport: StreamingJsonHttpTransport | None = None,
    ) -> None:
        self._services = services
        self._allow_process_options = allow_process_options
        self._process_options_hint = process_options_hint
        self._transport = transport
        self._streaming_transport = streaming_transport
        self._running: dict[str, _RunToken] = {}
        self._running_lock = threading.Lock()

    def cancel(self, task_id: str) -> bool:
        with self._running_lock:
            token = self._running.get(task_id)
        if token is None:
            return False
        token.cancel()
        return True

    def approvals(self, task_id: str) -> list[ApprovalRequest]:
        return self._running_token(task_id).approvals.list(task_id)

    def decide_approval(
        self, task_id: str, approval_id: str, approved: bool, reason: str | None = None
    ) -> ApprovalRequest:
        registry = self._running_token(task_id).approvals
        request = registry.get(approval_id)
        if request is None or request.run_id != task_id:
            raise TaskOptionError(
                f'Agent task "{task_id}" has no approval request "{approval_id}".'
            )
        return registry.submit(approval_id, approved, reason)

    def _running_token(self, task_id: str) -> _RunToken:
        with self._running_lock:
            token = self._running.get(task_id)
        if token is None:
            raise TaskOptionError(f'No agent task "{task_id}" is running.')
        return token

    async def run(
        self,
        definition: AgentDefinition,
        task: ScopedAgentTask,
        options: TaskRunOptions | None = None,
        *,
        model: AgentModel | None = None,
        verification_gates: VerificationGateRegistry | None = None,
        pre_tool_hooks: Sequence[PreToolHook] = (),
        post_tool_hooks: Sequence[PostToolHook] = (),
        cancellation: CancellationToken | None = None,
    ) -> AgentResult:
        options = options or TaskRunOptions()
        self._require_process_options_allowed(options)
        live = model is not None or options.is_live
        token = _RunToken(cancellation)
        with self._running_lock:
            if task.id in self._running:
                raise TaskOptionError(f'Agent task "{task.id}" is already running.')
            self._running[task.id] = token
        manager = None
        try:
            manager = await self._connect_mcp(options)
            caller = parse_traceparent(options.traceparent) if options.traceparent else None
            context = TelemetryContext(
                run_id=task.id,
                task_id=task.id,
                agent_id=definition.identity,
                trace_id=caller.trace_id if caller else new_trace_id(),
                span_id=caller.parent_span_id if caller else None,
            )
            run_definition, executor = self._prepare_tools(
                definition, task, options, manager, token, context, live=live
            )
            agent_model = model or self._model_for(run_definition, options)
            permissions = options.permissions
            self._emit(
                "run.created",
                context,
                "started",
                {
                    "mode": "live" if live else "deterministic",
                    "model_binding": definition.model_binding.model_dump(mode="json"),
                    "capabilities": list(permissions.capabilities) if permissions else [],
                    "builtin_tools": list(options.builtin_tools),
                    "command_templates": [template.name for template in options.command_templates],
                    "mcp_servers": [server.name for server in options.mcp_servers],
                },
            )
            agent = self._services.create_agent(
                run_definition,
                agent_model,
                tool_executor=executor,
                verification_gates=verification_gates,
                pre_tool_hooks=pre_tool_hooks,
                post_tool_hooks=post_tool_hooks,
                watchdog_policy=AgentWatchdogPolicy(
                    run_deadline_seconds=options.run_deadline_seconds,
                    model_turn_timeout_seconds=options.model_turn_timeout_seconds,
                    tool_call_timeout_seconds=options.tool_call_timeout_seconds,
                    verification_timeout_seconds=options.verification_timeout_seconds,
                ),
                context_projection_policy=ContextProjectionPolicy(
                    **{
                        key: value
                        for key, value in {
                            "context_token_budget": options.context_token_budget,
                            "episode_token_budget": options.episode_token_budget,
                            "tool_result_preview_chars": options.tool_result_preview_chars,
                        }.items()
                        if value is not None
                    }
                ),
                project_state_projection_policy=ProjectStateProjectionPolicy(
                    **(
                        {"token_budget": options.project_state_token_budget}
                        if options.project_state_token_budget is not None
                        else {}
                    )
                ),
                telemetry_context=context,
            )
            result = await agent.run(task, token)
            self._emit(
                "run.completed" if result.status.value == "completed" else "run.terminated",
                context,
                result.status.value,
                {"iterations": result.iterations, "reason": result.reason},
            )
            return result
        finally:
            with self._running_lock:
                self._running.pop(task.id, None)
            if manager is not None:
                await manager.close()

    def _require_process_options_allowed(self, options: TaskRunOptions) -> None:
        if self._allow_process_options:
            return
        requested = []
        if options.command_templates:
            requested.append("command_templates")
        if any(isinstance(server, McpStdioServerConfig) for server in options.mcp_servers):
            requested.append("a stdio entry in mcp_servers")
        if requested:
            hint = f" {self._process_options_hint}" if self._process_options_hint else ""
            raise TaskOptionError(
                f"{' and '.join(requested)} would start processes on the host running this "
                f"agent, and this runner does not allow that.{hint}"
            )

    async def _connect_mcp(self, options: TaskRunOptions) -> Any:
        if not options.mcp_servers:
            return None
        from ..mcp.client import McpClientManager
        from ..mcp.client_types import McpConnectionState

        manager = McpClientManager(list(options.mcp_servers))
        try:
            await manager.connect_all()
            failed = [
                f'"{status.name}": {status.detail}'
                for status in manager.list_statuses()
                if status.state is not McpConnectionState.CONNECTED
            ]
            if failed:
                raise TaskOptionError("MCP servers did not connect: " + "; ".join(failed))
        except BaseException:
            await manager.close()
            raise
        return manager

    def _prepare_tools(
        self,
        definition: AgentDefinition,
        task: ScopedAgentTask,
        options: TaskRunOptions,
        manager: Any,
        token: _RunToken,
        telemetry_context: TelemetryContext,
        *,
        live: bool,
    ) -> tuple[AgentDefinition, ToolExecutor]:
        if options.permissions is None and not live:
            return definition, InMemoryTaskToolExecutor()
        permissions = options.permissions or PermissionOptions()
        registry = HarnessToolRegistry()
        extra_tools = self._builtin_definitions(options.builtin_tools)
        if manager is not None:
            from ..mcp.client_bridge import mcp_tools_as_extensions, registered_tool_name

            extensions, handlers = mcp_tools_as_extensions(manager)
            registry = HarnessToolRegistry.with_extensions(extensions, handlers=handlers)
            extra_tools += [
                _mcp_tool_definition(info, registered_tool_name(info.server_name, info.name))
                for info in manager.list_tools()
            ]
        declared = {tool.name for tool in definition.tools}
        tools = [*definition.tools, *(tool for tool in extra_tools if tool.name not in declared)]
        require_unique([tool.name for tool in tools], "agent tool names")
        unknown = [tool.name for tool in tools if registry.resolve(tool.name) is None]
        if unknown:
            raise TaskOptionError(
                f"Tools {unknown} have no implementation: the built-in tools are "
                f"{list(registry.names())}, and an MCP server's tools are named "
                "mcp__<server>__<tool>."
            )
        workspace = self._workspace(task.id, options.workspace)
        approvals = token.approvals
        approval_ids: dict[str, str] = {}
        for capability in permissions.approved_capabilities:
            reason = "Approved by the caller in the run options."
            request = approvals.request(task.id, task.id, capability, reason)
            approvals.submit(request.approval_id, True, reason)
            approval_ids[capability] = request.approval_id
        locked = task.locked_interface
        plan_task = PlanTask(
            task_id=task.id,
            scope=task.scope.label,
            locked_interface=locked if isinstance(locked, dict) else {"value": locked},
            instructions=task.instructions,
            acceptance_criteria="\n".join(task.acceptance_criteria),
            model_tier=ModelTier.STANDARD,
        )
        context = HarnessExecutionContext(
            run_id=task.id,
            node_id=task.id,
            role=permissions.role,
            plan_task=plan_task,
            run_root=workspace,
            artifacts=ArtifactStore(workspace),
            policy=CapabilityPolicy(
                [
                    CapabilityGrant(
                        role=permissions.role,
                        capabilities=list(permissions.capabilities),
                        allowed_paths=list(permissions.allowed_paths),
                    )
                ]
            ),
            approvals=approvals,
            supervisor=ProcessSupervisor(
                list(options.command_templates),
                telemetry=self._services.telemetry,
                telemetry_context_factory=lambda _template: telemetry_context,
            ),
            declared_output_paths=tuple(options.declared_output_paths),
            approval_ids_by_capability=approval_ids,
            result_journal=self._services.result_journal,
            search_client=DuckDuckGoHtmlClient() if options.web_search else None,
        )
        executor: ToolExecutor = HarnessToolExecutor(registry, context)
        if permissions.approval_mode == "wait":
            executor = _ApprovalWaitingExecutor(
                executor,
                approvals,
                permissions.approval_timeout_seconds,
                lambda event_type, status, payload: self._emit(
                    event_type, telemetry_context, status, payload
                ),
            )
        return definition.model_copy(update={"tools": tools}), executor

    @staticmethod
    def _builtin_definitions(names: Sequence[str]) -> list[ToolDefinition]:
        available = {tool.name: tool for tool in core_tool_definitions()}
        unknown = [name for name in names if name not in available]
        if unknown:
            raise TaskOptionError(
                f"builtin_tools {unknown} are not built-in tools; the built-in tools are "
                f"{sorted(available)}."
            )
        return [available[name] for name in names]

    def workspace_for(self, task_id: str, requested: str | None = None) -> Path:
        path = self._workspace_path(task_id, requested)
        if not path.is_dir():
            label = requested if requested is not None else f"workspaces/{file_safe_name(task_id)}"
            raise TaskOptionError(
                f'Agent task "{task_id}" has no workspace directory "{label}": nothing has been '
                "written there yet."
            )
        return path

    def _workspace(self, task_id: str, requested: str | None) -> Path:
        path = self._workspace_path(task_id, requested)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _workspace_path(self, task_id: str, requested: str | None) -> Path:
        root = self._services.run_root
        if requested is None:
            path = root / "workspaces" / file_safe_name(task_id)
        else:
            windows = PureWindowsPath(requested)
            if (
                not requested.strip()
                or requested.startswith(("/", "\\"))
                or windows.is_absolute()
                or windows.drive
            ):
                raise TaskOptionError(
                    f'workspace "{requested}" must be a non-empty path relative to the service '
                    "run root."
                )
            path = (root / requested).resolve()
            try:
                parts = relative_to_base(path, root).parts
            except ValueError as error:
                raise TaskOptionError(
                    f'workspace "{requested}" escapes the service run root.'
                ) from error
            if not parts or parts[0].startswith("."):
                raise TaskOptionError(
                    f'workspace "{requested}" is the service run root or a hidden directory '
                    "inside it, which hold the SDK's own records; name another subdirectory."
                )
        return path

    def _model_for(self, definition: AgentDefinition, options: TaskRunOptions) -> AgentModel:
        endpoint_options = options.model_endpoint
        if endpoint_options is None:
            return ScriptedModel(options.scripted_turns)
        try:
            endpoint = OpenAICompatibleEndpoint(
                base_url=endpoint_options.base_url,
                api_key=endpoint_options.api_key.get_secret_value(),
                timeout_seconds=endpoint_options.timeout_seconds,
                allow_insecure_http=endpoint_options.allow_insecure_http,
                propagate_trace_context=endpoint_options.propagate_trace_context,
            )
        except ValueError as error:
            raise TaskOptionError(f"model_endpoint is invalid: {error}") from error
        binding = definition.model_binding

        def adapter(provider: str, model: str) -> OpenAICompatibleAgentModel:
            return OpenAICompatibleAgentModel(
                endpoint,
                provider=provider,
                model=model,
                transport=self._transport,
                streaming_transport=self._streaming_transport,
            )

        primary = adapter(binding.provider, binding.model)
        if not binding.fallbacks:
            return primary
        return FailoverAgentModel(
            [primary, *(adapter(item.provider, item.model) for item in binding.fallbacks)],
            bindings=[binding, *binding.fallbacks],
        )

    def _emit(self, event_type: str, context: TelemetryContext, status: str, payload: Any) -> None:
        self._services.telemetry.emit(
            event_type,
            context,
            actor=TelemetryActor(kind="system", identifier="task-runner", role="runtime"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status=status,
            payload=payload,
        )


_APPROVAL_POLL_SECONDS = 0.05


class _ApprovalWaitingExecutor(ToolExecutor):
    def __init__(
        self,
        inner: ToolExecutor,
        approvals: ApprovalRegistry,
        timeout_seconds: float,
        record: Callable[[str, str, dict[str, Any]], None],
    ) -> None:
        self._inner = inner
        self._approvals = approvals
        self._timeout_seconds = timeout_seconds
        self._record = record

    async def execute(
        self, tool: ToolDefinition, invocation: ToolInvocationContext
    ) -> ToolExecutionResult:
        result = await self._inner.execute(tool, invocation)
        approval_id = _awaited_approval_id(result)
        if approval_id is None:
            return result
        request = self._approvals.get(approval_id)
        if request is None:
            return result
        self._record(
            "approval.requested",
            "pending",
            {
                "approval_id": approval_id,
                "capability": request.capability,
                "tool": tool.name,
                "tool_call_id": invocation.call.id,
                "timeout_seconds": self._timeout_seconds,
            },
        )
        request = await self._decision(approval_id)
        if request.status is ApprovalStatus.PENDING:
            self._record_decision(request, "timed-out")
            return result.model_copy(
                update={
                    "error": f'Approval "{approval_id}" for capability "{request.capability}" '
                    f"was not decided within {self._timeout_seconds:g}s."
                }
            )
        self._record_decision(request, request.status.value)
        if request.status is ApprovalStatus.APPROVED:
            return await self._inner.execute(tool, invocation)
        detail = f": {request.decision_reason}" if request.decision_reason else "."
        return result.model_copy(
            update={
                "error": f'Approval "{approval_id}" for capability "{request.capability}" '
                f"was rejected{detail}"
            }
        )

    async def _decision(self, approval_id: str) -> ApprovalRequest:
        loop = asyncio.get_running_loop()
        end = loop.time() + self._timeout_seconds
        while True:
            request = self._approvals.get(approval_id)
            if request is None:
                raise TaskOptionError(
                    f'Approval request "{approval_id}" disappeared while waiting.'
                )
            remaining = end - loop.time()
            if request.status is not ApprovalStatus.PENDING or remaining <= 0:
                return request
            await asyncio.sleep(min(_APPROVAL_POLL_SECONDS, remaining))

    def _record_decision(self, request: ApprovalRequest, status: str) -> None:
        self._record(
            "approval.decided",
            status,
            {
                "approval_id": request.approval_id,
                "capability": request.capability,
                "decision_reason": request.decision_reason,
            },
        )


def _awaited_approval_id(result: ToolExecutionResult) -> str | None:
    if result.status != "blocked" or not isinstance(result.output, dict):
        return None
    approval_id = result.output.get("approval_id")
    return approval_id if isinstance(approval_id, str) else None


def _mcp_tool_definition(info: Any, registered_name: str) -> ToolDefinition:
    try:
        return ToolDefinition(
            name=registered_name,
            description=info.description.strip()
            or f'Tool "{info.name}" of MCP server "{info.server_name}".',
            input_schema=info.input_schema,
            episode_kind=EpisodeKind.ACTION,
        )
    except ValueError as error:
        raise TaskOptionError(
            f'MCP tool "{info.server_name}::{info.name}" cannot be offered to the agent: {error}'
        ) from error
