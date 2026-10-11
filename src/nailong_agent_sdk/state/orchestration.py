# Copyright (c) 2026 David Michael Indraputra

"""Deterministic upward/downward controller state machine for approved agent graph runs."""

from __future__ import annotations

import hashlib
import os
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import (
    Fingerprint,
    file_fingerprint,
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ..foundations.errors import AgentSdkError
from ..foundations.identifiers import (
    is_valid_identifier,
    reserve_sequential_identifier,
    validate_identifier,
)
from ..foundations.record_locks import RecordLocks
from .elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from .orchestration_models import (
    ComplexityRouter,
    ComplexityRoutingRules,
    ControllerEvent,
    ControllerPhase,
    ControllerRecord,
    GapMetadata,
    SkillToolProfile,
    WorkflowArchitecture,
)
from .planning import Plan, PlanValidator
from .shared_state import SharedSubstrateSnapshot


class ControllerStateMachine:
    """Own plan review, dispatch readiness, bounded repair, and user escalation."""

    def __init__(
        self,
        controller_id: str,
        snapshot: SharedSubstrateSnapshot,
        profile: SkillToolProfile,
        routing_rules: ComplexityRoutingRules,
        gap_metadata: GapMetadata,
        *,
        project_state_id: str,
        max_repair_attempts: int,
        elastic_depth_ceiling: int = MAX_ELASTIC_DEPTH_LIMIT,
        elastic_nodes_ceiling: int = MAX_ELASTIC_NODES_LIMIT,
    ) -> None:
        if profile.source_snapshot_id != snapshot.snapshot_id or not profile.source_read_only:
            raise ValueError("Controller profile must bind the immutable source snapshot.")
        self._validator = PlanValidator()
        self.record = ControllerRecord(
            controller_id=controller_id,
            project_state_id=project_state_id,
            phase=ControllerPhase.PLANNING,
            architecture=ComplexityRouter(routing_rules).route(gap_metadata),
            profile=profile,
            snapshot=snapshot,
            max_repair_attempts=max_repair_attempts,
            elastic_depth_ceiling=elastic_depth_ceiling,
            elastic_nodes_ceiling=elastic_nodes_ceiling,
        )
        self._emit("controller-started", {"gap_metadata": gap_metadata.model_dump(mode="json")})

    @classmethod
    def from_record(cls, record: ControllerRecord) -> ControllerStateMachine:
        instance = cls.__new__(cls)
        instance._validator = PlanValidator()
        instance.record = record
        return instance

    def submit_plan(self, plan: Plan) -> ControllerRecord:
        self._require(ControllerPhase.PLANNING, ControllerPhase.REPAIR_REQUIRED)
        for name, ceiling in (
            ("max_elastic_depth", self.record.elastic_depth_ceiling),
            ("max_elastic_nodes", self.record.elastic_nodes_ceiling),
        ):
            declared = getattr(plan, name)
            if declared > ceiling:
                raise ValueError(
                    f'Plan "{plan.plan_id}" declares {name} {declared}, above the ceiling '
                    f'{name} {ceiling} of controller "{self.record.controller_id}".'
                )
        validation = self._validator.validate(plan)
        self.record = self.record.model_copy(
            update={
                "plan": plan,
                "plan_validation": validation,
                "plan_approved": None,
                "phase": ControllerPhase.AWAITING_PLAN_APPROVAL,
            }
        )
        self._emit("plan-presented", {"valid": validation.valid, "plan_id": plan.plan_id})
        return self.record

    def apply_advisory_architecture(
        self,
        architecture: WorkflowArchitecture,
        *,
        reason: str,
    ) -> ControllerRecord:
        """Record only a monotonic single-to-multi advisory routing lift.

        Deterministic complexity routing is the default.  An external advisory
        cannot lower a deterministic multi-agent requirement or mutate routing
        after plan review begins; it can only make a conservative planning-phase
        single-to-multi escalation explicit and auditable.
        """

        self._require(ControllerPhase.PLANNING)
        current = self.record.architecture
        if architecture is current:
            return self.record
        if (
            current is WorkflowArchitecture.SINGLE_AGENT
            and architecture is WorkflowArchitecture.MULTI_AGENT
        ):
            self.record = self.record.model_copy(update={"architecture": architecture})
            self._emit("architecture-advisory-lift", {"reason": reason})
            return self.record
        raise ValueError("Architecture selection may not lower a deterministic route.")

    def approve_plan(self, approved: bool, reason: str | None = None) -> ControllerRecord:
        self._require(ControllerPhase.AWAITING_PLAN_APPROVAL)
        if self.record.plan_validation is None or not self.record.plan_validation.valid:
            raise ValueError("Only a deterministically valid plan can be approved for dispatch.")
        phase = ControllerPhase.DISPATCH_READY if approved else ControllerPhase.PLANNING
        self.record = self.record.model_copy(update={"plan_approved": approved, "phase": phase})
        self._emit("plan-approval-recorded", {"approved": approved, "reason": reason})
        return self.record

    def begin_dispatch(self) -> ControllerRecord:
        self._require(ControllerPhase.DISPATCH_READY)
        self.record = self.record.model_copy(update={"phase": ControllerPhase.EXECUTING})
        self._emit("dispatch-started", {"architecture": self.record.architecture.value})
        return self.record

    def bind_run(self, run_id: str) -> ControllerRecord:
        self._require(ControllerPhase.EXECUTING)
        previous_run_id = self.record.run_id
        self.record = self.record.model_copy(update={"run_id": run_id})
        self._emit(
            "graph-run-bound" if previous_run_id is None else "graph-run-rebound",
            {"run_id": run_id, "previous_run_id": previous_run_id},
        )
        return self.record

    def record_stage_failure(self, reason: str) -> ControllerRecord:
        self._require(ControllerPhase.EXECUTING)
        next_attempt = self.record.repair_attempts + 1
        if next_attempt > self.record.max_repair_attempts:
            self.record = self.record.model_copy(
                update={"phase": ControllerPhase.ESCALATED, "escalation_reason": reason}
            )
            self._emit("user-escalation-required", {"reason": reason})
            return self.record
        self.record = self.record.model_copy(
            update={"phase": ControllerPhase.REPAIR_REQUIRED, "repair_attempts": next_attempt}
        )
        self._emit("bounded-repair-requested", {"reason": reason, "attempt": next_attempt})
        return self.record

    def complete(self) -> ControllerRecord:
        self._require(ControllerPhase.EXECUTING)
        self.record = self.record.model_copy(update={"phase": ControllerPhase.COMPLETED})
        self._emit("controller-completed", {})
        return self.record

    def require_cancellable(self) -> None:
        if self.record.phase in {ControllerPhase.COMPLETED, ControllerPhase.CANCELLED}:
            raise ValueError(
                f'Controller "{self.record.controller_id}" is already {self.record.phase.value}, '
                "a terminal phase, so it cannot be cancelled."
            )

    def cancel(self, reason: str) -> ControllerRecord:
        self.require_cancellable()
        self.record = self.record.model_copy(update={"phase": ControllerPhase.CANCELLED})
        self._emit("controller-cancelled", {"reason": reason})
        return self.record

    def _require(self, *allowed: ControllerPhase) -> None:
        if self.record.phase not in allowed:
            labels = ", ".join(item.value for item in allowed)
            raise ValueError(
                f'Controller command is invalid in phase "{self.record.phase.value}"; '
                f"expected {labels}."
            )

    def _emit(self, type_: str, details: dict[str, Any]) -> None:
        event = ControllerEvent(
            sequence=len(self.record.events) + 1,
            phase=self.record.phase,
            type=type_,
            details=details,
        )
        self.record = self.record.model_copy(update={"events": [*self.record.events, event]})


def _replace_with_retry(temporary: Path, target: Path, *, attempts: int = 5) -> None:
    """Retry atomic replacement after transient destination-handle failures."""

    replace_atomic(temporary, target, attempts=attempts)


class ControllerStateStore:
    """Persist controller metadata with a snapshot-bound append-only event log.

    Snapshots with an event hash name the durable event prefix they own. A failed
    replacement therefore leaves a recoverable prior generation, not a record
    combined with an uncommitted suffix. Older one-file records remain readable.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-controllers"
        self._root.mkdir(parents=True, exist_ok=True)
        self._claims = self._root / ".claims"
        self._locks = RecordLocks(self._root / ".locks", timeout_code="CONTROLLER_LOCK_TIMEOUT")
        self._seen: dict[str, Fingerprint] = {}
        self._persisted_event_counts: dict[str, int] = {}

    def reserve_controller_id(self, *, start: int = 1) -> tuple[str, int]:
        return reserve_sequential_identifier(self._claims, "controller", self.exists, start=start)

    def locked(self, controller_id: str) -> AbstractContextManager[None]:
        return self._locks.hold(validate_identifier(controller_id, "Controller id"))

    def save(self, record: ControllerRecord) -> None:
        with self.locked(record.controller_id):
            self._require_unchanged(record.controller_id)
            self._save_locked(record)
            fingerprint = self.fingerprint(record.controller_id)
            if fingerprint is not None:
                self._seen[record.controller_id] = fingerprint

    def changed_since_read(self, controller_id: str) -> bool:
        return self._seen.get(controller_id) != self.fingerprint(controller_id)

    def fingerprint(self, controller_id: str) -> Fingerprint | None:
        return file_fingerprint(self._record_path(controller_id))

    def _require_unchanged(self, controller_id: str) -> None:
        seen = self._seen.get(controller_id)
        if seen is None or self.fingerprint(controller_id) == seen:
            return
        raise AgentSdkError(
            "CONTROLLER_STATE_CONFLICT",
            f'Controller "{controller_id}" was changed on disk by another writer after this '
            "store last read or wrote it, so saving now would overwrite that change. Reload "
            "the controller with ControllerRuntime.get_controller before changing it.",
            {"controller_id": controller_id},
        )

    def _save_locked(self, record: ControllerRecord) -> None:
        persisted = self._persisted_event_counts.get(record.controller_id)
        if persisted is None:
            persisted = self._committed_event_count(record.controller_id)
        event_path = self._events_path(record.controller_id)
        self._discard_uncommitted_events(record.controller_id, persisted)
        if len(record.events) < persisted:
            # A reused controller ID has a shorter in-memory event history, so
            # replace the sidecar before publishing the new snapshot boundary.
            self._write_events(record.controller_id, record.events)
        elif len(record.events) > persisted:
            self._append_events(record.controller_id, record.events[persisted:])
        event_count, event_hash = _event_boundary(event_path)
        if event_count != len(record.events):
            raise ValueError("Controller event boundary does not match the record event count.")
        target = self._record_path(record.controller_id)
        temporary = unique_temporary_path(target)
        snapshot = record.model_copy(
            update={
                "events": [],
                "events_entry_count": event_count,
                "events_integrity_hash": event_hash,
            }
        )
        _write_json_candidate(temporary, snapshot.model_dump_json(indent=2))
        _replace_with_retry(temporary, target)
        self._persisted_event_counts[record.controller_id] = len(record.events)

    def exists(self, controller_id: str) -> bool:
        return is_valid_identifier(controller_id) and self._record_path(controller_id).is_file()

    def load(self, controller_id: str) -> ControllerRecord:
        target = self._record_path(controller_id)
        fingerprint = file_fingerprint(target)
        if fingerprint is None:
            raise ValueError(f'Controller "{controller_id}" is unknown.')
        snapshot = ControllerRecord.model_validate_json(read_text_retrying(target))
        self._seen[controller_id] = fingerprint
        if snapshot.events_integrity_hash is None:
            self._persisted_event_counts[controller_id] = 0
            return snapshot
        events, event_hash = self._read_events(
            controller_id, entry_limit=snapshot.events_entry_count
        )
        if (
            len(events) != snapshot.events_entry_count
            or event_hash != snapshot.events_integrity_hash
        ):
            raise ValueError(
                f'Controller "{controller_id}" event history failed integrity verification.'
            )
        _validate_event_sequence(events)
        record = snapshot.model_copy(update={"events": events})
        self._persisted_event_counts[controller_id] = len(events)
        return record

    def _record_path(self, controller_id: str) -> Path:
        return self._root / f"{validate_identifier(controller_id, 'Controller id')}.json"

    def _events_path(self, controller_id: str) -> Path:
        validated = validate_identifier(controller_id, "Controller id")
        return self._root / f"{validated}.events.jsonl"

    def _append_events(self, controller_id: str, events: list[ControllerEvent]) -> None:
        path = self._events_path(controller_id)
        with path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(event.model_dump_json())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_events(self, controller_id: str, events: list[ControllerEvent]) -> None:
        path = self._events_path(controller_id)
        temporary = unique_temporary_path(path)
        with temporary.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(event.model_dump_json())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)

    def _read_events(
        self, controller_id: str, *, entry_limit: int | None = None
    ) -> tuple[list[ControllerEvent], str]:
        path = self._events_path(controller_id)
        if not path.is_file():
            return [], _event_hash([])
        with path.open(encoding="utf-8") as handle:
            events: list[ControllerEvent] = []
            for line in handle:
                if not line.strip():
                    continue
                if entry_limit is not None and len(events) >= entry_limit:
                    break
                events.append(ControllerEvent.model_validate_json(line))
        return events, _event_hash(events)

    def _committed_event_count(self, controller_id: str) -> int:
        target = self._record_path(controller_id)
        if not target.is_file():
            return 0
        snapshot = ControllerRecord.model_validate_json(read_text_retrying(target))
        if snapshot.events_integrity_hash is None:
            return 0
        events, event_hash = self._read_events(
            controller_id, entry_limit=snapshot.events_entry_count
        )
        if (
            len(events) != snapshot.events_entry_count
            or event_hash != snapshot.events_integrity_hash
        ):
            raise ValueError(
                f'Controller "{controller_id}" event history failed integrity verification.'
            )
        return len(events)

    def _discard_uncommitted_events(self, controller_id: str, committed_count: int) -> None:
        path = self._events_path(controller_id)
        if _nonempty_event_line_count(path) <= committed_count:
            return
        events, _event_hash_value = self._read_events_from_path(path, entry_limit=committed_count)
        self._write_events(controller_id, events)

    @staticmethod
    def _read_events_from_path(
        path: Path, *, entry_limit: int | None = None
    ) -> tuple[list[ControllerEvent], str]:
        if not path.is_file():
            return [], _event_hash([])
        with path.open(encoding="utf-8") as handle:
            events: list[ControllerEvent] = []
            for line in handle:
                if not line.strip():
                    continue
                if entry_limit is not None and len(events) >= entry_limit:
                    break
                events.append(ControllerEvent.model_validate_json(line))
        return events, _event_hash(events)


def _event_boundary(path: Path) -> tuple[int, str]:
    if not path.is_file():
        return 0, _event_hash([])
    with path.open(encoding="utf-8") as handle:
        events = [ControllerEvent.model_validate_json(line) for line in handle if line.strip()]
    return len(events), _event_hash(events)


def _nonempty_event_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _event_hash(events: list[ControllerEvent]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(event.model_dump_json().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_event_sequence(events: list[ControllerEvent]) -> None:
    expected = list(range(1, len(events) + 1))
    actual = [event.sequence for event in events]
    if actual != expected:
        raise ValueError("Controller event history has non-contiguous sequence numbers.")


def _write_json_candidate(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
