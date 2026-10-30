# Copyright (c) 2026 David Michael Indraputra

"""The capability, path and approval check every governed tool call passes before it runs.

``CapabilityToolAuthorizer`` is the one implementation of that check. ``BaseAgent`` consults it
at the shared dispatch boundary, ahead of whichever ``ToolExecutor`` the host supplied, and
``HarnessToolExecutor`` consults the same object, so the decision does not depend on which
executor carries the call out.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any, Protocol

from ..foundations.contracts import AgentFailure, ToolDefinition, ToolExecutionResult
from ..foundations.hashing import canonical_hash
from .approvals import (
    ApprovalEvent,
    ApprovalEventKind,
    ApprovalRegistry,
    ApprovalRequest,
    ApprovalStatus,
    event_of,
)
from .policy import CapabilityPolicy, PolicyDecision, SideEffectClass
from .tools import ToolAuthorization, ToolInvocationContext

UNREGISTERED_CAPABILITY = "unregistered"
_WRITE_TOOLS = frozenset({"write_draft", "edit_draft", "notebook_edit"})


class RegisteredToolLike(Protocol):
    @property
    def capability(self) -> str: ...

    @property
    def side_effect(self) -> SideEffectClass: ...


class AuthorizationScope(Protocol):
    """The run-level facts an authorization depends on; read live, never copied."""

    @property
    def run_id(self) -> str: ...

    @property
    def node_id(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def run_root(self) -> Path: ...

    @property
    def policy(self) -> CapabilityPolicy: ...

    @property
    def approvals(self) -> ApprovalRegistry: ...

    @property
    def approval_ids_by_capability(self) -> Mapping[str, str]: ...

    @property
    def bind_approvals_to_arguments(self) -> bool: ...

    @property
    def single_use_capabilities(self) -> Collection[str]: ...


def requested_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name in _WRITE_TOOLS:
        path = arguments.get("path")
        return [path] if isinstance(path, str) else []
    return []


class CapabilityToolAuthorizer:
    """Authorize one call: closed registry, role grant, paths, then a typed approval.

    A state-changing call is allowed only after its approval is consumed inside the registry's
    transaction, so a single-use approval allows one call however many run at once, and an
    approval that was revoked, used up or granted for other arguments allows none. Every denial,
    and every consumption of a single-use approval, is recorded through the registry's sink;
    a consumption that cannot be recorded refuses the call.
    """

    def __init__(
        self,
        resolve: Callable[[str], RegisteredToolLike | None],
        scope: AuthorizationScope,
    ) -> None:
        self._resolve = resolve
        self._scope = scope

    def authorize(self, tool: ToolDefinition, context: ToolInvocationContext) -> ToolAuthorization:
        scope = self._scope
        registered = self._resolve(tool.name)
        if registered is None:
            failure = AgentFailure(
                code="HARNESS_TOOL_UNKNOWN",
                message=f'No harness tool named "{tool.name}" exists.',
                details={"tool": tool.name},
            )
            self._deny_event(UNREGISTERED_CAPABILITY, tool, context, failure.message)
            return self._refusal(
                tool,
                context,
                None,
                ToolExecutionResult(status="failed", error=failure.message, failure=failure),
            )
        capability = registered.capability
        arguments = context.call.arguments
        single_use = capability in scope.single_use_capabilities
        digest = (
            canonical_hash({"tool": tool.name, "arguments": arguments})
            if scope.bind_approvals_to_arguments or single_use
            else None
        )
        approval = self._approval_for(capability, digest)
        foreign = self._foreign(approval)
        if foreign is not None:
            return self._denied(tool, context, capability, foreign, approval)
        decision = scope.policy.evaluate(
            role=scope.role,
            capability=capability,
            side_effect=registered.side_effect,
            run_root=scope.run_root,
            requested_paths=requested_paths(tool.name, arguments),
            approval=approval,
            arguments_digest=digest,
        )
        if (
            decision.allowed
            and approval is not None
            and registered.side_effect is not SideEffectClass.READ_ONLY
        ):
            decision = self._consume(approval, tool, context, capability, decision)
        if decision.allowed:
            return ToolAuthorization(
                allowed=True,
                tool=tool.name,
                call_id=context.call.id,
                issuer=self,
                capability=capability,
                approval_id=None if approval is None else approval.approval_id,
            )
        if decision.approval_required:
            request = (
                approval
                if approval is not None and approval.status is ApprovalStatus.PENDING
                else scope.approvals.request(
                    scope.run_id,
                    scope.node_id,
                    capability,
                    decision.reason,
                    arguments_digest=digest,
                    single_use=single_use,
                )
            )
            return self._refusal(
                tool,
                context,
                capability,
                ToolExecutionResult(
                    status="blocked",
                    output={"approval_id": request.approval_id, "capability": capability},
                    error=decision.reason,
                    failure=AgentFailure(
                        code="TOOL_APPROVAL_REQUIRED",
                        message=decision.reason,
                        details={
                            "tool": tool.name,
                            "capability": capability,
                            "approval_id": request.approval_id,
                        },
                    ),
                ),
                request.approval_id,
            )
        return self._denied(tool, context, capability, decision.reason, approval)

    def _approval_for(self, capability: str, digest: str | None) -> ApprovalRequest | None:
        scope = self._scope
        approval_id = scope.approval_ids_by_capability.get(capability)
        if approval_id:
            mapped = scope.approvals.get(approval_id)
            if mapped is not None and mapped.status is ApprovalStatus.CONSUMED:
                found = scope.approvals.find(scope.run_id, scope.node_id, capability, digest)
                return found if found is not None else mapped
            return mapped
        return scope.approvals.find(scope.run_id, scope.node_id, capability, digest)

    def _foreign(self, approval: ApprovalRequest | None) -> str | None:
        scope = self._scope
        if approval is None:
            return None
        if approval.run_id == scope.run_id and approval.node_id == scope.node_id:
            return None
        return (
            f'Approval "{approval.approval_id}" belongs to run "{approval.run_id}" node '
            f'"{approval.node_id}", not to run "{scope.run_id}" node "{scope.node_id}"; an '
            "approval cannot be used by another node."
        )

    def _consume(
        self,
        approval: ApprovalRequest,
        tool: ToolDefinition,
        context: ToolInvocationContext,
        capability: str,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        approvals = self._scope.approvals
        if not approvals.consume(approval.approval_id):
            return PolicyDecision(
                allowed=False,
                reason=(
                    f'Approval "{approval.approval_id}" for capability "{capability}" was used '
                    "up or withdrawn before this call ran; a new approval is required."
                ),
                approval_required=True,
            )
        if approval.single_use:
            event = event_of(
                approval,
                ApprovalEventKind.CONSUMED,
                f'Used by call "{context.call.id}" of tool "{tool.name}".',
                tool=tool.name,
                tool_call_id=context.call.id,
            )
            try:
                approvals.record_event(event, strict=True)
            except Exception as error:
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f'The consumption of single-use approval "{approval.approval_id}" could '
                        f"not be recorded ({type(error).__name__}: {error}), so the call was "
                        "refused and the approval stays used."
                    ),
                )
        return decision

    def _denied(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
        capability: str,
        reason: str,
        approval: ApprovalRequest | None,
    ) -> ToolAuthorization:
        self._deny_event(capability, tool, context, reason, approval)
        return self._refusal(
            tool,
            context,
            capability,
            ToolExecutionResult(
                status="blocked",
                error=reason,
                failure=AgentFailure(
                    code="TOOL_DENIED_BY_POLICY",
                    message=reason,
                    details={"tool": tool.name, "capability": capability},
                ),
            ),
            None if approval is None else approval.approval_id,
        )

    def _refusal(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
        capability: str | None,
        result: ToolExecutionResult,
        approval_id: str | None = None,
    ) -> ToolAuthorization:
        return ToolAuthorization(
            allowed=False,
            tool=tool.name,
            call_id=context.call.id,
            issuer=self,
            capability=capability,
            approval_id=approval_id,
            result=result,
        )

    def _deny_event(
        self,
        capability: str,
        tool: ToolDefinition,
        context: ToolInvocationContext,
        reason: str,
        approval: ApprovalRequest | None = None,
    ) -> None:
        scope = self._scope
        scope.approvals.record_event(
            ApprovalEvent(
                kind=ApprovalEventKind.DENIED,
                run_id=scope.run_id,
                node_id=scope.node_id,
                capability=capability,
                approval_id=None if approval is None else approval.approval_id,
                tool=tool.name,
                tool_call_id=context.call.id,
                reason=reason,
                arguments_digest=None if approval is None else approval.arguments_digest,
                single_use=False if approval is None else approval.single_use,
            )
        )
