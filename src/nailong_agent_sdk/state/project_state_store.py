# Copyright (c) 2026 David Michael Indraputra

"""In-memory and file-backed project state stores with an append-only event audit trail."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic
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
        event = _make_event(current, next_state, transition)
        self._states[project_id] = next_state
        self._events[project_id].append(event)
        return next_state

    def events(self, project_id: str) -> tuple[ProjectStateEvent, ...]:
        self.load(project_id)
        return tuple(self._events[project_id])


class FileProjectStateStore(InMemoryProjectStateStore):
    """Atomic persistent project state plus an append-only revision-event audit trail."""

    def __init__(self, run_root: Path) -> None:
        super().__init__()
        self._root = run_root.resolve() / ".agent-project-state"
        self._root.mkdir(parents=True, exist_ok=True)
        self._fingerprints: dict[str, tuple[int, int, int] | None] = {}

    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState:
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
        self._states[project_id] = state
        self._events[project_id] = self._read_events(project_id)
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
        self._fingerprints[project_id] = self._fingerprint(project_id)
        return next_state

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
        temporary.write_text(_canonical_json(value), encoding="utf-8")
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
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        return str(item)

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=default)
