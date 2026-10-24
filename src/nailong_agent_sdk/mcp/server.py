# Copyright (c) 2026 David Michael Indraputra

"""MCP server exposing the Python BaseAgent and deterministic harness to TypeScript."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server import MCPServer

from ..agent.retention import RunRetention
from ..agent.run_checkpointer import RunHeadCheckpointer
from ..agent.runtime import AgentRuntimeServices
from ..agent.task_runner import AgentTaskRunner
from ..foundations.errors import AgentSdkError
from ..foundations.version import PACKAGE_NAME, package_version
from ..memory.context_projection import FileToolResultJournal
from ..observability.audit_log import AuditTranscriptStore
from ..observability.metrics import register_standard_metric_definitions
from ..observability.telemetry_store import TelemetryStore
from ..specifications.preprocessing import SpecificationPreprocessor
from ..state.controller_runtime import ControllerRuntime
from ..state.harness_coordinator import HarnessCoordinator
from ..state.planning import PlanValidator
from ..state.project_state_store import FileProjectStateStore
from ..tools.approval_audit import TelemetryApprovalSink
from ._shared import McpContext
from .arguments import ToolArgumentGuard
from .authority import McpAuthorityPolicy
from .security import (
    BearerTokenMiddleware,
    ServerConfigurationError,
    load_http_service_settings,
)
from .tool_groups import TOOL_GROUPS

SERVER_NAME = PACKAGE_NAME
SERVER_VERSION = package_version()
PROCESS_OPTIONS_ENVIRONMENT_VARIABLE = "AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS"
RETENTION_ANCHOR_ENVIRONMENT_VARIABLE = "AGENT_RUNTIME_RETENTION_ANCHOR_PATH"


def create_mcp_server(
    run_root: Path | None = None,
    *,
    audit_max_open_handles: int | None = None,
    audit_max_payload_chars: int | None = None,
    authority: McpAuthorityPolicy | None = None,
    checkpointer: RunHeadCheckpointer | None = None,
) -> MCPServer:
    """Build the BaseAgent and typed harness MCP surface without a network listener."""

    # Defer MCP initialization so ordinary SDK imports do not create runtime
    # stores or require the optional server implementation.
    from mcp.server import MCPServer

    resolved_run_root = run_root or Path(os.environ.get("AGENT_RUNTIME_RUN_ROOT", ".agent-runtime"))
    if checkpointer is not None:
        checkpointer.require_outside(resolved_run_root.resolve())
    telemetry = TelemetryStore(resolved_run_root)
    register_standard_metric_definitions(telemetry)
    coordinator = HarnessCoordinator(
        resolved_run_root,
        approval_sink=TelemetryApprovalSink(telemetry),
        anchorer=checkpointer,
    )
    project_states = FileProjectStateStore(resolved_run_root)
    controller_runtime = ControllerRuntime(
        resolved_run_root,
        telemetry=telemetry,
        coordinator=coordinator,
        project_state_store=project_states,
    )
    specification_root = Path(
        os.environ.get("AGENT_SPECIFICATION_ROOT", str(resolved_run_root / "specifications"))
    )
    audit_options: dict[str, int] = {}
    if audit_max_open_handles is not None:
        audit_options["max_open_handles"] = audit_max_open_handles
    if audit_max_payload_chars is not None:
        audit_options["max_payload_chars"] = audit_max_payload_chars
    audit_logs = AuditTranscriptStore(resolved_run_root, **audit_options)
    result_journal = FileToolResultJournal(resolved_run_root)
    task_runner = AgentTaskRunner(
        AgentRuntimeServices(
            run_root=resolved_run_root.resolve(),
            result_journal=result_journal,
            project_state_store=project_states,
            telemetry=telemetry,
            audit_logs=audit_logs,
            checkpointer=checkpointer,
        ),
        allow_process_options=os.environ.get(PROCESS_OPTIONS_ENVIRONMENT_VARIABLE) == "1",
        process_options_hint=(
            f"Start the service with {PROCESS_OPTIONS_ENVIRONMENT_VARIABLE}=1 to allow it."
        ),
    )
    ctx = McpContext(
        run_root=resolved_run_root,
        coordinator=coordinator,
        telemetry=telemetry,
        audit_logs=audit_logs,
        project_states=project_states,
        controller_runtime=controller_runtime,
        specification_root=specification_root,
        preprocessor=SpecificationPreprocessor(specification_root),
        plan_validator=PlanValidator(),
        task_runner=task_runner,
        retention=RunRetention(
            resolved_run_root,
            telemetry=telemetry,
            audit_logs=audit_logs,
            result_journal=result_journal,
            anchor_path=_anchor_path(),
        ),
        authority=authority or McpAuthorityPolicy(),
    )

    server = MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Python implementation of the Agent-Assisted Design BaseAgent and deterministic "
            "typed-DAG harness. Agent coordination occurs only through typed graph state."
        ),
    )

    for register in TOOL_GROUPS:
        register(server, ctx)
    server.middleware.append(ToolArgumentGuard(server))

    return server


def _anchor_path() -> Path | None:
    configured = os.environ.get(RETENTION_ANCHOR_ENVIRONMENT_VARIABLE, "").strip()
    return Path(configured) if configured else None


_default_server: MCPServer | None = None


def __getattr__(name: str) -> Any:
    """Construct the default MCP server on explicit attribute access only."""

    global _default_server
    if name == "mcp":
        if _default_server is None:
            _default_server = create_mcp_server()
        return _default_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Advertise the lazy ``mcp`` export without constructing its runtime stores."""

    return sorted({*globals(), "mcp"})


def main() -> None:
    """Run the Python agent runtime as a bearer-authenticated Streamable HTTP service."""

    try:
        settings = load_http_service_settings(os.environ)
    except ServerConfigurationError as error:
        print(f"nailong-agent-sdk MCP server cannot start: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    import uvicorn

    notice = settings.approval_notice()
    if notice is not None:
        print(f"nailong-agent-sdk MCP server: {notice}", file=sys.stderr, flush=True)
    checkpoint_notice = settings.checkpoint_notice()
    if checkpoint_notice is not None:
        print(f"nailong-agent-sdk MCP server: {checkpoint_notice}", file=sys.stderr, flush=True)
    try:
        server = create_mcp_server(
            audit_max_open_handles=settings.audit_max_open_handles,
            audit_max_payload_chars=settings.audit_max_payload_chars,
            authority=settings.authority_policy(),
            checkpointer=settings.checkpointer(),
        )
    except (AgentSdkError, OSError, ValueError) as error:
        print(f"nailong-agent-sdk MCP server cannot start: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=settings.transport_security(),
        host=settings.host,
    )
    app.add_middleware(
        BearerTokenMiddleware, token=settings.token, approver_token=settings.approver_token
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        **settings.ssl_options(),
    )


if __name__ == "__main__":
    main()
