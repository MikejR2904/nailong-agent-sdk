# Copyright (c) 2026 David Michael Indraputra

"""Typed approval gates for state-changing harness actions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from ..foundations.contracts import StrictModel


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
    """Own approval state as typed data rather than conversation text."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._next_id = 1

    def request(self, run_id: str, node_id: str, capability: str, reason: str) -> ApprovalRequest:
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
        return self._requests.get(approval_id)

    def list(self, run_id: str | None = None) -> list[ApprovalRequest]:
        values = self._requests.values()
        if run_id is not None:
            values = (request for request in values if request.run_id == run_id)
        return sorted(values, key=lambda request: request.approval_id)
