# Copyright (c) 2026 David Michael Indraputra

"""Customizable, bounded verification gates for BaseAgent output acceptance.

A model may name only a gate declared in its static ``AgentDefinition``. SDK consumers
register the gate object or callback in a local ``VerificationGateRegistry``; callbacks
are never supplied by model output or by an MCP request.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..foundations.contracts import AgentDefinition, ScopedAgentTask
from ..foundations.errors import AgentSdkError
from ..observability.telemetry_models import TelemetryAuthority

MAX_EVIDENCE_CHARS = 4_096
MAX_VERIFIER_ID_CHARS = 128
MAX_ALL_OF_MEMBERS = 16
_MEMBER_REASON_CHARS = 200
_MEMBER_NAME_CHARS = 64
_MEMBER_EVIDENCE_CHARS = 100
_AUTHORITY_RANK = {
    TelemetryAuthority.DETERMINISTIC: 0,
    TelemetryAuthority.SYSTEM: 1,
    TelemetryAuthority.TOOL: 2,
    TelemetryAuthority.MODEL_MEDIATED: 3,
    TelemetryAuthority.HUMAN: 4,
}


@dataclass(frozen=True)
class VerificationDecision:
    """The only acceptance result a verification gate may return."""

    passed: bool
    reason: str | None = None
    verifier_id: str | None = None
    authority: TelemetryAuthority | None = None
    evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.verifier_id is not None:
            _check_verifier_id(self.verifier_id)
        if self.authority is not None and not isinstance(self.authority, TelemetryAuthority):
            try:
                object.__setattr__(self, "authority", TelemetryAuthority(self.authority))
            except ValueError as error:
                valid = ", ".join(item.value for item in TelemetryAuthority)
                raise ValueError(f'authority "{self.authority}" is not one of: {valid}.') from error
        if self.evidence is not None:
            if not isinstance(self.evidence, Mapping) or not all(
                isinstance(key, str) for key in self.evidence
            ):
                raise ValueError("evidence must be a mapping with string keys.")
            try:
                encoded = json.dumps(
                    dict(self.evidence), sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"evidence must be JSON-serializable: {type(error).__name__}: {error}"
                ) from error
            if len(encoded) > MAX_EVIDENCE_CHARS:
                raise ValueError(
                    f"evidence is {len(encoded)} characters of JSON; the limit is "
                    f"{MAX_EVIDENCE_CHARS}."
                )
            object.__setattr__(self, "evidence", json.loads(encoded))


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
    ``VerificationDecision``, ``bool``, or ``(bool, reason)`` tuple. ``verifier_id`` and
    ``authority`` name who decides and what kind of authority that is; both are recorded
    with the decision.
    """

    callback: VerificationCallback
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None
    verifier_id: str | None = None
    authority: TelemetryAuthority | None = None

    async def verify(self, context: VerificationContext) -> VerificationReturn:
        result = self.callback(context, *self.args, **(self.kwargs or {}))
        if inspect.isawaitable(result):
            return await result
        return result


class StatusIsCompleteGate:
    """Minimal deterministic gate for the initial MCP route and tests."""

    verifier_id = "status-is-complete"
    authority = TelemetryAuthority.DETERMINISTIC

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


class AllOfVerificationGate:
    """Accept only when every member gate accepts, recording each member's outcome."""

    def __init__(
        self,
        gates: Sequence[VerificationGate],
        *,
        verifier_id: str | None = None,
        stop_at_first_failure: bool = True,
    ) -> None:
        members = list(gates)
        if not members:
            raise ValueError("AllOfVerificationGate needs at least one member gate.")
        if len(members) > MAX_ALL_OF_MEMBERS:
            raise ValueError(
                f"AllOfVerificationGate accepts at most {MAX_ALL_OF_MEMBERS} member gates, "
                f"got {len(members)}."
            )
        if verifier_id is not None:
            _check_verifier_id(verifier_id)
        self._members = members
        self.verifier_id = verifier_id or "all-of"
        self.authority: TelemetryAuthority | None = None
        self.stop_at_first_failure = stop_at_first_failure

    async def verify(self, context: VerificationContext) -> VerificationDecision:
        outcomes: list[tuple[str, VerificationDecision, TelemetryAuthority | None]] = []
        for index, gate in enumerate(self._members):
            declared_id = _declared(gate, "verifier_id")
            try:
                raw = gate.verify(context)
                if inspect.isawaitable(raw):
                    raw = await raw
                decision = normalize_verification_return(raw)
            except Exception as error:
                raise AgentSdkError(
                    "VERIFICATION_GATE_MEMBER_FAILED",
                    f'Member {index} ("{declared_id or f"member-{index}"}") of verification gate '
                    f'"{self.verifier_id}" raised {type(error).__name__}: {error}',
                    {"member_index": index, "verification_gate_id": self.verifier_id},
                ) from error
            name = str(decision.verifier_id or declared_id or f"member-{index}")
            authority = self._member_authority(
                index, name, decision.authority or _declared(gate, "authority")
            )
            outcomes.append((name, decision, authority))
            if not decision.passed and self.stop_at_first_failure:
                break
        failures = [(name, item) for name, item, _authority in outcomes if not item.passed]
        declared_authorities = [
            authority for _name, _item, authority in outcomes if authority is not None
        ]
        reason = None
        if failures:
            reason = "; ".join(
                f"{name}: {item.reason or 'rejected'}"[:_MEMBER_REASON_CHARS]
                for name, item in failures
            )
        return VerificationDecision(
            not failures,
            reason,
            verifier_id=self.verifier_id,
            authority=(
                max(declared_authorities, key=_AUTHORITY_RANK.__getitem__)
                if declared_authorities
                else None
            ),
            evidence={
                "members": [
                    {
                        "verifier_id": clip_evidence_text(name, _MEMBER_NAME_CHARS),
                        "passed": item.passed,
                        "reason": clip_evidence_text(item.reason or "", _MEMBER_EVIDENCE_CHARS)
                        or None,
                        "authority": authority.value if authority else None,
                    }
                    for name, item, authority in outcomes
                ],
                "evaluated": len(outcomes),
                "total": len(self._members),
            },
        )

    def _member_authority(self, index: int, name: str, value: Any) -> TelemetryAuthority | None:
        if value is None or isinstance(value, TelemetryAuthority):
            return value
        try:
            return TelemetryAuthority(value)
        except ValueError as error:
            raise AgentSdkError(
                "VERIFICATION_GATE_METADATA_INVALID",
                f'Member {index} ("{name}") of verification gate "{self.verifier_id}" declares '
                f"authority {value!r}, which is not a TelemetryAuthority.",
                {"member_index": index, "verification_gate_id": self.verifier_id},
            ) from error


class _NamedGate:
    def __init__(self, gate: VerificationGate, name: str) -> None:
        self._gate = gate
        self.verifier_id = _declared(gate, "verifier_id") or name
        self.authority = _declared(gate, "authority")

    def verify(
        self, context: VerificationContext
    ) -> VerificationReturn | Awaitable[VerificationReturn]:
        return self._gate.verify(context)


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

    def register_all_of(
        self,
        gate_id: str,
        member_ids: Sequence[str],
        *,
        replace: bool = False,
        stop_at_first_failure: bool = True,
    ) -> None:
        _validate_gate_id(gate_id)
        members = [_NamedGate(self._gate(member_id), member_id) for member_id in member_ids]
        self.register(
            gate_id,
            AllOfVerificationGate(
                members, verifier_id=gate_id, stop_at_first_failure=stop_at_first_failure
            ),
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
        return self._gate(gate_id)

    def _gate(self, gate_id: str) -> VerificationGate:
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
        return _attributed(normalize_verification_return(result), gate, gate_id)


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


def _check_verifier_id(value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_VERIFIER_ID_CHARS:
        raise ValueError(
            f"verifier_id must be 1 to {MAX_VERIFIER_ID_CHARS} non-blank characters, got {value!r}."
        )


def clip_evidence_text(text: str, limit: int) -> str:
    """Cut ``text`` so its JSON-escaped form is at most ``limit`` characters."""

    used = 0
    for index, character in enumerate(text):
        used += len(json.dumps(character)) - 2
        if used > limit:
            return text[:index]
    return text


def _declared(gate: Any, name: str) -> Any:
    return getattr(gate, name, None)


def _attributed(
    decision: VerificationDecision, gate: VerificationGate, gate_id: str | None
) -> VerificationDecision:
    verifier_id = decision.verifier_id or _declared(gate, "verifier_id")
    authority = decision.authority or _declared(gate, "authority")
    if verifier_id == decision.verifier_id and authority == decision.authority:
        return decision
    try:
        return dataclasses.replace(decision, verifier_id=verifier_id, authority=authority)
    except ValueError as error:
        raise AgentSdkError(
            "VERIFICATION_GATE_METADATA_INVALID",
            f'Verification gate "{gate_id}" declares invalid metadata: {error}',
            {"verification_gate_id": gate_id},
        ) from error


def _validate_gate_id(gate_id: str) -> None:
    if not gate_id or not gate_id.strip():
        raise ValueError("Verification gate identifier must be non-empty.")
    if any(character.isspace() for character in gate_id):
        raise ValueError("Verification gate identifier must not contain whitespace.")
