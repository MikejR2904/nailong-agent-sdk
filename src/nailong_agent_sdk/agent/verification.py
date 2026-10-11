# Copyright (c) 2026 David Michael Indraputra

"""Customizable, bounded verification gates for BaseAgent output acceptance.

A model may name only a gate declared in its static ``AgentDefinition``. SDK consumers
register the gate object or callback in a local ``VerificationGateRegistry``; callbacks
are never supplied by model output or by an MCP request.
"""

from __future__ import annotations

import inspect
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


class VerificationGateRegistry:
    """Registry of host-owned named output acceptance gates.

    The agent definition selects a name. Only the application embedding the SDK can
    register its implementation. This preserves the harness-owned verification boundary.
    """

    def __init__(self) -> None:
        self._gates: dict[str, VerificationGate] = {"status-is-complete": StatusIsCompleteGate()}

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

        if gate_id == "status-is-complete":
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
        return normalize_verification_return(result)


def normalize_verification_return(result: VerificationReturn) -> VerificationDecision:
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
    raise TypeError(
        "Verification gate must return VerificationDecision, bool, or (bool, reason) where "
        f"reason is a string or None; got {type(result).__name__}."
    )


def _validate_gate_id(gate_id: str) -> None:
    if not gate_id or not gate_id.strip():
        raise ValueError("Verification gate identifier must be non-empty.")
    if any(character.isspace() for character in gate_id):
        raise ValueError("Verification gate identifier must not contain whitespace.")
