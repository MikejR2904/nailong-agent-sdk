# Copyright (c) 2026 David Michael Indraputra

"""The short path: a model, instructions and a few tools, then ``await agent.run(prompt)``.

Everything here composes existing SDK parts. It adds no tool, permission or model of its own:
the caller hands over each one, and the full API underneath stays available.
"""

from __future__ import annotations

from .agent import Agent, Answer
from .cancel import CancelToken
from .checks import OutputCheck
from .core_tools import CoreTools, core_tools
from .dev_tools import dev_tools
from .forge import Forge, Reviewer, ToolProposal
from .hooks import AfterTool, BeforeTool, ToolEvent
from .mcp_tools import McpTools, connect_mcp
from .models import OPENAI_COMPATIBLE_BASE_URLS, ModelSetup, anthropic_messages, openai_chat
from .notebook_tools import notebook_tools
from .planner import Planner, PlannerError, PlanProposal, Role, TeamResult
from .sessions import Session
from .skill_files import (
    SkillDefinition,
    discover_skills,
    load_skill_definition,
    render_skill,
    skill_context_of,
    skill_file_text,
)
from .skills import skill
from .toolbox import ApprovalRequest, Approver, Refusal, Toolbox, ToolGroup
from .tools import FunctionTool, tool

__all__ = [
    "OPENAI_COMPATIBLE_BASE_URLS",
    "AfterTool",
    "Agent",
    "Answer",
    "ApprovalRequest",
    "Approver",
    "BeforeTool",
    "CancelToken",
    "CoreTools",
    "Forge",
    "FunctionTool",
    "McpTools",
    "ModelSetup",
    "OutputCheck",
    "PlanProposal",
    "Planner",
    "PlannerError",
    "Refusal",
    "Reviewer",
    "Role",
    "Session",
    "TeamResult",
    "ToolEvent",
    "ToolProposal",
    "ToolGroup",
    "Toolbox",
    "anthropic_messages",
    "connect_mcp",
    "core_tools",
    "dev_tools",
    "notebook_tools",
    "openai_chat",
    "skill",
    "SkillDefinition",
    "discover_skills",
    "load_skill_definition",
    "render_skill",
    "skill_context_of",
    "skill_file_text",
    "tool",
]
