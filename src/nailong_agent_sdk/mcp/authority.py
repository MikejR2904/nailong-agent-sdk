# Copyright (c) 2026 David Michael Indraputra

"""Credential classes, operation classes and decision binding for the MCP service."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..agent.retention import DECISION_STREAM_ID
from ..foundations.hashing import canonical_hash, sha256_hex
from ..observability.telemetry_models import (
    DecisionSource,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetryEvent,
    TelemetrySeverity,
)
from ..observability.telemetry_store import TelemetryStore
from ..tools.approvals import ApprovalRequest

DEFAULT_APPROVAL_MAX_AGE_SECONDS = 300.0
MAX_APPROVAL_MAX_AGE_SECONDS = 86_400.0
DEFAULT_CLOCK_SKEW_SECONDS = 30.0
HOST_VARIABLE = "AGENT_RUNTIME_AUTH_TOKEN"
APPROVER_VARIABLE = "AGENT_RUNTIME_APPROVER_TOKEN"
SINGLE_CREDENTIAL_VARIABLE = "AGENT_RUNTIME_ALLOW_HOST_APPROVALS"
MAX_RECORDED_ERROR_CHARS = 500


class McpAuthority(StrEnum):
    HOST = "host"
    APPROVER = "approver"
    LOCAL = "local"


class McpOperation(StrEnum):
    READ = "read"
    HOST = "host"
    APPROVAL = "approval"


@dataclass(frozen=True)
class McpPrincipal:
    authority: McpAuthority
    principal_id: str

    @classmethod
    def for_secret(cls, authority: McpAuthority, secret: str) -> McpPrincipal:
        return cls(authority, f"{authority.value}-{sha256_hex(secret)[:12]}")


LOCAL_PRINCIPAL = McpPrincipal(McpAuthority.LOCAL, "local-process")

_CURRENT: ContextVar[McpPrincipal | None] = ContextVar("nailong_mcp_principal", default=None)


def current_principal() -> McpPrincipal | None:
    return _CURRENT.get()


@contextmanager
def authenticated_as(principal: McpPrincipal | None) -> Iterator[None]:
    token = _CURRENT.set(principal)
    try:
        yield
    finally:
        _CURRENT.reset(token)


class McpAuthorizationError(Exception):
    pass


class Unauthenticated(McpAuthorizationError):
    pass


class OperationNotPermitted(McpAuthorizationError):
    pass


class ApprovalsDisabled(McpAuthorizationError):
    pass


class ApprovalBindingRequired(McpAuthorizationError):
    pass


class ApprovalStale(McpAuthorizationError):
    pass


class ApprovalExpired(McpAuthorizationError):
    pass


@dataclass(frozen=True)
class McpAuthorityPolicy:
    approver_configured: bool = False
    allow_host_approvals: bool = False
    require_principal: bool = False
    approval_max_age_seconds: float = DEFAULT_APPROVAL_MAX_AGE_SECONDS
    clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        if not 0 < self.approval_max_age_seconds <= MAX_APPROVAL_MAX_AGE_SECONDS:
            raise ValueError(
                "approval_max_age_seconds must be above 0 and at most "
                f"{MAX_APPROVAL_MAX_AGE_SECONDS:g}, got {self.approval_max_age_seconds!r}."
            )
        if self.clock_skew_seconds < 0:
            raise ValueError(
                f"clock_skew_seconds must not be negative, got {self.clock_skew_seconds!r}."
            )
        if self.approver_configured and self.allow_host_approvals:
            raise ValueError(
                "approver_configured and allow_host_approvals are mutually exclusive: with a "
                "separate approver credential the host credential never decides approvals."
            )

    @property
    def single_credential(self) -> bool:
        return self.allow_host_approvals and not self.approver_configured

    def resolve(self, presented: McpPrincipal | None, tool: str) -> McpPrincipal:
        if presented is not None:
            return presented
        if self.require_principal:
            raise Unauthenticated(
                f'Tool "{tool}" was called without an authenticated principal; the HTTP service '
                "resolves one from the bearer token before any tool runs, so this call did not "
                "come through it."
            )
        return LOCAL_PRINCIPAL

    def check(self, principal: McpPrincipal, operation: McpOperation, tool: str) -> None:
        if principal.authority is McpAuthority.LOCAL or operation is McpOperation.READ:
            return
        if principal.authority is McpAuthority.APPROVER:
            if operation is McpOperation.APPROVAL:
                return
            raise OperationNotPermitted(
                f'Tool "{tool}" starts or steers work and needs the host credential '
                f"({HOST_VARIABLE}); the approver credential ({APPROVER_VARIABLE}) is limited to "
                "reading state and recording approval decisions."
            )
        if operation is McpOperation.HOST:
            return
        if self.single_credential:
            return
        if self.approver_configured:
            raise OperationNotPermitted(
                f'Tool "{tool}" records an approval decision and needs the approver credential '
                f"({APPROVER_VARIABLE}); the host credential ({HOST_VARIABLE}) cannot decide "
                "approvals."
            )
        raise ApprovalsDisabled(
            f'Tool "{tool}" records an approval decision and is disabled: the service has no '
            f"approver credential. Start it with {APPROVER_VARIABLE} set to a secret that "
            f"differs from {HOST_VARIABLE}, or with {SINGLE_CREDENTIAL_VARIABLE}=1 to let the "
            "host credential decide approvals, which removes the separation between the two."
        )


def approval_request_digest(request: ApprovalRequest) -> str:
    return canonical_hash(
        {
            "approval_id": request.approval_id,
            "run_id": request.run_id,
            "node_id": request.node_id,
            "capability": request.capability,
            "reason": request.reason,
        }
    )


def value_digest(value: Any) -> str:
    return canonical_hash(value)


def plan_digest(plan: Any) -> str | None:
    return None if plan is None else value_digest(plan.model_dump(mode="json"))


def orchestration_digest(record: Any) -> str:
    return value_digest(
        {
            "orchestration_id": record.orchestration_id,
            "controller_id": record.controller_id,
            "architecture": record.architecture.value,
            "execution_plan": record.execution_plan.model_dump(mode="json"),
            "assignments": [item.model_dump(mode="json") for item in record.assignments],
        }
    )


def short(digest: str | None) -> str:
    return "none" if digest is None else digest[:12]


def _parse_issued_at(value: str, tool: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ApprovalExpired(
            f'Tool "{tool}" received issued_at_utc "{value}", which is not an ISO-8601 timestamp.'
        ) from error
    if parsed.tzinfo is None:
        raise ApprovalExpired(
            f'Tool "{tool}" received issued_at_utc "{value}" without a UTC offset; send the '
            'instant the approver decided, for example "2026-01-01T12:00:00Z".'
        )
    return parsed.astimezone(UTC)


@dataclass
class DecisionReceipt:
    principal_id: str
    authority: str
    stream: str = DECISION_STREAM_ID
    sequence: int | None = None
    warning: str | None = None

    def source(self) -> DecisionSource:
        return DecisionSource(authority=TelemetryAuthority.HUMAN, identifier=self.principal_id)

    def fields(self) -> dict[str, Any]:
        recorded: dict[str, Any] = {
            "principal": self.principal_id,
            "authority": self.authority,
            "stream": self.stream,
            "sequence": self.sequence,
        }
        if self.warning is not None:
            recorded["warning"] = self.warning
        return {"decision_record": recorded}


@dataclass
class DecisionGate:
    policy: McpAuthorityPolicy
    telemetry: TelemetryStore
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def resolve(self, tool: str) -> McpPrincipal:
        return self.policy.resolve(current_principal(), tool)

    def refuse_operation(
        self, tool: str, operation: McpOperation, principal: McpPrincipal, error: Exception
    ) -> None:
        self._emit_refusal(
            "mcp.authority.refused",
            tool,
            principal,
            {"operation": operation.value},
            error,
        )

    @contextmanager
    def decide(
        self,
        tool: str,
        *,
        subject: str,
        approved: bool | None,
        binding_field: str,
        expected: str | None,
        actual: Callable[[], str | None],
        issued_at_utc: str | None,
        label: str | None,
        reason: str | None = None,
    ) -> Iterator[DecisionReceipt]:
        principal = self.policy.resolve(current_principal(), tool)
        self.policy.check(principal, McpOperation.APPROVAL, tool)
        actual_digest = actual()
        recorded = {
            "subject": subject,
            "approved": approved,
            "binding_field": binding_field,
            "binding_expected": expected,
            "binding_actual": actual_digest,
            "issued_at_utc": issued_at_utc,
            "approver_label": label,
            "reason": reason,
            "host_may_approve": self.policy.single_credential
            and principal.authority is McpAuthority.HOST,
        }
        try:
            self._verify(
                principal, tool, subject, binding_field, expected, actual_digest, issued_at_utc
            )
        except McpAuthorizationError as error:
            self._emit_refusal("mcp.decision.refused", tool, principal, recorded, error)
            raise
        receipt = DecisionReceipt(principal.principal_id, principal.authority.value)
        event = self._append(
            "mcp.decision.authorized", "authorized", tool, principal, recorded, None
        )
        receipt.sequence = event.sequence
        try:
            yield receipt
        except BaseException as error:
            failure = {
                **recorded,
                "error_type": type(error).__name__,
                "error": str(error)[:MAX_RECORDED_ERROR_CHARS],
            }
            self._append_after("mcp.decision.failed", "failed", tool, principal, failure, receipt)
            raise
        self._append_after("mcp.decision.applied", "applied", tool, principal, recorded, receipt)

    def _verify(
        self,
        principal: McpPrincipal,
        tool: str,
        subject: str,
        binding_field: str,
        expected: str | None,
        actual: str | None,
        issued_at_utc: str | None,
    ) -> None:
        mandatory = principal.authority is McpAuthority.APPROVER
        if expected is None:
            if mandatory:
                raise ApprovalBindingRequired(
                    f'Tool "{tool}" needs {binding_field}, the digest of {subject} the approver '
                    "reviewed; an approver decision that is not bound to it is refused."
                )
        elif actual is None or not hmac.compare_digest(expected.encode(), actual.encode()):
            raise ApprovalStale(
                f'Tool "{tool}" received {binding_field} {short(expected)}, but {subject} is '
                f"now {short(actual)}: it changed after the approver reviewed it, so the "
                "decision is not applied."
            )
        if issued_at_utc is None:
            if mandatory:
                raise ApprovalBindingRequired(
                    f'Tool "{tool}" needs issued_at_utc, the instant the approver decided; an '
                    "approver decision without it cannot be checked for expiry."
                )
            return
        issued = _parse_issued_at(issued_at_utc, tool)
        age = (self.clock() - issued).total_seconds()
        if age > self.policy.approval_max_age_seconds:
            raise ApprovalExpired(
                f'Tool "{tool}" received a decision issued {age:.0f}s ago; the service accepts '
                f"decisions at most {self.policy.approval_max_age_seconds:g}s old."
            )
        if age < -self.policy.clock_skew_seconds:
            raise ApprovalExpired(
                f'Tool "{tool}" received a decision issued {-age:.0f}s in the future; the '
                f"service tolerates {self.policy.clock_skew_seconds:g}s of clock skew."
            )

    def _emit_refusal(
        self,
        event_type: str,
        tool: str,
        principal: McpPrincipal,
        recorded: dict[str, Any],
        error: Exception,
    ) -> None:
        payload = {
            **recorded,
            "reason_type": type(error).__name__,
            "reason_detail": str(error)[:MAX_RECORDED_ERROR_CHARS],
        }
        try:
            self._append(
                event_type,
                "refused",
                tool,
                principal,
                payload,
                None,
                severity=TelemetrySeverity.WARNING,
                authority=TelemetryAuthority.SYSTEM,
            )
        except Exception as audit_error:
            error.add_note(
                f"The refusal could not be recorded in the {DECISION_STREAM_ID} stream: "
                f"{type(audit_error).__name__}: {audit_error}"
            )

    def _append_after(
        self,
        event_type: str,
        status: str,
        tool: str,
        principal: McpPrincipal,
        payload: dict[str, Any],
        receipt: DecisionReceipt,
    ) -> None:
        try:
            self._append(event_type, status, tool, principal, payload, receipt.sequence)
        except Exception as audit_error:
            receipt.warning = (
                f"The outcome could not be recorded in the {DECISION_STREAM_ID} stream: "
                f"{type(audit_error).__name__}: {audit_error}"
            )

    def _append(
        self,
        event_type: str,
        status: str,
        tool: str,
        principal: McpPrincipal,
        payload: dict[str, Any],
        authorized_sequence: int | None,
        *,
        severity: TelemetrySeverity = TelemetrySeverity.INFO,
        authority: TelemetryAuthority = TelemetryAuthority.HUMAN,
    ) -> TelemetryEvent:
        return self.telemetry.emit(
            event_type,
            TelemetryContext(run_id=DECISION_STREAM_ID),
            actor=TelemetryActor(
                kind="mcp-principal",
                identifier=principal.principal_id,
                role=principal.authority.value,
            ),
            authority=authority,
            status=status,
            severity=severity,
            payload={
                "tool": tool,
                "principal": principal.principal_id,
                "principal_authority": principal.authority.value,
                **payload,
                **(
                    {}
                    if authorized_sequence is None
                    else {"authorized_sequence": authorized_sequence}
                ),
            },
        )
