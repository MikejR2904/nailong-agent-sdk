# Copyright (c) 2026 David Michael Indraputra

"""Atomic local orchestration record and policy persistence."""

from __future__ import annotations

from pathlib import Path

from ...foundations.atomic_io import (
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ...foundations.identifiers import (
    file_safe_name,
    is_valid_identifier,
    reserve_sequential_identifier,
    validate_identifier,
)
from .models import OrchestrationPolicy, OrchestrationRecord


class OrchestrationStateStore:
    """Atomic local record store; provider credentials and callbacks are excluded."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-orchestrations"
        self._root.mkdir(parents=True, exist_ok=True)
        self._claims = self._root / ".claims"
        self._policies = self._root / "policies"
        self._policies.mkdir(parents=True, exist_ok=True)

    def reserve_orchestration_id(self, *, start: int = 1) -> tuple[str, int]:
        return reserve_sequential_identifier(
            self._claims, "orchestration", self.exists, start=start
        )

    def save(self, record: OrchestrationRecord) -> None:
        target = self._record_path(record.orchestration_id)
        temporary = unique_temporary_path(target)
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        replace_atomic(temporary, target)

    def load(self, orchestration_id: str) -> OrchestrationRecord:
        target = self._record_path(orchestration_id)
        if not target.is_file():
            raise ValueError(f'Orchestration "{orchestration_id}" is unknown.')
        return OrchestrationRecord.model_validate_json(read_text_retrying(target))

    def exists(self, orchestration_id: str) -> bool:
        if not is_valid_identifier(orchestration_id):
            return False
        return self._record_path(orchestration_id).is_file()

    def save_policy(self, policy: OrchestrationPolicy) -> None:
        """Persist a non-secret policy and reject a same-ID semantic rewrite."""

        target = self._policy_path(policy.policy_id)
        serialized = policy.model_dump_json(indent=2)
        if target.is_file():
            existing = OrchestrationPolicy.model_validate_json(read_text_retrying(target))
            if existing != policy:
                raise ValueError(
                    f'Policy "{policy.policy_id}" already exists with different authority fields.'
                )
            return
        temporary = unique_temporary_path(target)
        temporary.write_text(serialized, encoding="utf-8")
        replace_atomic(temporary, target)

    def load_policy(self, policy_id: str) -> OrchestrationPolicy:
        target = self._policy_path(policy_id)
        if not target.is_file():
            raise ValueError(f'Orchestration policy "{policy_id}" is unknown.')
        return OrchestrationPolicy.model_validate_json(read_text_retrying(target))

    def _record_path(self, orchestration_id: str) -> Path:
        return self._root / f"{validate_identifier(orchestration_id, 'Orchestration id')}.json"

    def _policy_path(self, policy_id: str) -> Path:
        return self._policies / f"{file_safe_name(policy_id)}.json"
