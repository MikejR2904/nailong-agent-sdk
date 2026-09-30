# Copyright (c) 2026 David Michael Indraputra

"""Pure deterministic construction of model-facing initial context."""

from __future__ import annotations

from copy import deepcopy

from ..foundations.contracts import AgentDefinition, AgentPrompt, PromptSection, ScopedAgentTask


def assemble_initial_context(definition: AgentDefinition, task: ScopedAgentTask) -> AgentPrompt:
    """Build context in the framework's mandated order.

    The ordering is grounded in the systems-design framework (updated PDF, p. 61):
    identity/instructions; scoped task fields including exact locked interface;
    matched skills; then only the declared tool definitions.
    """

    return AgentPrompt(
        sections=[
            PromptSection(kind="identity", value=definition.identity),
            PromptSection(
                kind="instructions", value=definition.instructions.model_dump(mode="json")
            ),
            PromptSection(
                kind="task",
                value={
                    "scope": deepcopy(task.scope.model_dump(mode="json")),
                    "locked_interface": deepcopy(task.locked_interface),
                    "instructions": task.instructions,
                    "acceptance_criteria": deepcopy(task.acceptance_criteria),
                },
            ),
            PromptSection(
                kind="skills",
                value=[skill.model_dump(mode="json") for skill in task.skills],
            ),
            PromptSection(
                kind="tools",
                value=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": deepcopy(tool.input_schema),
                    }
                    for tool in definition.tools
                ],
            ),
        ]
    )
