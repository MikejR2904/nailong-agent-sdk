# Copyright (c) 2026 David Michael Indraputra

"""Typed approval gates for state-changing harness actions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from ..foundations.atomic_io import replace_atomic
from ..foundations.contracts import StrictModel
from ..foundations.file_lock import FileLock


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(StrictModel):
    approval_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_reason: str | None = None


class ApprovalRegistry:
    """Own approval state as typed data rather than conversation text.

    With ``path`` the registry is durable: every change reloads the file and
    writes it back atomically under a cross-process lock, so a decision recorded
    by one process (an operator's MCP client) is seen by another (the runtime
    executing the gated tool), and survives restarts. Without ``path`` it lives
    in memory for the lifetime of one process.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = FileLock(path) if path is not None else None
        self._requests: dict[str, ApprovalRequest] = {}
        self._next_id = 1
        self._reload()

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        if self._lock is None:
            yield
            return
        with self._lock.hold():
            self._reload()
            yield
            self._persist()

    def _reload(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._next_id = int(payload["next_id"])
        self._requests = {
            item["approval_id"]: ApprovalRequest.model_validate(item)
            for item in payload["requests"]
        }

    def _persist(self) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "next_id": self._next_id,
            "requests": [item.model_dump(mode="json") for item in self._requests.values()],
        }
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        replace_atomic(temporary, self._path)

    def request(self, run_id: str, node_id: str, capability: str, reason: str) -> ApprovalRequest:
        with self._mutation():
            request = ApprovalRequest(
                approval_id=f"approval-{self._next_id}",
                run_id=run_id,
                node_id=node_id,
                capability=capability,
                reason=reason,
            )
            self._next_id += 1
            self._requests[request.approval_id] = request
        return request

    def submit(
        self, approval_id: str, approved: bool, reason: str | None = None
    ) -> ApprovalRequest:
        with self._mutation():
            request = self._requests.get(approval_id)
            if request is None:
                raise ValueError(f'Approval request "{approval_id}" is unknown.')
            if request.status is not ApprovalStatus.PENDING:
                raise ValueError(f'Approval request "{approval_id}" already has a decision.')
            updated = request.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
                    "decision_reason": reason,
                }
            )
            self._requests[approval_id] = updated
        return updated

    def get(self, approval_id: str) -> ApprovalRequest | None:
        self._reload()
        return self._requests.get(approval_id)

    def list(self, run_id: str | None = None) -> list[ApprovalRequest]:
        self._reload()
        values = self._requests.values()
        if run_id is not None:
            values = (request for request in values if request.run_id == run_id)
        return sorted(values, key=lambda request: int(request.approval_id.split("-")[-1]))
