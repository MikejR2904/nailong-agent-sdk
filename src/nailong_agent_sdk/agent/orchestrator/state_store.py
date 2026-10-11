# Copyright (c) 2026 David Michael Indraputra

"""Atomic local orchestration record and policy persistence."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

from ...foundations.atomic_io import (
    Fingerprint,
    file_fingerprint,
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ...foundations.errors import AgentSdkError
from ...foundations.identifiers import (
    file_safe_name,
    is_valid_identifier,
    reserve_sequential_identifier,
    validate_identifier,
)
from ...foundations.record_locks import RecordLocks
from .models import OrchestrationPolicy, OrchestrationRecord


class OrchestrationStateStore:
    """Atomic local record store; provider credentials and callbacks are excluded."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-orchestrations"
        self._root.mkdir(parents=True, exist_ok=True)
        self._claims = self._root / ".claims"
        self._policies = self._root / "policies"
        self._policies.mkdir(parents=True, exist_ok=True)
        self._locks = RecordLocks(self._root / ".locks", timeout_code="ORCHESTRATION_LOCK_TIMEOUT")
        self._policy_locks = RecordLocks(
            self._policies / ".locks", timeout_code="ORCHESTRATION_LOCK_TIMEOUT"
        )
        self._seen: dict[str, Fingerprint] = {}

    def reserve_orchestration_id(self, *, start: int = 1) -> tuple[str, int]:
        return reserve_sequential_identifier(
            self._claims, "orchestration", self.exists, start=start
        )

    def locked(self, orchestration_id: str) -> AbstractContextManager[None]:
        return self._locks.hold(validate_identifier(orchestration_id, "Orchestration id"))

    def save(self, record: OrchestrationRecord) -> None:
        with self.locked(record.orchestration_id):
            self._require_unchanged(record.orchestration_id)
            target = self._record_path(record.orchestration_id)
            temporary = unique_temporary_path(target)
            temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            replace_atomic(temporary, target)
            fingerprint = self.fingerprint(record.orchestration_id)
            if fingerprint is not None:
                self._seen[record.orchestration_id] = fingerprint

    def load(self, orchestration_id: str) -> OrchestrationRecord:
        target = self._record_path(orchestration_id)
        fingerprint = file_fingerprint(target)
        if fingerprint is None:
            raise ValueError(f'Orchestration "{orchestration_id}" is unknown.')
        record = OrchestrationRecord.model_validate_json(read_text_retrying(target))
        self._seen[orchestration_id] = fingerprint
        return record

    def exists(self, orchestration_id: str) -> bool:
        if not is_valid_identifier(orchestration_id):
            return False
        return self._record_path(orchestration_id).is_file()

    def fingerprint(self, orchestration_id: str) -> Fingerprint | None:
        return file_fingerprint(self._record_path(orchestration_id))

    def save_policy(self, policy: OrchestrationPolicy) -> None:
        """Persist a non-secret policy and reject a same-ID semantic rewrite."""

        target = self._policy_path(policy.policy_id)
        serialized = policy.model_dump_json(indent=2)
        with self._policy_locks.hold(policy.policy_id):
            if target.is_file():
                existing = OrchestrationPolicy.model_validate_json(read_text_retrying(target))
                if existing != policy:
                    raise ValueError(
                        f'Policy "{policy.policy_id}" already exists with different authority '
                        "fields."
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

    def _require_unchanged(self, orchestration_id: str) -> None:
        seen = self._seen.get(orchestration_id)
        if seen is None or self.fingerprint(orchestration_id) == seen:
            return
        raise AgentSdkError(
            "ORCHESTRATION_STATE_CONFLICT",
            f'Orchestration "{orchestration_id}" was changed on disk by another writer after '
            "this store last read or wrote it, so saving now would overwrite that change. "
            "Reload the orchestration with Orchestrator.get before changing it.",
            {"orchestration_id": orchestration_id},
        )

    def _record_path(self, orchestration_id: str) -> Path:
        return self._root / f"{validate_identifier(orchestration_id, 'Orchestration id')}.json"

    def _policy_path(self, policy_id: str) -> Path:
        return self._policies / f"{file_safe_name(policy_id)}.json"
