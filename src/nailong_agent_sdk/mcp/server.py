# Copyright (c) 2026 David Michael Indraputra

"""MCP server exposing the Python BaseAgent and deterministic harness to TypeScript."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server import MCPServer

from ..observability.audit_log import AuditTranscriptStore
from ..observability.metrics import register_standard_metric_definitions
from ..observability.telemetry_store import TelemetryStore
from ..specifications.gate import Gate1ArtifactStore, SpecificationGate
from ..specifications.git_versioning import SpecificationVersionService
from ..specifications.preprocessing import SpecificationPreprocessor
from ..state.controller_runtime import ControllerRuntime
from ..state.harness_coordinator import HarnessCoordinator
from ..state.planning import PlanValidator
from ..state.project_state_store import FileProjectStateStore
from ._shared import McpContext
from .agent_tools import register_agent_tools
from .controller_tools import register_controller_tools
from .git_tools import register_git_tools
from .orchestration_tools import register_orchestration_tools
from .project_state_tools import register_project_state_tools
from .run_tools import register_run_tools
from .specification_tools import register_specification_tools
from .telemetry_tools import register_telemetry_tools

SERVER_NAME = "agent-design-python-runtime"
SERVER_VERSION = "0.17.0"


def create_mcp_server(run_root: Path | None = None) -> MCPServer:
    """Build the BaseAgent and typed harness MCP surface without a network listener."""

    # Defer MCP initialization so ordinary SDK imports do not create runtime
    # stores or require the optional server implementation.
    from mcp.server import MCPServer

    resolved_run_root = run_root or Path(os.environ.get("AGENT_RUNTIME_RUN_ROOT", ".agent-runtime"))
    telemetry = TelemetryStore(resolved_run_root)
    register_standard_metric_definitions(telemetry)
    controller_runtime = ControllerRuntime(resolved_run_root, telemetry=telemetry)
    specification_root = Path(
        os.environ.get("AGENT_SPECIFICATION_ROOT", str(resolved_run_root / "specifications"))
    )
    ctx = McpContext(
        run_root=resolved_run_root,
        coordinator=HarnessCoordinator(resolved_run_root),
        telemetry=telemetry,
        audit_logs=AuditTranscriptStore(resolved_run_root),
        project_states=FileProjectStateStore(resolved_run_root),
        controller_runtime=controller_runtime,
        specification_root=specification_root,
        preprocessor=SpecificationPreprocessor(specification_root),
        specification_gate=SpecificationGate(),
        gate_store=Gate1ArtifactStore(specification_root),
        plan_validator=PlanValidator(),
        versioning=SpecificationVersionService(resolved_run_root),
    )

    server = MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Python implementation of the Agent-Assisted Design BaseAgent and deterministic "
            "typed-DAG harness. Agent coordination occurs only through typed graph state."
        ),
    )

    register_agent_tools(server, ctx)
    register_project_state_tools(server, ctx)
    register_orchestration_tools(server, ctx)
    register_run_tools(server, ctx)
    register_controller_tools(server, ctx)
    register_specification_tools(server, ctx)
    register_telemetry_tools(server, ctx)
    register_git_tools(server, ctx)

    return server


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
    """Run the Python agent runtime as a loopback-only Streamable HTTP service."""

    host = os.environ.get("AGENT_RUNTIME_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENT_RUNTIME_PORT", "8001"))
    create_mcp_server().run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
