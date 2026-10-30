# Copyright (c) 2026 David Michael Indraputra

"""A read-only inventory of every persistent resource class under a run root.

Each resource is classified by who owns it, what references it and what a retention policy
would do with it. Only telemetry, audit transcripts and tool-result journals of finished runs
are removed by `RunRetention`; every other class is accounted for and protected, with the rule
that protects it, so an operator can see what the run root holds before any broader cleanup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.hashing import sha256_hex
from ..foundations.identifiers import file_safe_name
from ..memory.context_projection import FileToolResultJournal
from ..observability.audit_log import AuditTranscriptStore
from ..observability.telemetry_store import TelemetryStore
from .retention import (
    RESERVED_STREAM_IDS,
    RETENTION_DIRECTORY,
    TERMINAL_EVENT_TYPE,
    RetentionPolicy,
    RetentionProblem,
    RunRetention,
    read_tombstones,
)

INVENTORY_SCHEMA = "agent-sdk-retention-inventory-v1"
DEFAULT_MAX_ITEMS = 1_000
_TERMINAL_CONTROLLER_PHASES = frozenset({"completed", "cancelled"})
_TERMINAL_ORCHESTRATION_STATUSES = frozenset({"executed", "failed", "cancelled"})
_TERMINAL_NODE_STATUSES = frozenset({"completed", "failed", "cancelled"})
_RUN_SUFFIXES = (".approvals.json", ".history.jsonl", ".json")
_CONTROLLER_SUFFIXES = (".events.jsonl", ".json")
_AUDIT_SUFFIXES = (".transcript.md", ".jsonl")


class ResourceClass(StrEnum):
    TELEMETRY = "telemetry"
    AUDIT_TRANSCRIPT = "audit-transcript"
    TOOL_RESULTS = "tool-results"
    UNSCOPED_TOOL_RESULTS = "unscoped-tool-results"
    GRAPH_RUN = "graph-run"
    CONTROLLER = "controller"
    ORCHESTRATION = "orchestration"
    PROJECT_STATE = "project-state"
    WORKSPACE = "workspace"
    EXPORTED_REPORT = "exported-report"
    SEMANTIC_MEMORY = "semantic-memory"
    EPISODE_ARCHIVE = "episode-archive"
    ARTIFACT_MANIFEST = "artifact-manifest"
    SPECIFICATION = "specification"
    RETENTION_EVIDENCE = "retention-evidence"
    UNCLASSIFIED = "unclassified"


class Ownership(StrEnum):
    RUN_OWNED = "run-owned"
    SHARED = "shared"
    EVIDENCE = "evidence"
    UNOWNED = "unowned"


class Disposition(StrEnum):
    PRUNABLE = "prunable"
    RECLAIMABLE = "reclaimable"
    PROTECTED = "protected"


class InventoryItem(StrictModel):
    resource_class: ResourceClass
    resource_id: str
    path: str
    bytes: int = Field(ge=0)
    files: int = Field(ge=0)
    ownership: Ownership
    owner: str | None = None
    referenced_by: list[str] = Field(default_factory=list)
    disposition: Disposition
    reason: str


class ClassSummary(StrictModel):
    resource_class: ResourceClass
    items: int = Field(ge=0)
    bytes: int = Field(ge=0)
    prunable: int = Field(default=0, ge=0)
    reclaimable: int = Field(default=0, ge=0)
    protected: int = Field(default=0, ge=0)
    prunable_bytes: int = Field(default=0, ge=0)
    reclaimable_bytes: int = Field(default=0, ge=0)


class RunRootInventory(StrictModel):
    schema_version: str = INVENTORY_SCHEMA
    run_root: str
    evaluated_at: str
    policy: RetentionPolicy
    classes: list[ClassSummary] = Field(default_factory=list)
    items: list[InventoryItem] = Field(default_factory=list)
    items_omitted: int = Field(default=0, ge=0)
    total_items: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    prunable_bytes: int = Field(default=0, ge=0)
    reclaimable_bytes: int = Field(default=0, ge=0)
    protected_bytes: int = Field(default=0, ge=0)
    telemetry_database_bytes: int = Field(default=0, ge=0)
    tombstones: int = Field(default=0, ge=0)
    problems: list[RetentionProblem] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems

    def of_class(self, resource_class: ResourceClass) -> ClassSummary | None:
        return next((item for item in self.classes if item.resource_class is resource_class), None)


@dataclass
class _Extent:
    bytes: int = 0
    files: int = 0
    newest: float = 0.0

    def add(self, other: _Extent) -> None:
        self.bytes += other.bytes
        self.files += other.files
        self.newest = max(self.newest, other.newest)


def _extent(path: Path) -> _Extent:
    total = _Extent()
    try:
        status = path.lstat()
    except OSError:
        return total
    if not os.path.isdir(path) or os.path.islink(path):
        return _Extent(status.st_size, 1, status.st_mtime)
    for directory, _names, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                item = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            total.bytes += item.st_size
            total.files += 1
            total.newest = max(total.newest, item.st_mtime)
    return total


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _stem(name: str, suffixes: tuple[str, ...]) -> str | None:
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def _moment(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


@dataclass
class _RunVerdict:
    run_id: str
    prunable: bool
    reason: str
    last_event_at: datetime | None
    terminal: bool


@dataclass
class _Builder:
    root: Path
    policy: RetentionPolicy
    moment: datetime
    cutoff: datetime
    items: list[InventoryItem] = field(default_factory=list)
    problems: list[RetentionProblem] = field(default_factory=list)
    runs: dict[str, _RunVerdict] = field(default_factory=dict)
    tombstoned: set[str] = field(default_factory=set)
    claimed: set[str] = field(default_factory=set)

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def claim(self, path: Path) -> None:
        self.claimed.add(self.relative(path))

    def add(
        self,
        resource_class: ResourceClass,
        resource_id: str,
        path: Path,
        extent: _Extent,
        ownership: Ownership,
        disposition: Disposition,
        reason: str,
        *,
        owner: str | None = None,
        referenced_by: list[str] | None = None,
    ) -> None:
        self.items.append(
            InventoryItem(
                resource_class=resource_class,
                resource_id=resource_id,
                path=self.relative(path),
                bytes=extent.bytes,
                files=extent.files,
                ownership=ownership,
                owner=owner,
                referenced_by=sorted(referenced_by or []),
                disposition=disposition,
                reason=reason,
            )
        )

    def old_enough(self, newest: float) -> bool:
        return newest == 0.0 or _moment(newest) <= self.cutoff


def inventory_run_root(
    run_root: Path,
    policy: RetentionPolicy,
    *,
    now: datetime | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> RunRootInventory:
    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    if max_items < 0:
        raise ValueError("max_items must not be negative.")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    root = run_root.resolve()
    builder = _Builder(
        root=root,
        policy=policy,
        moment=moment,
        cutoff=moment - timedelta(seconds=policy.older_than_seconds),
    )
    database_bytes = _telemetry_items(builder)
    _audit_items(builder)
    _journal_items(builder)
    reports = _report_items(builder)
    controllers, orchestrations = _record_tables(builder)
    _orchestration_items(builder, orchestrations)
    controller_dispositions = _controller_items(builder, controllers, orchestrations)
    _graph_run_items(builder, controllers, orchestrations, controller_dispositions)
    _project_state_items(builder, controllers)
    _workspace_items(builder)
    _memory_items(builder)
    _artifact_items(builder)
    _specification_items(builder)
    _evidence_items(builder)
    _unclassified_items(builder)
    return _assemble(builder, root, database_bytes, reports, max_items)


def _assemble(
    builder: _Builder, root: Path, database_bytes: int, reports: int, max_items: int
) -> RunRootInventory:
    summaries: dict[ResourceClass, ClassSummary] = {}
    for item in builder.items:
        summary = summaries.setdefault(
            item.resource_class,
            ClassSummary(resource_class=item.resource_class, items=0, bytes=0),
        )
        summary.items += 1
        summary.bytes += item.bytes
        if item.disposition is Disposition.PRUNABLE:
            summary.prunable += 1
            summary.prunable_bytes += item.bytes
        elif item.disposition is Disposition.RECLAIMABLE:
            summary.reclaimable += 1
            summary.reclaimable_bytes += item.bytes
        else:
            summary.protected += 1
    ordered = sorted(
        builder.items, key=lambda item: (item.resource_class.value, -item.bytes, item.resource_id)
    )
    listed = ordered[:max_items]
    classes = [summaries[key] for key in sorted(summaries, key=lambda key: key.value)]
    try:
        tombstones = len(read_tombstones(root))
    except Exception:
        tombstones = 0
    return RunRootInventory(
        run_root=str(root),
        evaluated_at=builder.moment.isoformat(),
        policy=builder.policy,
        classes=classes,
        items=listed,
        items_omitted=len(ordered) - len(listed),
        total_items=len(ordered),
        total_bytes=sum(item.bytes for item in ordered),
        prunable_bytes=sum(s.prunable_bytes for s in classes),
        reclaimable_bytes=sum(s.reclaimable_bytes for s in classes),
        protected_bytes=sum(
            item.bytes for item in ordered if item.disposition is Disposition.PROTECTED
        ),
        telemetry_database_bytes=database_bytes,
        tombstones=tombstones,
        problems=builder.problems,
    )


def _telemetry_items(builder: _Builder) -> int:
    database = builder.root / ".agent-telemetry" / "telemetry.sqlite3"
    directory = builder.root / ".agent-telemetry"
    if not database.is_file():
        return 0
    builder.claim(directory)
    database_bytes = sum(
        path.stat().st_size
        for path in (database, database.with_name(database.name + "-wal"))
        if path.is_file()
    )
    telemetry = TelemetryStore(builder.root, read_only=True)
    audit = AuditTranscriptStore(builder.root, read_only=True)
    journal = FileToolResultJournal(builder.root, read_only=True)
    try:
        retention = RunRetention(
            builder.root, telemetry=telemetry, audit_logs=audit, result_journal=journal
        )
        report = retention.prune(builder.policy, dry_run=True, now=builder.moment)
        pruned = {run.run_id for run in report.runs}
        problems = {problem.run_id: problem.reason for problem in report.problems}
        builder.problems.extend(report.problems)
        for activity in telemetry.run_activity(terminal_event_types={TERMINAL_EVENT_TYPE}):
            run_id = activity.run_id
            last = datetime.fromisoformat(activity.last_event_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if run_id in pruned:
                verdict = _RunVerdict(
                    run_id, True, "finished and idle beyond the policy", last, True
                )
            elif run_id in RESERVED_STREAM_IDS:
                verdict = _RunVerdict(
                    run_id,
                    False,
                    "a stream the runtime itself writes (protected_run_ids)",
                    last,
                    False,
                )
            elif run_id in problems:
                verdict = _RunVerdict(run_id, False, problems[run_id], last, activity.terminal)
            else:
                verdict = _RunVerdict(
                    run_id, False, _protection_reason(activity, builder), last, activity.terminal
                )
            builder.runs[run_id] = verdict
            footprint = telemetry.footprint(run_id)
            builder.add(
                ResourceClass.TELEMETRY,
                run_id,
                database,
                _Extent(footprint.bytes, 1),
                Ownership.RUN_OWNED,
                Disposition.PRUNABLE if verdict.prunable else Disposition.PROTECTED,
                verdict.reason,
                owner=run_id,
            )
    finally:
        audit.close()
        telemetry.close()
    try:
        builder.tombstoned = {item.run_id for item in read_tombstones(builder.root)}
    except Exception as error:
        builder.problems.append(
            RetentionProblem(run_id="(tombstone log)", reason=f"{type(error).__name__}: {error}")
        )
    return database_bytes


def _protection_reason(activity: Any, builder: _Builder) -> str:
    policy = builder.policy
    if not activity.terminal and not policy.include_unfinished:
        return f'no "{TERMINAL_EVENT_TYPE}" event, so the run may still be live'
    last = datetime.fromisoformat(activity.last_event_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last > builder.cutoff:
        return f"last event within the past {policy.older_than_seconds:g}s (older_than_seconds)"
    return (
        "retained by keep_most_recent, max_runs, or a prune that was interrupted "
        "(see the retention report)"
    )


def _directory(builder: _Builder, name: str) -> Path | None:
    path = builder.root / name
    if not path.is_dir():
        return None
    builder.claim(path)
    return path


def _audit_items(builder: _Builder) -> None:
    directory = _directory(builder, ".agent-audit-logs")
    if directory is None:
        return
    safe_to_run = {file_safe_name(run_id): run_id for run_id in builder.runs}
    grouped: dict[str, _Extent] = {}
    for entry in sorted(directory.iterdir()):
        stem = _stem(entry.name, _AUDIT_SUFFIXES) or entry.name
        grouped.setdefault(stem, _Extent()).add(_extent(entry))
    for stem, extent in grouped.items():
        run_id = safe_to_run.get(stem)
        path = directory / stem
        if run_id is None:
            builder.add(
                ResourceClass.AUDIT_TRANSCRIPT,
                stem,
                path,
                extent,
                Ownership.UNOWNED,
                Disposition.PROTECTED,
                "no telemetry run owns this transcript, so retention cannot judge it finished",
            )
            continue
        verdict = builder.runs[run_id]
        builder.add(
            ResourceClass.AUDIT_TRANSCRIPT,
            run_id,
            path,
            extent,
            Ownership.RUN_OWNED,
            Disposition.PRUNABLE if verdict.prunable else Disposition.PROTECTED,
            verdict.reason,
            owner=run_id,
        )


def _journal_items(builder: _Builder) -> None:
    directory = _directory(builder, ".agent-tool-results")
    if directory is None:
        return
    index_directory = directory / ".runs"
    safe_to_run = {file_safe_name(run_id): run_id for run_id in builder.runs}
    scoped: dict[str, str] = {}
    run_indexes: dict[str, list[str]] = {}
    if index_directory.is_dir():
        for index in sorted(index_directory.glob("*.handles")):
            safe = index.name[: -len(".handles")]
            try:
                handles = [line.strip() for line in index.read_text("utf-8").splitlines()]
            except OSError:
                continue
            for handle in handles:
                if handle:
                    scoped[handle] = safe
                    run_indexes.setdefault(safe, []).append(handle)
    per_run: dict[str, _Extent] = {}
    unscoped = _Extent()
    other = _Extent()
    for entry in directory.iterdir():
        if entry.name == ".runs":
            other.add(_extent(entry))
            continue
        handle = entry.name[: -len(".json")] if entry.name.endswith(".json") else None
        if handle is None or not handle.startswith("result-"):
            other.add(_extent(entry))
            continue
        owner = scoped.get(handle)
        if owner is None:
            unscoped.add(_extent(entry))
        else:
            per_run.setdefault(owner, _Extent()).add(_extent(entry))
    for safe, extent in sorted(per_run.items()):
        run_id = safe_to_run.get(safe)
        if run_id is None:
            builder.add(
                ResourceClass.TOOL_RESULTS,
                safe,
                directory,
                extent,
                Ownership.UNOWNED,
                Disposition.PROTECTED,
                "no telemetry run owns these tool results, so retention cannot judge them finished",
            )
            continue
        verdict = builder.runs[run_id]
        builder.add(
            ResourceClass.TOOL_RESULTS,
            run_id,
            directory,
            extent,
            Ownership.RUN_OWNED,
            Disposition.PRUNABLE if verdict.prunable else Disposition.PROTECTED,
            verdict.reason,
            owner=run_id,
        )
    if unscoped.files:
        builder.add(
            ResourceClass.UNSCOPED_TOOL_RESULTS,
            "unscoped",
            directory,
            unscoped,
            Ownership.UNOWNED,
            Disposition.PROTECTED,
            "recorded without a run scope, so nothing proves that no live task or retained "
            "evidence still cites these handles",
        )
    if other.files:
        builder.add(
            ResourceClass.TOOL_RESULTS,
            "journal-index",
            directory,
            other,
            Ownership.SHARED,
            Disposition.PROTECTED,
            "per-run handle indexes and the handle counter are shared by every run",
        )


def _report_items(builder: _Builder) -> int:
    directory = builder.root / ".agent-telemetry" / "reports"
    if not directory.is_dir():
        return 0
    count = 0
    for entry in sorted(directory.iterdir()):
        count += 1
        builder.add(
            ResourceClass.EXPORTED_REPORT,
            entry.name,
            entry,
            _extent(entry),
            Ownership.EVIDENCE,
            Disposition.PROTECTED,
            "an exported run report is the copy of a run's evidence that outlives the run",
        )
    return count


@dataclass
class _Record:
    identifier: str
    path: Path
    extent: _Extent
    data: dict[str, Any] | None


def _records(directory: Path, suffixes: tuple[str, ...]) -> dict[str, _Record]:
    found: dict[str, _Record] = {}
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith(".") or entry.is_dir():
            continue
        stem = _stem(entry.name, suffixes)
        if stem is None:
            continue
        record = found.setdefault(stem, _Record(stem, directory / stem, _Extent(), None))
        record.extent.add(_extent(entry))
        if entry.name == f"{stem}.json":
            record.data = _read_object(entry)
    return found


def _record_tables(
    builder: _Builder,
) -> tuple[dict[str, _Record], dict[str, _Record]]:
    controllers: dict[str, _Record] = {}
    orchestrations: dict[str, _Record] = {}
    directory = _directory(builder, ".agent-controllers")
    if directory is not None:
        controllers = _records(directory, _CONTROLLER_SUFFIXES)
    directory = _directory(builder, ".agent-orchestrations")
    if directory is not None:
        orchestrations = _records(directory, (".json",))
    return controllers, orchestrations


def _orchestration_items(builder: _Builder, orchestrations: dict[str, _Record]) -> None:
    for identifier, record in orchestrations.items():
        status = (record.data or {}).get("status")
        if record.data is None:
            disposition, reason = Disposition.PROTECTED, "record unreadable: not evaluated"
        elif status not in _TERMINAL_ORCHESTRATION_STATUSES:
            disposition = Disposition.PROTECTED
            reason = f'its status is "{status}", which is not final'
        elif not builder.old_enough(record.extent.newest):
            disposition = Disposition.PROTECTED
            reason = f"last written within the past {builder.policy.older_than_seconds:g}s"
        else:
            disposition = Disposition.RECLAIMABLE
            reason = (
                f'final status "{status}" and idle beyond the policy; no SDK operation removes it'
            )
        builder.add(
            ResourceClass.ORCHESTRATION,
            identifier,
            record.path,
            record.extent,
            Ownership.SHARED,
            disposition,
            reason,
        )
    policies = builder.root / ".agent-orchestrations" / "policies"
    if policies.is_dir():
        builder.add(
            ResourceClass.ORCHESTRATION,
            "policies",
            policies,
            _extent(policies),
            Ownership.SHARED,
            Disposition.PROTECTED,
            "orchestration policies are shared by every orchestration that names them",
        )


def _controller_items(
    builder: _Builder,
    controllers: dict[str, _Record],
    orchestrations: dict[str, _Record],
) -> dict[str, Disposition]:
    referenced_by: dict[str, list[tuple[str, bool]]] = {}
    for identifier, record in orchestrations.items():
        data = record.data or {}
        controller_id = data.get("controller_id")
        if isinstance(controller_id, str):
            final = data.get("status") in _TERMINAL_ORCHESTRATION_STATUSES
            referenced_by.setdefault(controller_id, []).append((identifier, final))
    outcome: dict[str, Disposition] = {}
    for identifier, record in controllers.items():
        data = record.data
        users = referenced_by.get(identifier, [])
        live = [name for name, final in users if not final]
        if data is None:
            disposition, reason = Disposition.PROTECTED, "record unreadable: not evaluated"
        elif data.get("phase") not in _TERMINAL_CONTROLLER_PHASES:
            disposition = Disposition.PROTECTED
            reason = f'its phase is "{data.get("phase")}", which is not final'
        elif live:
            disposition = Disposition.PROTECTED
            reason = f"referenced by the unfinished orchestration {live[0]}"
        elif not builder.old_enough(record.extent.newest):
            disposition = Disposition.PROTECTED
            reason = f"last written within the past {builder.policy.older_than_seconds:g}s"
        else:
            disposition = Disposition.RECLAIMABLE
            reason = "final phase and idle beyond the policy; no SDK operation removes it"
        outcome[identifier] = disposition
        builder.add(
            ResourceClass.CONTROLLER,
            identifier,
            record.path,
            record.extent,
            Ownership.SHARED,
            disposition,
            reason,
            referenced_by=[f"orchestration:{name}" for name, _ in users],
        )
    return outcome


def _graph_run_items(
    builder: _Builder,
    controllers: dict[str, _Record],
    orchestrations: dict[str, _Record],
    controller_dispositions: dict[str, Disposition],
) -> None:
    directory = _directory(builder, ".agent-runs")
    if directory is None:
        return
    users: dict[str, list[str]] = {}
    blockers: dict[str, str] = {}
    for identifier, record in controllers.items():
        run_id = (record.data or {}).get("run_id")
        if isinstance(run_id, str):
            users.setdefault(run_id, []).append(f"controller:{identifier}")
            if controller_dispositions.get(identifier) is not Disposition.RECLAIMABLE:
                blockers.setdefault(run_id, f"controller {identifier} is retained")
    for identifier, record in orchestrations.items():
        run_id = (record.data or {}).get("graph_run_id")
        if isinstance(run_id, str):
            users.setdefault(run_id, []).append(f"orchestration:{identifier}")
            status = (record.data or {}).get("status")
            if status not in _TERMINAL_ORCHESTRATION_STATUSES:
                blockers.setdefault(run_id, f"orchestration {identifier} is not final")
    for identifier, record in _records(directory, _RUN_SUFFIXES).items():
        data = record.data
        statuses = ((data or {}).get("graph") or {}).get("statuses")
        finished = bool((data or {}).get("cancelled")) or (
            isinstance(statuses, dict)
            and bool(statuses)
            and all(value in _TERMINAL_NODE_STATUSES for value in statuses.values())
        )
        if data is None:
            disposition, reason = Disposition.PROTECTED, "record unreadable: not evaluated"
        elif not finished:
            disposition = Disposition.PROTECTED
            reason = "some of its nodes are not in a final state"
        elif identifier in blockers:
            disposition = Disposition.PROTECTED
            reason = f"referenced by a retained record: {blockers[identifier]}"
        elif not builder.old_enough(record.extent.newest):
            disposition = Disposition.PROTECTED
            reason = f"last written within the past {builder.policy.older_than_seconds:g}s"
        else:
            disposition = Disposition.RECLAIMABLE
            reason = (
                "every node is final and nothing retained references it; no SDK operation "
                "removes it"
            )
        builder.add(
            ResourceClass.GRAPH_RUN,
            identifier,
            record.path,
            record.extent,
            Ownership.RUN_OWNED,
            disposition,
            reason,
            owner=identifier,
            referenced_by=users.get(identifier, []),
        )


def _project_state_items(builder: _Builder, controllers: dict[str, _Record]) -> None:
    directory = _directory(builder, ".agent-project-state")
    if directory is None:
        return
    by_hash: dict[str, list[str]] = {}
    for identifier, record in controllers.items():
        project = (record.data or {}).get("project_state_id")
        if isinstance(project, str):
            by_hash.setdefault(sha256_hex(project), []).append(f"controller:{identifier}")
    grouped: dict[str, _Extent] = {}
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith("."):
            continue
        stem = entry.name[: -len(".json")] if entry.name.endswith(".json") else entry.name
        grouped.setdefault(stem, _Extent()).add(_extent(entry))
    for stem, extent in grouped.items():
        references = by_hash.get(stem, [])
        reason = (
            f"working memory shared by {len(references)} controller(s); retention never "
            "reclaims project state"
            if references
            else "working memory owned by the host application; retention never reclaims "
            "project state"
        )
        builder.add(
            ResourceClass.PROJECT_STATE,
            stem,
            directory / stem,
            extent,
            Ownership.SHARED,
            Disposition.PROTECTED,
            reason,
            referenced_by=references,
        )


def _workspace_items(builder: _Builder) -> None:
    directory = _directory(builder, "workspaces")
    if directory is None:
        return
    safe_to_run = {file_safe_name(run_id): run_id for run_id in builder.runs}
    tombstoned = {file_safe_name(run_id): run_id for run_id in builder.tombstoned}
    for entry in sorted(directory.iterdir()):
        extent = _extent(entry)
        owner = safe_to_run.get(entry.name) or tombstoned.get(entry.name)
        if owner is None:
            builder.add(
                ResourceClass.WORKSPACE,
                entry.name,
                entry,
                extent,
                Ownership.UNOWNED,
                Disposition.PROTECTED,
                "no run record or tombstone names this workspace, so its owner is unknown",
            )
            continue
        verdict = builder.runs.get(owner)
        if verdict is None:
            disposition = (
                Disposition.RECLAIMABLE
                if builder.old_enough(extent.newest)
                else Disposition.PROTECTED
            )
            reason = (
                "its run was pruned and a tombstone records it; the workspace is the agent's "
                "work product and no SDK operation removes it"
                if disposition is Disposition.RECLAIMABLE
                else "written within the retention window"
            )
        elif verdict.prunable and builder.old_enough(extent.newest):
            disposition = Disposition.RECLAIMABLE
            reason = "its run is finished and idle beyond the policy; no SDK operation removes it"
        else:
            disposition = Disposition.PROTECTED
            reason = (
                f"its run is retained: {verdict.reason}"
                if not verdict.prunable
                else "written after the run's last event or within the retention window"
            )
        builder.add(
            ResourceClass.WORKSPACE,
            entry.name,
            entry,
            extent,
            Ownership.RUN_OWNED,
            disposition,
            reason,
            owner=owner,
        )


def _memory_items(builder: _Builder) -> None:
    directory = _directory(builder, ".agent-memory")
    if directory is None:
        return
    archive = directory / "archive"
    referenced = _referenced_archive_ids(directory)
    for entry in sorted(directory.iterdir()):
        if entry == archive:
            continue
        builder.add(
            ResourceClass.SEMANTIC_MEMORY,
            entry.name,
            entry,
            _extent(entry),
            Ownership.SHARED,
            Disposition.PROTECTED,
            "the checkpoint and snapshot generations a restart restores from",
        )
    if not archive.is_dir():
        return
    summaries = archive / "summaries"
    for entry in sorted(archive.iterdir()):
        if entry == summaries:
            continue
        _archive_item(builder, entry, referenced)
    if summaries.is_dir():
        for entry in sorted(summaries.iterdir()):
            _archive_item(builder, entry, referenced)


def _archive_item(builder: _Builder, entry: Path, referenced: set[str] | None) -> None:
    identifier = entry.stem
    extent = _extent(entry)
    if referenced is None:
        disposition = Disposition.PROTECTED
        reason = "no valid snapshot to compare against, so every archive entry is kept"
    elif identifier in referenced:
        disposition = Disposition.PROTECTED
        reason = "referenced by the newest memory snapshot (episode payload or summary source)"
    elif builder.old_enough(extent.newest):
        disposition = Disposition.RECLAIMABLE
        reason = (
            "no retained snapshot references it; content-addressed archive entries are "
            "historical evidence and no SDK operation removes them"
        )
    else:
        disposition = Disposition.PROTECTED
        reason = f"written within the past {builder.policy.older_than_seconds:g}s"
    builder.add(
        ResourceClass.EPISODE_ARCHIVE,
        identifier,
        entry,
        extent,
        Ownership.SHARED,
        disposition,
        reason,
    )


def _referenced_archive_ids(memory_root: Path) -> set[str] | None:
    snapshots = memory_root / "snapshots"
    if not snapshots.is_dir():
        return None
    newest: dict[str, Any] | None = None
    for entry in sorted(snapshots.iterdir(), reverse=True):
        loaded = _read_object(entry)
        if loaded is not None and "records" in loaded:
            newest = loaded
            break
    if newest is None:
        return None
    identifiers: set[str] = set()
    for record in newest.get("records", []):
        if isinstance(record, dict) and isinstance(record.get("archive_id"), str):
            identifiers.add(record["archive_id"])
    for item in newest.get("evicted", []):
        if isinstance(item, dict) and isinstance(item.get("archive_id"), str):
            identifiers.add(item["archive_id"])
    for ref in newest.get("summary_index", []):
        if isinstance(ref, dict) and isinstance(ref.get("archive_ref"), str):
            identifiers.add(ref["archive_ref"])
    for summary in newest.get("summaries", []):
        if isinstance(summary, dict):
            for archive_id in summary.get("archive_ids", []) or []:
                if isinstance(archive_id, str):
                    identifiers.add(archive_id)
    return identifiers


def _artifact_items(builder: _Builder) -> None:
    directory = _directory(builder, ".agent-artifacts")
    if directory is None:
        return
    builder.add(
        ResourceClass.ARTIFACT_MANIFEST,
        ".agent-artifacts",
        directory,
        _extent(directory),
        Ownership.SHARED,
        Disposition.PROTECTED,
        "artifact manifests are referenced by project state and verification evidence",
    )


def _specification_items(builder: _Builder) -> None:
    directory = _directory(builder, "specifications")
    if directory is None:
        return
    builder.add(
        ResourceClass.SPECIFICATION,
        "specifications",
        directory,
        _extent(directory),
        Ownership.SHARED,
        Disposition.PROTECTED,
        "specification trees are inputs that every task and controller reads",
    )


def _evidence_items(builder: _Builder) -> None:
    directory = _directory(builder, RETENTION_DIRECTORY)
    if directory is None:
        return
    for entry in sorted(directory.iterdir()):
        builder.add(
            ResourceClass.RETENTION_EVIDENCE,
            entry.name,
            entry,
            _extent(entry),
            Ownership.EVIDENCE,
            Disposition.PROTECTED,
            "the record of what retention removed and the fences of prunes in progress",
        )


def _unclassified_items(builder: _Builder) -> None:
    for entry in sorted(builder.root.iterdir()):
        relative = builder.relative(entry)
        if relative in builder.claimed:
            continue
        builder.add(
            ResourceClass.UNCLASSIFIED,
            entry.name,
            entry,
            _extent(entry),
            Ownership.UNOWNED,
            Disposition.PROTECTED,
            "not a resource class the SDK writes; left untouched",
        )
