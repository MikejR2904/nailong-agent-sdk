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
from .authority import McpOperation

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_project_state_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "initialize_project_state", exclusive=False)
    def initialize_project_state(
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

    @ctx.tool(server, "get_project_state", exclusive=False, operation=McpOperation.READ)
    def get_project_state(project_id: str) -> dict[str, Any]:
        """Return current project state; history remains separate audit evidence."""

        try:
            return {
                "ok": True,
                "state": ctx.project_states.load(project_id).model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(
        server, "record_human_project_decision", exclusive=False, operation=McpOperation.APPROVAL
    )
    def record_human_project_decision(
        project_id: str,
        decision_id: str,
        content: str,
        status: str,
        evidence_id: str,
        source_spans: list[str] | None = None,
        state_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Record a human-owned decision with explicit source evidence, never a model claim.

        An approver credential must send `state_hash` (`state.state_hash` of
        `get_project_state`) and `issued_at_utc` (the instant the approver decided);
        `approver_label` is the caller's unverified note on who decided and is only
        recorded."""

        try:
            with (
                ctx.project_states.locked(project_id),
                ctx.gate.decide(
                    "record_human_project_decision",
                    subject=f'project state "{project_id}"',
                    approved=None,
                    binding_field="state_hash",
                    expected=state_hash,
                    actual=lambda: ctx.project_states.load(project_id).state_hash,
                    issued_at_utc=issued_at_utc,
                    label=approver_label,
                    reason=content,
                ) as receipt,
            ):
                state = ctx.project_states.apply(
                    project_id,
                    StateTransition(
                        kind=StateTransitionKind.HUMAN_DECISION,
                        actor=StateAuthority.HUMAN,
                        action_id=f"human-decision:{decision_id}",
                        payload={
                            "decision_id": decision_id,
                            "content": content,
                            "status": status,
                            **receipt.source().fields(),
                        },
                        evidence=[
                            StateEvidence(
                                evidence_id=evidence_id,
                                kind="human-decision-source",
                                source_spans=source_spans or [],
                            )
                        ],
                    ),
                )
            return {"ok": True, "state": state.model_dump(mode="json"), **receipt.fields()}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "resolve_project_question", exclusive=False, operation=McpOperation.APPROVAL)
    def resolve_project_question(
        project_id: str,
        question_id: str,
        resolution: str,
        evidence_id: str,
        source_spans: list[str] | None = None,
        state_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Close an open question with the human's recorded answer and its source evidence.

        An approver credential must send `state_hash` (`state.state_hash` of
        `get_project_state`) and `issued_at_utc` (the instant the approver decided);
        `approver_label` is the caller's unverified note on who decided and is only
        recorded."""

        try:
            with (
                ctx.project_states.locked(project_id),
                ctx.gate.decide(
                    "resolve_project_question",
                    subject=f'project state "{project_id}"',
                    approved=None,
                    binding_field="state_hash",
                    expected=state_hash,
                    actual=lambda: ctx.project_states.load(project_id).state_hash,
                    issued_at_utc=issued_at_utc,
                    label=approver_label,
                    reason=resolution,
                ) as receipt,
            ):
                state = ctx.project_states.apply(
                    project_id,
                    StateTransition(
                        kind=StateTransitionKind.QUESTION_RESOLVED,
                        actor=StateAuthority.HUMAN,
                        action_id=f"question-resolved:{question_id}",
                        payload={
                            "question_id": question_id,
                            "resolution": resolution,
                            **receipt.source().fields(),
                        },
                        evidence=[
                            StateEvidence(
                                evidence_id=evidence_id,
                                kind="human-question-resolution-source",
                                source_spans=source_spans or [],
                            )
                        ],
                    ),
                )
            return {"ok": True, "state": state.model_dump(mode="json"), **receipt.fields()}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "clear_project_blocker", exclusive=False, operation=McpOperation.APPROVAL)
    def clear_project_blocker(
        project_id: str,
        blocker_id: str,
        reason: str,
        evidence_id: str,
        source_spans: list[str] | None = None,
        state_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Clear a recorded blocker once its blocking requirement has been resolved.

        An approver credential must send `state_hash` (`state.state_hash` of
        `get_project_state`) and `issued_at_utc` (the instant the approver decided);
        `approver_label` is the caller's unverified note on who decided and is only
        recorded."""

        try:
            with (
                ctx.project_states.locked(project_id),
                ctx.gate.decide(
                    "clear_project_blocker",
                    subject=f'project state "{project_id}"',
                    approved=None,
                    binding_field="state_hash",
                    expected=state_hash,
                    actual=lambda: ctx.project_states.load(project_id).state_hash,
                    issued_at_utc=issued_at_utc,
                    label=approver_label,
                    reason=reason,
                ) as receipt,
            ):
                state = ctx.project_states.apply(
                    project_id,
                    StateTransition(
                        kind=StateTransitionKind.BLOCKER_CLEARED,
                        actor=StateAuthority.HUMAN,
                        action_id=f"blocker-cleared:{blocker_id}",
                        payload={
                            "blocker_id": blocker_id,
                            "reason": reason,
                            **receipt.source().fields(),
                        },
                        evidence=[
                            StateEvidence(
                                evidence_id=evidence_id,
                                kind="human-blocker-clearance-source",
                                source_spans=source_spans or [],
                            )
                        ],
                    ),
                )
            return {"ok": True, "state": state.model_dump(mode="json"), **receipt.fields()}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "open_project_question", exclusive=False)
    def open_project_question(
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
