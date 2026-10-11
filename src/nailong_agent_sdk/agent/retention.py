# Copyright (c) 2026 David Michael Indraputra

"""Opt-in pruning of finished runs from a run root, leaving a hash-chained tombstone for each."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field

from ..foundations.atomic_io import exclusive_file_lock
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_hash
from ..memory.context_projection import FileToolResultJournal, JournalFootprint
from ..observability.audit_log import AuditFootprint, AuditTranscriptStore
from ..observability.telemetry_helpers import first_chain_break, utc_now
from ..observability.telemetry_models import (
    ChainBreak,
    TelemetryFootprint,
    TelemetryRunActivity,
)
from ..observability.telemetry_store import TelemetryStore

RETENTION_DIRECTORY = ".agent-retention"
TOMBSTONE_FILE = "tombstones.jsonl"
LOCK_FILE = "retention.lock"
TERMINAL_EVENT_TYPE = "agent.terminated"
MAX_OLDER_THAN_SECONDS = 3_153_600_000.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0


class RetentionPolicy(StrictModel):
    older_than_seconds: float = Field(gt=0, le=MAX_OLDER_THAN_SECONDS)
    keep_most_recent: int = Field(default=0, ge=0)
    max_runs: int | None = Field(default=None, ge=1)
    include_unfinished: bool = False
    export_reports: bool = False


class PrunedRun(StrictModel):
    run_id: str
    last_event_at: str
    events: int = Field(ge=0)
    metrics: int = Field(ge=0)
    audit_entries: int = Field(ge=0)
    journal_handles: int = Field(ge=0)
    bytes: int = Field(ge=0)
    report_path: str | None = None
    tombstone_sequence: int | None = None


class RetentionProblem(StrictModel):
    run_id: str
    reason: str


class RetentionReport(StrictModel):
    schema_version: str = "agent-sdk-retention-report-v1"
    dry_run: bool
    policy: RetentionPolicy
    evaluated_at: str
    runs_examined: int = Field(ge=0)
    runs: list[PrunedRun] = Field(default_factory=list)
    kept: dict[str, int] = Field(default_factory=dict)
    problems: list[RetentionProblem] = Field(default_factory=list)
    bytes: int = Field(default=0, ge=0)

    @property
    def clean(self) -> bool:
        return not self.problems


class RunTombstone(StrictModel):
    schema_version: str = "agent-sdk-run-tombstone-v1"
    sequence: int = Field(ge=1)
    previous_hash: str | None = None
    run_id: str
    pruned_at_utc: str
    last_event_at: str
    policy: RetentionPolicy
    telemetry: TelemetryFootprint
    audit: AuditFootprint
    journal: JournalFootprint
    report_path: str | None = None
    integrity_hash: str = ""


class TombstonedHandle(StrictModel):
    handle_id: str
    content_hash: str
    run_id: str
    tombstone_sequence: int = Field(ge=1)


def tombstone_path(run_root: Path) -> Path:
    return run_root.resolve() / RETENTION_DIRECTORY / TOMBSTONE_FILE


def tombstone_hash(tombstone: RunTombstone) -> str:
    return canonical_hash(
        tombstone.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
    )


def tombstone_chain_break(run_root: Path) -> ChainBreak | None:
    return _load(tombstone_path(run_root))[1]


def read_tombstones(run_root: Path) -> list[RunTombstone]:
    path = tombstone_path(run_root)
    entries, failure = _load(path)
    if failure is not None:
        raise _broken_log(path, failure)
    return entries


def tombstoned_handles(run_root: Path) -> dict[str, TombstonedHandle]:
    handles: dict[str, TombstonedHandle] = {}
    for tombstone in read_tombstones(run_root):
        for handle in tombstone.journal.handles:
            handles[handle.handle_id] = TombstonedHandle(
                handle_id=handle.handle_id,
                content_hash=handle.content_hash,
                run_id=tombstone.run_id,
                tombstone_sequence=tombstone.sequence,
            )
    return handles


def tombstone_for_run(run_root: Path, run_id: str) -> RunTombstone | None:
    matching = [item for item in read_tombstones(run_root) if item.run_id == run_id]
    return matching[-1] if matching else None


class RunRetention:
    def __init__(
        self,
        run_root: Path,
        *,
        telemetry: TelemetryStore,
        audit_logs: AuditTranscriptStore,
        result_journal: FileToolResultJournal,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be positive.")
        self._run_root = run_root.resolve()
        self._telemetry = telemetry
        self._audit_logs = audit_logs
        self._journal = result_journal
        self._lock_timeout_seconds = lock_timeout_seconds

    def prune(
        self,
        policy: RetentionPolicy,
        *,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> RetentionReport:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("now must be timezone-aware.")
        if dry_run:
            return self._pass(policy, moment, None)
        with exclusive_file_lock(
            self._run_root / RETENTION_DIRECTORY / LOCK_FILE,
            timeout_seconds=self._lock_timeout_seconds,
            timeout_code="RETENTION_LOCK_TIMEOUT",
        ):
            return self._pass(policy, moment, _TombstoneAppender(tombstone_path(self._run_root)))

    def _pass(
        self, policy: RetentionPolicy, moment: datetime, log: _TombstoneAppender | None
    ) -> RetentionReport:
        activity = self._telemetry.run_activity(terminal_event_types={TERMINAL_EVENT_TYPE})
        cutoff = moment - timedelta(seconds=policy.older_than_seconds)
        newest = activity[-policy.keep_most_recent :] if policy.keep_most_recent else []
        protected = {item.run_id for item in newest}
        kept: Counter[str] = Counter()
        runs: list[PrunedRun] = []
        problems: list[RetentionProblem] = []
        for item in activity:
            reason = _keep_reason(item, policy, cutoff, protected)
            if reason is None and policy.max_runs is not None and len(runs) >= policy.max_runs:
                reason = f"beyond the {policy.max_runs} runs one call prunes (max_runs)"
            if reason is not None:
                kept[reason] += 1
                continue
            broken = self._integrity_problem(item.run_id)
            if broken is not None:
                problems.append(RetentionProblem(run_id=item.run_id, reason=broken))
                continue
            try:
                runs.append(self._prune_one(item, policy, log))
            except AgentSdkError as error:
                problems.append(RetentionProblem(run_id=item.run_id, reason=str(error)))
        return RetentionReport(
            dry_run=log is None,
            policy=policy,
            evaluated_at=moment.isoformat(),
            runs_examined=len(activity),
            runs=runs,
            kept=dict(kept),
            problems=problems,
            bytes=sum(run.bytes for run in runs),
        )

    def _integrity_problem(self, run_id: str) -> str | None:
        failure = self._telemetry.chain_break(run_id)
        if failure is not None:
            return (
                f"its telemetry chain is broken at sequence {failure.sequence} "
                f"({failure.kind}): {failure.message} It was left untouched as evidence."
            )
        failure = self._audit_logs.chain_break(run_id)
        if failure is not None:
            return (
                f"its audit transcript chain is broken at sequence {failure.sequence} "
                f"({failure.kind}): {failure.message} It was left untouched as evidence."
            )
        return None

    def _prune_one(
        self, item: TelemetryRunActivity, policy: RetentionPolicy, log: _TombstoneAppender | None
    ) -> PrunedRun:
        run_id = item.run_id
        telemetry = self._telemetry.footprint(run_id)
        audit = self._audit_logs.footprint(run_id)
        journal = self._journal.footprint(run_id)
        summary = PrunedRun(
            run_id=run_id,
            last_event_at=item.last_event_at,
            events=telemetry.event_count,
            metrics=telemetry.metric_count,
            audit_entries=audit.entries,
            journal_handles=len(journal.handles),
            bytes=telemetry.bytes + audit.bytes + journal.bytes,
        )
        if log is None:
            return summary
        step = "confirming that it received no new event"
        tombstone: RunTombstone | None = None
        try:
            self._telemetry.prune_run(
                run_id, dry_run=True, expected_last_sequence=item.last_sequence
            )
            report_path = None
            if policy.export_reports:
                step = "exporting its run report"
                exported = self._telemetry.create_run_report(run_id)["report_path"]
                report_path = (Path(".agent-telemetry") / exported).as_posix()
            step = "writing its tombstone"
            tombstone = log.record(
                run_id,
                pruned_at_utc=utc_now(),
                last_event_at=item.last_event_at,
                policy=policy,
                telemetry=telemetry,
                audit=audit,
                journal=journal,
                report_path=report_path,
            )
            step = "removing its tool results"
            self._journal.prune_run(run_id)
            step = "removing its audit transcript"
            self._audit_logs.prune_run(run_id)
            step = "removing its telemetry"
            self._telemetry.prune_run(run_id, expected_last_sequence=item.last_sequence)
        except (AgentSdkError, OSError, sqlite3.Error) as error:
            raise _stopped(run_id, step, error, tombstone) from error
        return summary.model_copy(
            update={"report_path": report_path, "tombstone_sequence": tombstone.sequence}
        )


def prune_run_root(
    run_root: Path,
    policy: RetentionPolicy,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RetentionReport:
    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    database = run_root.resolve() / ".agent-telemetry" / "telemetry.sqlite3"
    if not database.is_file():
        raise ValueError(f'Run root "{run_root}" has no telemetry database at "{database}".')
    telemetry = TelemetryStore(run_root, read_only=dry_run)
    audit_logs = AuditTranscriptStore(run_root, read_only=dry_run)
    try:
        retention = RunRetention(
            run_root,
            telemetry=telemetry,
            audit_logs=audit_logs,
            result_journal=FileToolResultJournal(run_root, read_only=dry_run),
        )
        return retention.prune(policy, dry_run=dry_run, now=now)
    finally:
        audit_logs.close()
        telemetry.close()


def _keep_reason(
    item: TelemetryRunActivity,
    policy: RetentionPolicy,
    cutoff: datetime,
    protected: set[str],
) -> str | None:
    if item.run_id in protected:
        return f"among the {policy.keep_most_recent} most recently active runs (keep_most_recent)"
    if not item.terminal and not policy.include_unfinished:
        return (
            f'no "{TERMINAL_EVENT_TYPE}" event, so the run may still be live '
            "(set include_unfinished to prune idle runs that never finished)"
        )
    last = datetime.fromisoformat(item.last_event_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last > cutoff:
        return f"last event within the past {policy.older_than_seconds:g}s (older_than_seconds)"
    return None


def _stopped(
    run_id: str, step: str, error: Exception, tombstone: RunTombstone | None
) -> AgentSdkError:
    name = error.code if isinstance(error, AgentSdkError) else type(error).__name__
    changed = isinstance(error, AgentSdkError) and error.code == "TELEMETRY_RUN_CHANGED"
    resume = (
        f" Tombstone {tombstone.sequence} is already written, so running the same policy again "
        "finishes the prune."
        if tombstone is not None and not changed
        else ""
    )
    return AgentSdkError(
        "RETENTION_STEP_FAILED",
        f'Pruning run "{run_id}" stopped while {step}: {name}: {error}{resume}',
        {"run_id": run_id, "step": step},
    )


def _broken_log(path: Path, failure: ChainBreak) -> AgentSdkError:
    return AgentSdkError(
        "RETENTION_TOMBSTONES_BROKEN",
        f'The retention tombstones in "{path}" fail verification at sequence {failure.sequence} '
        f"({failure.kind}): {failure.message}",
        {"path": str(path), "sequence": failure.sequence, "kind": failure.kind},
    )


def _complete_lines(path: Path) -> Iterator[RunTombstone]:
    if not path.is_file():
        return
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n"):
                return
            yield RunTombstone.model_validate_json(raw.decode("utf-8"))


def _load(path: Path) -> tuple[list[RunTombstone], ChainBreak | None]:
    entries: list[RunTombstone] = []

    def stream() -> Iterator[RunTombstone]:
        for entry in _complete_lines(path):
            entries.append(entry)
            yield entry

    failure = first_chain_break(
        stream(),
        noun="retention tombstone",
        previous_attribute="previous_hash",
        expected_hash=tombstone_hash,
    )
    return entries, failure


class _TombstoneAppender:
    def __init__(self, path: Path) -> None:
        self._path = path
        _drop_unfinished_line(path)
        entries, failure = _load(path)
        if failure is not None:
            raise _broken_log(path, failure)
        self._entries = entries
        self._known = {
            (entry.run_id, entry.telemetry.last_sequence, entry.telemetry.head_hash): entry
            for entry in entries
        }

    def record(
        self,
        run_id: str,
        *,
        pruned_at_utc: str,
        last_event_at: str,
        policy: RetentionPolicy,
        telemetry: TelemetryFootprint,
        audit: AuditFootprint,
        journal: JournalFootprint,
        report_path: str | None,
    ) -> RunTombstone:
        key = (run_id, telemetry.last_sequence, telemetry.head_hash)
        existing = self._known.get(key)
        if existing is not None:
            return existing
        previous = self._entries[-1] if self._entries else None
        draft = RunTombstone(
            sequence=len(self._entries) + 1,
            previous_hash=previous.integrity_hash if previous is not None else None,
            run_id=run_id,
            pruned_at_utc=pruned_at_utc,
            last_event_at=last_event_at,
            policy=policy,
            telemetry=telemetry,
            audit=audit,
            journal=journal,
            report_path=report_path,
        )
        sealed = draft.model_copy(update={"integrity_hash": tombstone_hash(draft)})
        line = json.dumps(sealed.model_dump(mode="json"), sort_keys=True).encode("utf-8") + b"\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self._entries.append(sealed)
        self._known[key] = sealed
        return sealed


def _drop_unfinished_line(path: Path) -> None:
    if not path.is_file():
        return
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        with path.open("r+b") as stream:
            stream.truncate(data.rfind(b"\n") + 1)
