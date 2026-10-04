# Copyright (c) 2026 David Michael Indraputra

"""Durable run-record persistence: a fixed-state snapshot plus a growing-state sidecar.

Snapshots with a history hash name the exact sidecar prefix they own. A
restart ignores any later suffix, which makes a failed replacement recover
to the previous complete generation. Older full snapshots remain readable.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import (
    read_text_retrying,
    replace_atomic,
    unique_temporary_path,
)
from ..foundations.identifiers import (
    is_valid_identifier,
    reserve_sequential_identifier,
    validate_identifier,
)
from .coordination_records import RunRecord, _hash_run


def _replace_with_retry(temporary: Path, target: Path, *, attempts: int = 5) -> None:
    """Retry atomic replacement only for transient destination-handle failures."""

    replace_atomic(temporary, target, attempts=attempts)


class RunStateStore:
    """Persist fixed graph state in a snapshot and growing state in a sidecar.

    Snapshots with a history hash name the exact sidecar prefix they own. A
    restart ignores any later suffix, which makes a failed replacement recover
    to the previous complete generation. Older full snapshots remain readable.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-runs"
        self._root.mkdir(parents=True, exist_ok=True)
        self._claims = self._root / ".claims"
        # Process-local cursors for the snapshot-bound history prefix. They are
        # rebuilt from the verified durable boundary after a restart.
        self._persisted: dict[str, _PersistedHistoryCounts] = {}

    def reserve_run_id(self, *, start: int = 1) -> tuple[str, int]:
        return reserve_sequential_identifier(self._claims, "run", self.exists, start=start)

    def save(self, record: RunRecord) -> None:
        counts = self._counts_for(record.run_id)
        history_path = self._history_path(record.run_id)
        self._discard_uncommitted_history(record.run_id, history_path, counts)
        if _history_shrunk(record.graph, counts):
            # A reused run ID has a shorter in-memory history, so start a new
            # sidecar before publishing the replacement snapshot.
            history_path.unlink(missing_ok=True)
            counts = _PersistedHistoryCounts()
        new_entries, counts = _diff_history(record.graph, counts)
        if new_entries:
            _append_history(history_path, new_entries, counts)
        target = self._record_path(record.run_id)
        temporary = unique_temporary_path(target)
        snapshot = record.model_copy(
            update={
                "graph": _emptied_history(record.graph),
                "history_entry_count": counts.entry_count,
                "history_integrity_hash": counts.digest.hexdigest(),
            }
        )
        _write_json_atomic_candidate(temporary, snapshot.model_dump_json(indent=2))
        _replace_with_retry(temporary, target)
        self._persisted[record.run_id] = counts

    def load(self, run_id: str) -> RunRecord:
        target = self._record_path(run_id)
        if not target.is_file():
            raise ValueError(f'Run "{run_id}" is unknown.')
        snapshot = RunRecord.model_validate_json(read_text_retrying(target))
        if snapshot.history_integrity_hash is None:
            # Legacy full snapshots retain their embedded history until a later
            # successful split-format save publishes a bound sidecar prefix.
            self._persisted[run_id] = _PersistedHistoryCounts()
            _verify_run_record(snapshot)
            return snapshot
        history, counts, history_hash = _replay_history(
            self._history_path(run_id), entry_limit=snapshot.history_entry_count
        )
        if (
            counts.entry_count != snapshot.history_entry_count
            or history_hash != snapshot.history_integrity_hash
        ):
            raise ValueError(f'Run "{run_id}" history failed integrity verification.')
        self._persisted[run_id] = counts
        graph = _merge_history(snapshot.graph, history)
        record = snapshot.model_copy(update={"graph": graph})
        _verify_run_record(record)
        return record

    def exists(self, run_id: str) -> bool:
        return is_valid_identifier(run_id) and self._record_path(run_id).is_file()

    def fingerprint(self, run_id: str) -> tuple[int, int, int] | None:
        try:
            status = self._record_path(run_id).stat()
        except FileNotFoundError:
            return None
        return status.st_ino, status.st_mtime_ns, status.st_size

    def approvals_path(self, run_id: str) -> Path:
        return self._root / f"{validate_identifier(run_id, 'Run id')}.approvals.json"

    def _record_path(self, run_id: str) -> Path:
        return self._root / f"{validate_identifier(run_id, 'Run id')}.json"

    def _history_path(self, run_id: str) -> Path:
        return self._root / f"{validate_identifier(run_id, 'Run id')}.history.jsonl"

    def _counts_for(self, run_id: str) -> _PersistedHistoryCounts:
        cached = self._persisted.get(run_id)
        if cached is not None:
            return cached
        target = self._record_path(run_id)
        if not target.is_file():
            return _PersistedHistoryCounts()
        snapshot = RunRecord.model_validate_json(read_text_retrying(target))
        if snapshot.history_integrity_hash is None:
            return _PersistedHistoryCounts()
        _history, counts, history_hash = _replay_history(
            self._history_path(run_id), entry_limit=snapshot.history_entry_count
        )
        if (
            counts.entry_count != snapshot.history_entry_count
            or history_hash != snapshot.history_integrity_hash
        ):
            raise ValueError(f'Run "{run_id}" history failed integrity verification.')
        return counts

    def _discard_uncommitted_history(
        self, run_id: str, path: Path, counts: _PersistedHistoryCounts
    ) -> None:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size == counts.byte_length:
            return
        if size < counts.byte_length:
            raise ValueError(
                f'Run "{run_id}" history file holds {size} bytes but {counts.byte_length} bytes '
                "are committed; the history was truncated outside the store."
            )
        # The snapshot is the commit record. History beyond its named prefix was
        # not published and must not be replayed or appended to on retry.
        with path.open("r+b") as handle:
            handle.truncate(counts.byte_length)
            handle.flush()
            os.fsync(handle.fileno())


@dataclass
class _PersistedHistoryCounts:
    """How much of each growing graph collection is already in the history log."""

    entry_count: int = 0
    events: int = 0
    route_decisions: int = 0
    conflicts: int = 0
    discovery_keys: set[str] = field(default_factory=set)
    value_keys: set[str] = field(default_factory=set)
    lateral_dependency_counts: dict[str, int] = field(default_factory=dict)
    byte_length: int = 0
    digest: Any = field(default_factory=hashlib.sha256)


def _emptied_history(graph: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``graph`` with every growing collection cleared.

    Copies only the top level and ``shared_state``; unrelated nested values
    (nodes, edges, statuses, results, substrate) are shared by reference rather
    than deep-copied, since this runs on every save and a full deep copy would
    defeat the point of no longer rewriting the full growing state.
    """

    result = dict(graph)
    shared_state = dict(result.get("shared_state", {}))
    result["shared_state"] = shared_state
    result["events"] = []
    shared_state["route_decisions"] = []
    shared_state["conflicts"] = []
    shared_state["discoveries"] = {}
    shared_state["values"] = {}
    shared_state["lateral_dependencies"] = {}
    return result


def _history_shrunk(graph: dict[str, Any], counts: _PersistedHistoryCounts) -> bool:
    shared_state = graph.get("shared_state", {})
    if len(graph.get("events", [])) < counts.events:
        return True
    if len(shared_state.get("route_decisions", [])) < counts.route_decisions:
        return True
    if len(shared_state.get("conflicts", [])) < counts.conflicts:
        return True
    if not counts.discovery_keys.issubset(shared_state.get("discoveries", {})):
        return True
    if not counts.value_keys.issubset(shared_state.get("values", {})):
        return True
    lateral_dependencies = shared_state.get("lateral_dependencies", {})
    return any(
        len(lateral_dependencies.get(key, [])) < already
        for key, already in counts.lateral_dependency_counts.items()
    )


def _diff_history(
    graph: dict[str, Any], counts: _PersistedHistoryCounts
) -> tuple[list[dict[str, Any]], _PersistedHistoryCounts]:
    """Return history entries new since ``counts``, and the resulting counts."""

    shared_state = graph.get("shared_state", {})
    entries: list[dict[str, Any]] = []
    updated = _PersistedHistoryCounts(
        entry_count=counts.entry_count,
        events=counts.events,
        route_decisions=counts.route_decisions,
        conflicts=counts.conflicts,
        discovery_keys=set(counts.discovery_keys),
        value_keys=set(counts.value_keys),
        lateral_dependency_counts=dict(counts.lateral_dependency_counts),
        byte_length=counts.byte_length,
        digest=counts.digest.copy(),
    )

    events = graph.get("events", [])
    entries.extend({"kind": "event", "value": item} for item in events[counts.events :])
    updated.events = len(events)

    route_decisions = shared_state.get("route_decisions", [])
    entries.extend(
        {"kind": "route_decision", "value": item}
        for item in route_decisions[counts.route_decisions :]
    )
    updated.route_decisions = len(route_decisions)

    conflicts = shared_state.get("conflicts", [])
    entries.extend({"kind": "conflict", "value": item} for item in conflicts[counts.conflicts :])
    updated.conflicts = len(conflicts)

    for key, value in shared_state.get("discoveries", {}).items():
        if key not in counts.discovery_keys:
            entries.append({"kind": "discovery", "key": key, "value": value})
            updated.discovery_keys.add(key)

    for key, value in shared_state.get("values", {}).items():
        if key not in counts.value_keys:
            entries.append({"kind": "value", "key": key, "value": value})
            updated.value_keys.add(key)

    for key, items in shared_state.get("lateral_dependencies", {}).items():
        already = counts.lateral_dependency_counts.get(key, 0)
        entries.extend(
            {"kind": "lateral_dependency", "key": key, "value": item} for item in items[already:]
        )
        updated.lateral_dependency_counts[key] = len(items)

    updated.entry_count += len(entries)
    return entries, updated


def _replay_history(
    path: Path, *, entry_limit: int | None = None
) -> tuple[dict[str, Any], _PersistedHistoryCounts, str]:
    """Reconstruct one bounded durable graph-history prefix."""

    events: list[Any] = []
    route_decisions: list[Any] = []
    conflicts: list[Any] = []
    discoveries: dict[str, Any] = {}
    values: dict[str, Any] = {}
    lateral_dependencies: dict[str, list[Any]] = {}
    entries, end_offset = _read_history_entries(path, entry_limit=entry_limit)
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(_encode_entry(entry))
        kind = entry["kind"]
        if kind == "event":
            events.append(entry["value"])
        elif kind == "route_decision":
            route_decisions.append(entry["value"])
        elif kind == "conflict":
            conflicts.append(entry["value"])
        elif kind == "discovery":
            discoveries[entry["key"]] = entry["value"]
        elif kind == "value":
            values[entry["key"]] = entry["value"]
        elif kind == "lateral_dependency":
            lateral_dependencies.setdefault(entry["key"], []).append(entry["value"])
        else:
            raise ValueError(f'Unknown run history entry kind "{kind}".')
    history = {
        "events": events,
        "route_decisions": route_decisions,
        "conflicts": conflicts,
        "discoveries": discoveries,
        "values": values,
        "lateral_dependencies": lateral_dependencies,
    }
    counts = _PersistedHistoryCounts(
        entry_count=len(entries),
        events=len(events),
        route_decisions=len(route_decisions),
        conflicts=len(conflicts),
        discovery_keys=set(discoveries),
        value_keys=set(values),
        lateral_dependency_counts={key: len(items) for key, items in lateral_dependencies.items()},
        byte_length=end_offset,
        digest=digest,
    )
    return history, counts, digest.hexdigest()


def _encode_entry(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _append_history(
    path: Path, entries: list[dict[str, Any]], counts: _PersistedHistoryCounts
) -> None:
    blob = b"".join(_encode_entry(entry) for entry in entries)
    with path.open("ab") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    counts.digest.update(blob)
    counts.byte_length += len(blob)


def _read_history_entries(
    path: Path, *, entry_limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0
    data = path.read_bytes()
    entries: list[dict[str, Any]] = []
    position = 0
    end_offset = 0
    while position < len(data):
        newline = data.find(b"\n", position)
        line_end = len(data) if newline == -1 else newline + 1
        line = data[position:line_end]
        if line.strip():
            if entry_limit is not None and len(entries) >= entry_limit:
                break
            try:
                entries.append(json.loads(line))
            except ValueError as error:
                raise ValueError(
                    f'Run history "{path.name}" entry {len(entries) + 1} is not valid JSON: '
                    f"{type(error).__name__}: {error}"
                ) from error
        position = line_end
        end_offset = position
    return entries, end_offset


def _write_json_atomic_candidate(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _verify_run_record(record: RunRecord) -> None:
    expected = _hash_run(
        record.run_id,
        record.plan_id,
        record.graph,
        record.plan_validation,
        record.cancelled,
    )
    if record.run_hash != expected:
        raise ValueError(f'Run "{record.run_id}" failed integrity verification.')


def _merge_history(snapshot_graph: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    """Recombine a history-emptied snapshot with its replayed growing collections."""

    graph = dict(snapshot_graph)
    shared_state = dict(graph.get("shared_state", {}))
    graph["shared_state"] = shared_state
    graph["events"] = history["events"]
    shared_state["route_decisions"] = history["route_decisions"]
    shared_state["conflicts"] = history["conflicts"]
    shared_state["discoveries"] = history["discoveries"]
    shared_state["values"] = history["values"]
    shared_state["lateral_dependencies"] = history["lateral_dependencies"]
    return graph
