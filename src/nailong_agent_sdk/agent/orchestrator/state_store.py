# Copyright (c) 2026 David Michael Indraputra

"""Atomic local orchestration record and policy persistence."""

from __future__ import annotations

from pathlib import Path

from ...foundations.atomic_io import replace_atomic
from .models import OrchestrationPolicy, OrchestrationRecord


class OrchestrationStateStore:
    """Atomic local record store; provider credentials and callbacks are excluded."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-orchestrations"
        self._root.mkdir(parents=True, exist_ok=True)
        self._policies = self._root / "policies"
        self._policies.mkdir(parents=True, exist_ok=True)

    def save(self, record: OrchestrationRecord) -> None:
        target = self._root / f"{record.orchestration_id}.json"
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        replace_atomic(temporary, target)

    def load(self, orchestration_id: str) -> OrchestrationRecord:
        target = self._root / f"{orchestration_id}.json"
        if not target.is_file():
            raise ValueError(f'Orchestration "{orchestration_id}" is unknown.')
        return OrchestrationRecord.model_validate_json(target.read_text(encoding="utf-8"))

    def exists(self, orchestration_id: str) -> bool:
        return (self._root / f"{orchestration_id}.json").is_file()

    def save_policy(self, policy: OrchestrationPolicy) -> None:
        """Persist a non-secret policy and reject a same-ID semantic rewrite."""

        target = self._policies / f"{policy.policy_id}.json"
        serialized = policy.model_dump_json(indent=2)
        if target.is_file():
            existing = OrchestrationPolicy.model_validate_json(target.read_text(encoding="utf-8"))
            if existing != policy:
                raise ValueError(
                    f'Policy "{policy.policy_id}" already exists with different authority fields.'
                )
            return
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        replace_atomic(temporary, target)

    def load_policy(self, policy_id: str) -> OrchestrationPolicy:
        target = self._policies / f"{policy_id}.json"
        if not target.is_file():
            raise ValueError(f'Orchestration policy "{policy_id}" is unknown.')
        return OrchestrationPolicy.model_validate_json(target.read_text(encoding="utf-8"))
