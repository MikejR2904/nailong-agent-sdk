# Copyright (c) 2026 David Michael Indraputra

"""The independent recorder: a hash-chained ground-truth log the system cannot write."""

from __future__ import annotations

import json
import threading
from collections.abc import Collection
from pathlib import Path

from .schema import Event, digest_bytes, digest_of


class BoundaryRecorder:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._handle = path.open("w", encoding="utf-8", newline="\n")
        self._previous = ""
        self._events: list[Event] = []
        self._lock = threading.Lock()

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: Event) -> Event:
        with self._lock:
            line = event.as_dict()
            line["n"] = len(self._events) + 1
            line["prev"] = self._previous
            line["hash"] = digest_of(line)
            self._handle.write(json.dumps(line, sort_keys=True) + "\n")
            self._handle.flush()
            self._previous = line["hash"]
            self._events.append(event)
        return event

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


def load_recorded(path: Path) -> list[Event]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(Event.from_dict(json.loads(line)))
    return events


def verify_recorded(path: Path) -> bool:
    previous = ""
    expected = 1
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        claimed = data.pop("hash", None)
        if data.get("prev") != previous or data.get("n") != expected:
            return False
        if digest_of(data) != claimed:
            return False
        previous = claimed
        expected += 1
    return True


class WorkspaceWatcher:
    def __init__(self, root: Path, paths: Collection[str] | None = None) -> None:
        self._root = root
        self._paths = None if paths is None else frozenset(paths)
        self._known = self.snapshot()

    def snapshot(self) -> dict[str, str]:
        found: dict[str, str] = {}
        if not self._root.is_dir():
            return found
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self._root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if self._paths is not None and relative.as_posix() not in self._paths:
                continue
            found[relative.as_posix()] = digest_bytes(path.read_bytes())
        return found

    def changes(self) -> list[tuple[str, str]]:
        current = self.snapshot()
        changed = [
            (path, value) for path, value in current.items() if self._known.get(path) != value
        ]
        self._known = current
        return changed
