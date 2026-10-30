# Copyright (c) 2026 David Michael Indraputra

"""The ordered tool groups every MCP server registers, kept in one place."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ._shared import McpContext
from .agent_tools import register_agent_tools
from .controller_tools import register_controller_tools
from .orchestration_tools import register_orchestration_tools
from .project_state_tools import register_project_state_tools
from .run_tools import register_run_tools
from .specification_tools import register_specification_tools
from .telemetry_tools import register_telemetry_tools

TOOL_GROUPS: tuple[Callable[[Any, McpContext], None], ...] = (
    register_agent_tools,
    register_project_state_tools,
    register_orchestration_tools,
    register_run_tools,
    register_controller_tools,
    register_specification_tools,
    register_telemetry_tools,
)
