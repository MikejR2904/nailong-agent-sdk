# Copyright (c) 2026 David Michael Indraputra

"""In-memory and file-backed project state stores with an append-only event audit trail."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic
from ..foundations.canonical import canonical_json
from ..foundations.file_lock import FileLock
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
    """Process-local project state for one bounded task (nothing is persisted)."""

    def __init__(self) -> None:
        self._states: dict[str, ProjectState] = {}
        self._events: dict[str, list[ProjectStateEvent]] = {}

    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState:
        if project_id not in self._states:
            self._states[project_id] = make_project_state(
                project_id=project_id,
                revision=0,
                stage_schema=stage_schema,
            )
            self._events[project_id] = []
        return self._states[project_id]

    def load(self, project_id: str) -> ProjectState:
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
        current = self.load(project_id)
        next_state = ProjectStateReducer.apply(
            current, transition, summary_max_chars=summary_max_chars
        )
        event = _make_event(current, next_state, transition, summary_max_chars)
        self._states[project_id] = next_state
        self._events[project_id].append(event)
        return next_state

    def events(self, project_id: str) -> tuple[ProjectStateEvent, ...]:
        self.load(project_id)
        return tuple(self._events[project_id])


class FileProjectStateStore(InMemoryProjectStateStore):
    """Atomic persistent project state plus an append-only revision-event audit trail.

    ``apply`` writes the event, then the state, under a per-project cross-process
    lock. If a process dies between those two writes, the next ``load`` rolls the
    state forward by replaying the last event's transition and accepts the result
    only if it reproduces the hash that event recorded.
    """

    def __init__(self, run_root: Path) -> None:
        super().__init__()
        self._root = run_root.resolve() / ".agent-project-state"
        self._root.mkdir(parents=True, exist_ok=True)
        self._fingerprints: dict[str, tuple[int, int, int] | None] = {}

    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState:
        with self._lock(project_id).hold():
            target = self._state_path(project_id)
            if target.is_file():
                return self.load(project_id)
            state = super().ensure(project_id, stage_schema)
            self._write_json(target, state.model_dump(mode="json"))
            self._fingerprints[project_id] = self._fingerprint(project_id)
            return state

    def load(self, project_id: str) -> ProjectState:
        fingerprint = self._fingerprint(project_id)
        if project_id in self._states and self._fingerprints.get(project_id) == fingerprint:
            return super().load(project_id)
        target = self._state_path(project_id)
        if not target.is_file():
            raise ValueError(f'Project state "{project_id}" is unknown.')
        state = ProjectState.model_validate_json(target.read_text(encoding="utf-8"))
        events = self._read_events(project_id)
        if state.revision == len(events) - 1:
            with self._lock(project_id).hold():
                state = self._roll_forward(project_id, state, events[-1])
            fingerprint = self._fingerprint(project_id)
        self._states[project_id] = state
        self._events[project_id] = events
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
        with self._lock(project_id).hold():
            # Reload under the lock so a change by another process is the base.
            current = self.load(project_id)
            next_state = ProjectStateReducer.apply(
                current, transition, summary_max_chars=summary_max_chars
            )
            event = _make_event(current, next_state, transition, summary_max_chars)
            self._write_json(
                self._event_path(project_id, event.revision), event.model_dump(mode="json")
            )
            self._write_json(self._state_path(project_id), next_state.model_dump(mode="json"))
            self._states[project_id] = next_state
            self._events[project_id].append(event)
            self._fingerprints[project_id] = self._fingerprint(project_id)
            return next_state

    def _roll_forward(
        self, project_id: str, state: ProjectState, event: ProjectStateEvent
    ) -> ProjectState:
        """Finish an ``apply`` that wrote its event but died before writing the state."""

        current = ProjectState.model_validate_json(
            self._state_path(project_id).read_text(encoding="utf-8")
        )
        if current.revision != state.revision:
            return current  # another process already recovered it
        if event.previous_state_hash != state.state_hash or event.payload is None:
            raise ValueError(
                f'Project state "{project_id}" is one revision behind its event log and the '
                "last event cannot be replayed (it predates replay data or does not follow "
                "this state). Remove the last event file to discard that transition."
            )
        transition = StateTransition(
            kind=event.kind,
            actor=event.actor,
            action_id=event.action_id,
            payload=event.payload,
            evidence=event.evidence,
        )
        recovered = ProjectStateReducer.apply(
            state, transition, summary_max_chars=event.summary_max_chars or 2_048
        )
        if recovered.state_hash != event.state_hash:
            raise ValueError(
                f'Replaying event {event.revision} of project "{project_id}" did not reproduce '
                "its recorded state hash; the event log was altered."
            )
        self._write_json(self._state_path(project_id), recovered.model_dump(mode="json"))
        return recovered

    def _lock(self, project_id: str) -> FileLock:
        return FileLock(self._state_path(project_id))

    def _fingerprint(self, project_id: str) -> tuple[int, int, int] | None:
        try:
            status = self._state_path(project_id).stat()
        except FileNotFoundError:
            return None
        return status.st_ino, status.st_mtime_ns, status.st_size

    def _state_path(self, project_id: str) -> Path:
        return self._root / f"{_safe_id(project_id)}.json"

    def _event_path(self, project_id: str, revision: int) -> Path:
        path = self._root / _safe_id(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{revision:08d}.json"

    def _read_events(self, project_id: str) -> list[ProjectStateEvent]:
        event_root = self._root / _safe_id(project_id)
        if not event_root.is_dir():
            return []
        return [
            ProjectStateEvent.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(event_root.glob("*.json"))
        ]

    def _verify_history(self, project_id: str) -> None:
        state = self._states[project_id]
        events = self._events[project_id]
        if state.revision != len(events):
            raise ValueError("Project state revision does not match persisted event count.")
        previous_hash: str | None = None
        for expected_revision, event in enumerate(events, start=1):
            if event.revision != expected_revision:
                raise ValueError("Project state event revisions are not contiguous.")
            if previous_hash is not None and event.previous_state_hash != previous_hash:
                raise ValueError("Project state event history has a broken state-hash chain.")
            previous_hash = event.state_hash
        if events and events[-1].state_hash != state.state_hash:
            raise ValueError("Latest project state hash does not match the event history.")

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(canonical_json(value, models=True), encoding="utf-8")
        replace_atomic(temporary, path)


def _make_event(
    previous: ProjectState,
    current: ProjectState,
    transition: StateTransition,
    summary_max_chars: int,
) -> ProjectStateEvent:
    return ProjectStateEvent(
        payload=transition.payload,
        summary_max_chars=summary_max_chars,
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
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
