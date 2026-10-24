# Copyright (c) 2026 David Michael Indraputra

"""Typed approval gates for state-changing harness actions."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from ..foundations.atomic_io import (
    exclusive_file_lock,
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ..foundations.contracts import StrictModel
from ..observability.telemetry_models import DecisionSource, TelemetryAuthority


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    CONSUMED = "consumed"


class ApprovalRequest(StrictModel):
    approval_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_reason: str | None = None
    decided_by: str | None = None
    decision_authority: TelemetryAuthority | None = None
    arguments_digest: str | None = None
    single_use: bool = False


class _PersistedApprovals(StrictModel):
    schema_version: str = "approvals-v1"
    next_id: int = Field(ge=1)
    requests: list[ApprovalRequest] = Field(default_factory=list)


class ApprovalRegistry:
    """Own approval state as typed data rather than conversation text."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._next_id = 1
        self._fingerprint: tuple[int, int] | None = None
        self._changed = False
        self._refresh()

    def request(
        self,
        run_id: str,
        node_id: str,
        capability: str,
        reason: str,
        *,
        arguments_digest: str | None = None,
        single_use: bool = False,
    ) -> ApprovalRequest:
        with self._transaction():
            for existing in self._matching(run_id, node_id, capability):
                if (
                    existing.status is ApprovalStatus.PENDING
                    and existing.arguments_digest == arguments_digest
                    and existing.single_use == single_use
                ):
                    return existing
            request = ApprovalRequest(
                approval_id=f"approval-{self._next_id}",
                run_id=run_id,
                node_id=node_id,
                capability=capability,
                reason=reason,
                arguments_digest=arguments_digest,
                single_use=single_use,
            )
            self._next_id += 1
            self._requests[request.approval_id] = request
            self._changed = True
            return request

    def submit(
        self,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
        *,
        source: DecisionSource | None = None,
    ) -> ApprovalRequest:
        with self._transaction():
            request = self._requests.get(approval_id)
            if request is None:
                raise ValueError(f'Approval request "{approval_id}" is unknown.')
            if request.status is not ApprovalStatus.PENDING:
                raise ValueError(
                    f'Approval request "{approval_id}" already has the decision '
                    f'"{request.status.value}".'
                )
            updated = request.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
                    "decision_reason": reason,
                    "decided_by": source.identifier if source is not None else None,
                    "decision_authority": source.authority if source is not None else None,
                }
            )
            self._requests[approval_id] = updated
            self._changed = True
            return updated

    def revoke(
        self,
        approval_id: str,
        reason: str,
        *,
        source: DecisionSource | None = None,
    ) -> ApprovalRequest:
        with self._transaction():
            request = self._requests.get(approval_id)
            if request is None:
                raise ValueError(f'Approval request "{approval_id}" is unknown.')
            if request.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                raise ValueError(
                    f'Approval request "{approval_id}" has the decision '
                    f'"{request.status.value}" and cannot be revoked.'
                )
            updated = request.model_copy(
                update={
                    "status": ApprovalStatus.REVOKED,
                    "decision_reason": reason,
                    "decided_by": source.identifier if source is not None else request.decided_by,
                    "decision_authority": (
                        source.authority if source is not None else request.decision_authority
                    ),
                }
            )
            self._requests[approval_id] = updated
            self._changed = True
            return updated

    def consume(self, approval_id: str) -> bool:
        """Use an approval once: a single-use approval turns ``CONSUMED``, others stay approved."""

        with self._transaction():
            request = self._requests.get(approval_id)
            if request is None or request.status is not ApprovalStatus.APPROVED:
                return False
            if request.single_use:
                self._requests[approval_id] = request.model_copy(
                    update={"status": ApprovalStatus.CONSUMED}
                )
                self._changed = True
            return True

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            self._refresh()
            return self._requests.get(approval_id)

    def find(
        self,
        run_id: str,
        node_id: str,
        capability: str,
        arguments_digest: str | None = None,
    ) -> ApprovalRequest | None:
        with self._lock:
            self._refresh()
            matching = [
                request
                for request in self._matching(run_id, node_id, capability)
                if request.arguments_digest in {None, arguments_digest}
            ]
            return matching[-1] if matching else None

    def list(self, run_id: str | None = None) -> list[ApprovalRequest]:
        with self._lock:
            self._refresh()
            values = self._requests.values()
            if run_id is not None:
                values = (request for request in values if request.run_id == run_id)
            return sorted(values, key=_approval_order)

    def _matching(self, run_id: str, node_id: str, capability: str) -> list[ApprovalRequest]:
        return sorted(
            (
                request
                for request in self._requests.values()
                if request.run_id == run_id
                and request.node_id == node_id
                and request.capability == capability
            ),
            key=_approval_order,
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            path = self._path
            self._changed = False
            if path is None:
                yield
                return
            with exclusive_file_lock(
                path.with_suffix(".lock"), timeout_code="APPROVAL_LOCK_TIMEOUT"
            ):
                self._load(path)
                yield
                if self._changed:
                    self._save(path)

    def _refresh(self) -> None:
        path = self._path
        if path is None:
            return
        try:
            status = path.stat()
        except FileNotFoundError:
            return
        if self._fingerprint != (status.st_mtime_ns, status.st_size):
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            status = path.stat()
        except FileNotFoundError:
            return
        persisted = _PersistedApprovals.model_validate_json(read_text_retrying(path))
        self._requests = {request.approval_id: request for request in persisted.requests}
        self._next_id = persisted.next_id
        self._fingerprint = (status.st_mtime_ns, status.st_size)

    def _save(self, path: Path) -> None:
        persisted = _PersistedApprovals(
            next_id=self._next_id, requests=sorted(self._requests.values(), key=_approval_order)
        )
        temporary = unique_temporary_path(path)
        temporary.write_text(persisted.model_dump_json(indent=2), encoding="utf-8")
        replace_atomic(temporary, path)
        status = path.stat()
        self._fingerprint = (status.st_mtime_ns, status.st_size)


def _approval_order(request: ApprovalRequest) -> tuple[int, str]:
    number = request.approval_id.rpartition("-")[2]
    return (int(number) if number.isdigit() else 0, request.approval_id)
