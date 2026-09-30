# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for project working-memory initialization, decisions, and open questions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..state.project_state_models import (
    QuestionOwner,
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_project_state_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="initialize_project_state", structured_output=True)
    async def initialize_project_state(
        project_id: str,
        stage_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or load a bounded state-first working-memory object for one project."""

        try:
            state = ctx.project_states.ensure(
                project_id, StageStateSchema.model_validate(stage_schema)
            )
            return {"ok": True, "state": state.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_project_state", structured_output=True)
    async def get_project_state(project_id: str) -> dict[str, Any]:
        """Return current project state; history remains separate audit evidence."""

        try:
            return {
                "ok": True,
                "state": ctx.project_states.load(project_id).model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="record_human_project_decision", structured_output=True)
    async def record_human_project_decision(
        project_id: str,
        decision_id: str,
        content: str,
        status: str,
        evidence_id: str,
        source_spans: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a human-owned decision with explicit source evidence, never a model claim."""

        try:
            state = ctx.project_states.apply(
                project_id,
                StateTransition(
                    kind=StateTransitionKind.HUMAN_DECISION,
                    actor=StateAuthority.HUMAN,
                    action_id=f"human-decision:{decision_id}",
                    payload={"decision_id": decision_id, "content": content, "status": status},
                    evidence=[
                        StateEvidence(
                            evidence_id=evidence_id,
                            kind="human-decision-source",
                            source_spans=source_spans or [],
                        )
                    ],
                ),
            )
            return {"ok": True, "state": state.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="open_project_question", structured_output=True)
    async def open_project_question(
        project_id: str,
        question_id: str,
        content: str,
        owner: str,
        evidence_id: str,
        source_spans: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record an explicit unresolved question instead of permitting an inferred state claim."""

        try:
            parsed_owner = QuestionOwner(owner)
            state = ctx.project_states.apply(
                project_id,
                StateTransition(
                    kind=StateTransitionKind.QUESTION_OPENED,
                    actor=StateAuthority.HARNESS,
                    action_id=f"question:{question_id}",
                    payload={
                        "question_id": question_id,
                        "content": content,
                        "owner": parsed_owner.value,
                    },
                    evidence=[
                        StateEvidence(
                            evidence_id=evidence_id,
                            kind="unresolved-question-source",
                            source_spans=source_spans or [],
                        )
                    ],
                ),
            )
            return {"ok": True, "state": state.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
