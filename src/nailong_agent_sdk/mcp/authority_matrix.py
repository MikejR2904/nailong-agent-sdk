# Copyright (c) 2026 David Michael Indraputra

"""One export of who may do what: state transitions, MCP credentials and MCP tool classes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..observability.telemetry_models import TelemetryAuthority
from ..state.project_state_engine import QUESTION_RESOLUTION_AUTHORITY, TRANSITION_AUTHORITY
from ..state.project_state_models import StateAuthority, StateTransitionKind
from ._shared import McpContext
from .authority import (
    McpAuthority,
    McpAuthorityPolicy,
    McpAuthorizationError,
    McpOperation,
    McpPrincipal,
)
from .tool_groups import TOOL_GROUPS

AUTHORITY_MATRIX_SCHEMA = "authority-matrix-v1"
_PROBE_TOOL = "authority-matrix-probe"
_CREDENTIAL_POLICIES: dict[str, McpAuthorityPolicy] = {
    "approver-credential-configured": McpAuthorityPolicy(approver_configured=True),
    "host-credential-may-approve": McpAuthorityPolicy(allow_host_approvals=True),
    "approvals-disabled": McpAuthorityPolicy(),
}


class _ToolRecorder:
    def tool(
        self, *, name: str, structured_output: bool
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            return function

        return decorate


def registered_tool_operations() -> dict[str, McpOperation]:
    unused: Any = None
    context = McpContext(
        run_root=Path("."),
        coordinator=unused,
        telemetry=unused,
        audit_logs=unused,
        project_states=unused,
        controller_runtime=unused,
        specification_root=Path("."),
        preprocessor=unused,
        plan_validator=unused,
        task_runner=unused,
        retention=unused,
    )
    recorder = _ToolRecorder()
    for register in TOOL_GROUPS:
        register(recorder, context)
    return dict(context.operations)


def authority_matrix() -> dict[str, Any]:
    return {
        "schema_version": AUTHORITY_MATRIX_SCHEMA,
        "telemetry_authorities": [item.value for item in TelemetryAuthority],
        "state_authorities": [item.value for item in StateAuthority],
        "state_transitions": _state_transitions(),
        "mcp": {
            "credentials": [item.value for item in McpAuthority],
            "operations": [item.value for item in McpOperation],
            "credential_policies": _credential_policies(),
            "tools": {
                name: operation.value
                for name, operation in sorted(registered_tool_operations().items())
            },
        },
    }


def _state_transitions() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for kind in StateTransitionKind:
        if kind is StateTransitionKind.QUESTION_RESOLVED:
            rows[kind.value] = {
                "restricted": True,
                "actors_by_question_owner": {
                    owner.value: sorted(item.value for item in allowed)
                    for owner, allowed in QUESTION_RESOLUTION_AUTHORITY.items()
                },
            }
            continue
        allowed = TRANSITION_AUTHORITY.get(kind)
        rows[kind.value] = {
            "restricted": allowed is not None,
            "actors": sorted(item.value for item in (allowed or StateAuthority)),
        }
    return rows


def _credential_policies() -> dict[str, dict[str, dict[str, str]]]:
    policies: dict[str, dict[str, dict[str, str]]] = {}
    for name, policy in _CREDENTIAL_POLICIES.items():
        by_credential: dict[str, dict[str, str]] = {}
        for authority in McpAuthority:
            principal = McpPrincipal(authority, f"{authority.value}-probe")
            outcomes: dict[str, str] = {}
            for operation in McpOperation:
                try:
                    policy.check(principal, operation, _PROBE_TOOL)
                except McpAuthorizationError as error:
                    outcomes[operation.value] = type(error).__name__
                else:
                    outcomes[operation.value] = "allowed"
            by_credential[authority.value] = outcomes
        policies[name] = by_credential
    return policies
