# Copyright (c) 2026 David Michael Indraputra

"""In-memory and file-backed project state stores with an append-only event audit trail."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import (
    Fingerprint,
    file_fingerprint,
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ..foundations.hashing import model_canonical_json, sha256_hex
from ..foundations.record_locks import RecordLocks
from .project_state_engine import ProjectStateReducer
from .project_state_models import (
    ProjectState,
    ProjectStateEvent,
    StageStateSchema,
    StateTransition,
    _event_hash,
    make_project_state,
)


class InMemoryProjectStateStore:
    """Deterministic state repository for a bounded task and unit tests."""

    def __init__(self) -> None:
        self._states: dict[str, ProjectState] = {}
        self._events: dict[str, list[ProjectStateEvent]] = {}
        self._lock = threading.RLock()

    @contextmanager
    def locked(self, project_id: str) -> Iterator[None]:
        with self._lock:
            yield

    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState:
        with self._lock:
            if project_id not in self._states:
                self._states[project_id] = make_project_state(
                    project_id=project_id,
                    revision=0,
                    stage_schema=stage_schema,
                )
                self._events[project_id] = []
            return self._states[project_id]

    def load(self, project_id: str) -> ProjectState:
        with self._lock:
            state = self._states.get(project_id)
            if state is None:
                raise ValueError(f'Project state "{project_id}" is unknown.')
            return state

    def apply(
        self,
        project_id: str,
        transition: StateTransition,
        *,
        summary_max_chars: int = 2_048,
    ) -> ProjectState:
        with self._lock:
            current = self.load(project_id)
            next_state = ProjectStateReducer.apply(
                current, transition, summary_max_chars=summary_max_chars
            )
            event = _make_event(current, next_state, transition)
            self._states[project_id] = next_state
            self._events[project_id].append(event)
            return next_state

    def events(self, project_id: str) -> tuple[ProjectStateEvent, ...]:
        with self._lock:
            self.load(project_id)
            return tuple(self._events[project_id])


class FileProjectStateStore(InMemoryProjectStateStore):
    """Atomic persistent project state plus an append-only revision-event audit trail."""

    def __init__(self, run_root: Path, *, read_only: bool = False) -> None:
        super().__init__()
        self._root = run_root.resolve() / ".agent-project-state"
        self._read_only = read_only
        if not read_only:
            self._root.mkdir(parents=True, exist_ok=True)
        self._locks = RecordLocks(self._root / ".locks", timeout_code="PROJECT_STATE_LOCK_TIMEOUT")
        self._fingerprints: dict[str, Fingerprint | None] = {}

    @contextmanager
    def locked(self, project_id: str) -> Iterator[None]:
        self._require_writable("locked")
        with self._lock, self._locks.hold(project_id):
            yield

    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState:
        target = self._state_path(project_id)
        if target.is_file():
            return self.load(project_id)
        self._require_writable("ensure")
        with self.locked(project_id):
            if target.is_file():
                return self.load(project_id)
            state = super().ensure(project_id, stage_schema)
            self._write_json(target, state.model_dump(mode="json"))
            self._fingerprints[project_id] = file_fingerprint(target)
            return state

    def load(self, project_id: str) -> ProjectState:
        with self._lock:
            fingerprint = file_fingerprint(self._state_path(project_id))
            if project_id in self._states and self._fingerprints.get(project_id) == fingerprint:
                return super().load(project_id)
            target = self._state_path(project_id)
            if not target.is_file():
                raise ValueError(f'Project state "{project_id}" is unknown.')
            state = ProjectState.model_validate_json(read_text_retrying(target))
            self._states[project_id] = state
            self._events[project_id] = self._read_events(
                project_id, through_revision=state.revision
            )
            self._verify_history(project_id)
            self._fingerprints[project_id] = fingerprint
            return state

    def apply(
        self,
        project_id: str,
        transition: StateTransition,
        *,
        summary_max_chars: int = 2_048,
    ) -> ProjectState:
        self._require_writable("apply")
        self.load(project_id)
        with self.locked(project_id):
            current = self.load(project_id)
            next_state = ProjectStateReducer.apply(
                current, transition, summary_max_chars=summary_max_chars
            )
            event = _make_event(current, next_state, transition)
            self._write_json(
                self._event_path(project_id, event.revision), event.model_dump(mode="json")
            )
            self._write_json(self._state_path(project_id), next_state.model_dump(mode="json"))
            self._states[project_id] = next_state
            self._events[project_id].append(event)
            self._fingerprints[project_id] = file_fingerprint(self._state_path(project_id))
            return next_state

    def _require_writable(self, operation: str) -> None:
        if self._read_only:
            raise RuntimeError(
                f'Project state store at "{self._root}" was opened read-only; "{operation}" '
                "is not available."
            )

    def _state_path(self, project_id: str) -> Path:
        return self._root / f"{_safe_id(project_id)}.json"

    def _event_path(self, project_id: str, revision: int) -> Path:
        path = self._root / _safe_id(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{revision:08d}.json"

    def _read_events(self, project_id: str, *, through_revision: int) -> list[ProjectStateEvent]:
        event_root = self._root / _safe_id(project_id)
        if not event_root.is_dir():
            return []
        events: list[ProjectStateEvent] = []
        for path in sorted(event_root.glob("*.json")):
            if not path.stem.isdigit():
                raise ValueError(
                    f'Project state "{project_id}" has an event file "{path.name}" whose name '
                    "is not a revision number."
                )
            if int(path.stem) > through_revision:
                break
            events.append(ProjectStateEvent.model_validate_json(read_text_retrying(path)))
        return events

    def _verify_history(self, project_id: str) -> None:
        state = self._states[project_id]
        events = self._events[project_id]
        if state.revision != len(events):
            raise ValueError(
                f'Project state "{project_id}" is at revision {state.revision} but '
                f"{len(events)} events are persisted up to that revision."
            )
        previous_hash: str | None = None
        for expected_revision, event in enumerate(events, start=1):
            if event.revision != expected_revision:
                raise ValueError(
                    f'Project state "{project_id}" event revisions are not contiguous: '
                    f"expected {expected_revision}, found {event.revision}."
                )
            if previous_hash is not None and event.previous_state_hash != previous_hash:
                raise ValueError(
                    f'Project state "{project_id}" event {event.revision} has a broken '
                    "state-hash chain."
                )
            previous_hash = event.state_hash
        if events and events[-1].state_hash != state.state_hash:
            raise ValueError(
                f'Latest project state hash of "{project_id}" does not match event '
                f"{events[-1].revision}."
            )

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = unique_temporary_path(path)
        temporary.write_text(model_canonical_json(value), encoding="utf-8")
        replace_atomic(temporary, path)


def _make_event(
    previous: ProjectState,
    current: ProjectState,
    transition: StateTransition,
) -> ProjectStateEvent:
    return ProjectStateEvent(
        project_id=current.project_id,
        revision=current.revision,
        kind=transition.kind,
        actor=transition.actor,
        action_id=transition.action_id,
        evidence=transition.evidence,
        previous_state_hash=previous.state_hash,
        state_hash=current.state_hash,
        event_hash=_event_hash(
            current.project_id,
            current.revision,
            transition.kind,
            transition.actor,
            transition.action_id,
            transition.evidence,
            previous.state_hash,
            current.state_hash,
        ),
    )


def _safe_id(value: str) -> str:
    return sha256_hex(value)
