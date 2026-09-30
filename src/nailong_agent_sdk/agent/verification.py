# Copyright (c) 2026 David Michael Indraputra

"""Customizable, bounded verification gates for BaseAgent output acceptance.

A model may name only a gate declared in its static ``AgentDefinition``. SDK consumers
register the gate object or callback in a local ``VerificationGateRegistry``; callbacks
are never supplied by model output or by an MCP request.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..foundations.contracts import AgentDefinition, ScopedAgentTask
from ..foundations.errors import AgentSdkError


@dataclass(frozen=True)
class VerificationDecision:
    """The only acceptance result a verification gate may return."""

    passed: bool
    reason: str | None = None


@dataclass(frozen=True)
class VerificationContext:
    """Read-only input presented to a local, registered verification gate."""

    output: Any
    definition: AgentDefinition
    task: ScopedAgentTask


type VerificationReturn = VerificationDecision | bool | tuple[bool, str | None]
type VerificationCallback = Callable[
    [VerificationContext, Any], VerificationReturn | Awaitable[VerificationReturn]
]


class VerificationGate(Protocol):
    """Protocol implemented by deterministic or consumer-provided acceptance gates."""

    def verify(
        self, context: VerificationContext
    ) -> VerificationReturn | Awaitable[VerificationReturn]: ...


@dataclass(frozen=True)
class CallableVerificationGate:
    """Adapt a consumer callback plus fixed local configuration into a verification gate.

    The callback receives ``VerificationContext`` first, followed by the configured
    positional and keyword arguments. It can be sync or async and may return a
    ``VerificationDecision``, ``bool``, or ``(bool, reason)`` tuple.
    """

    callback: VerificationCallback
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None

    async def verify(self, context: VerificationContext) -> VerificationReturn:
        result = self.callback(context, *self.args, **(self.kwargs or {}))
        if inspect.isawaitable(result):
            return await result
        return result


class StatusIsCompleteGate:
    """Minimal deterministic gate for the initial MCP route and tests."""

    def verify(self, context: VerificationContext) -> VerificationDecision:
        if not isinstance(context.output, dict):
            return VerificationDecision(False, "Candidate output is not an object.")
        status = context.output.get(context.definition.termination_policy.status_field)
        if status != "complete":
            return VerificationDecision(
                False,
                f'Candidate output field "{context.definition.termination_policy.status_field}" '
                'is not "complete".',
            )
        return VerificationDecision(True)


class ValidateRtlTaskResultGate:
    """Validate a declared RTLWorker result against the immutable task interface.

    The framework requires the RTL stage to preserve the copied interface and return a
    structured local-check result; it does not claim whole-design functional correctness
    at this gate (updated framework design, pp. 65-66).
    """

    def verify(self, context: VerificationContext) -> VerificationDecision:
        output = context.output
        if not isinstance(output, dict):
            return VerificationDecision(False, "RTL task output is not an object.")
        if output.get("status") != "complete":
            return VerificationDecision(False, 'RTL task output status is not "complete".')
        draft_artifact_id = output.get("draft_artifact_id")
        if not isinstance(draft_artifact_id, str) or not draft_artifact_id.startswith("sha256:"):
            return VerificationDecision(
                False, "RTL task output lacks a content-addressed draft artifact."
            )
        if not isinstance(output.get("checks"), list):
            return VerificationDecision(False, "RTL task output lacks a checks list.")
        expected_hash = hashlib.sha256(
            json.dumps(context.task.locked_interface, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if output.get("locked_interface_hash") != expected_hash:
            return VerificationDecision(
                False,
                "RTL task result does not attest to the exact locked-interface snapshot.",
            )
        return VerificationDecision(True)


class VerificationGateRegistry:
    """Registry of host-owned named output acceptance gates.

    The agent definition selects a name. Only the application embedding the SDK can
    register its implementation. This preserves the harness-owned verification boundary.
    """

    def __init__(self) -> None:
        self._gates: dict[str, VerificationGate] = {
            "status-is-complete": StatusIsCompleteGate(),
            "validate-rtl-task-result": ValidateRtlTaskResultGate(),
        }

    def register(self, gate_id: str, gate: VerificationGate, *, replace: bool = False) -> None:
        """Register a local gate object under a stable definition-facing identifier."""

        _validate_gate_id(gate_id)
        if gate_id in self._gates and not replace:
            raise ValueError(f'Verification gate "{gate_id}" is already registered.')
        self._gates[gate_id] = gate

    def register_callable(
        self,
        gate_id: str,
        callback: VerificationCallback,
        *args: Any,
        replace: bool = False,
        **kwargs: Any,
    ) -> None:
        """Register a sync or async consumer callback with fixed local arguments.

        Callback configuration is local process state. It is deliberately not serialized
        into `AgentDefinition`, MCP payloads, telemetry, or model context.
        """

        self.register(
            gate_id,
            CallableVerificationGate(callback=callback, args=tuple(args), kwargs=dict(kwargs)),
            replace=replace,
        )

    def unregister(self, gate_id: str) -> None:
        """Remove a non-built-in gate when an embedding application is reconfigured."""

        if gate_id in {"status-is-complete", "validate-rtl-task-result"}:
            raise ValueError("Built-in verification gates cannot be unregistered.")
        if gate_id not in self._gates:
            raise AgentSdkError(
                "VERIFICATION_GATE_UNKNOWN", f'Unknown verification gate "{gate_id}".'
            )
        del self._gates[gate_id]

    def resolve(self, gate_id: str | None) -> VerificationGate | None:
        if gate_id is None:
            return None
        gate = self._gates.get(gate_id)
        if gate is None:
            raise AgentSdkError(
                "VERIFICATION_GATE_UNKNOWN", f'Unknown verification gate "{gate_id}".'
            )
        return gate

    async def evaluate(
        self,
        gate_id: str | None,
        output: Any,
        definition: AgentDefinition,
        task: ScopedAgentTask,
    ) -> VerificationDecision | None:
        """Evaluate a resolved local gate and normalize its safe return contract."""

        gate = self.resolve(gate_id)
        if gate is None:
            return None
        result = gate.verify(VerificationContext(output=output, definition=definition, task=task))
        if inspect.isawaitable(result):
            result = await result
        return _normalize_decision(result)


def _normalize_decision(result: VerificationReturn) -> VerificationDecision:
    if isinstance(result, VerificationDecision):
        return result
    if isinstance(result, bool):
        return VerificationDecision(result)
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], bool)
        and (result[1] is None or isinstance(result[1], str))
    ):
        return VerificationDecision(result[0], result[1])
    raise TypeError("Verification gate must return VerificationDecision, bool, or (bool, reason).")


def _validate_gate_id(gate_id: str) -> None:
    if not gate_id or not gate_id.strip():
        raise ValueError("Verification gate identifier must be non-empty.")
    if any(character.isspace() for character in gate_id):
        raise ValueError("Verification gate identifier must not contain whitespace.")
