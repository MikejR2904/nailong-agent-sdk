# Copyright (c) 2026 David Michael Indraputra

"""One retention policy applied across every growing ledger under a run root.

Telemetry, audit transcripts, stored tool results and episode records grow with
every run. ``apply_retention`` prunes them together so a long-lived host has a
bounded footprint. Telemetry and audit logs are pruned by whole run, so every
remaining integrity chain still verifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..memory.context_projection import FileToolResultJournal
from ..memory.episode_store import FileEpisodeStore
from .audit_log import AuditTranscriptStore
from .telemetry_store import TelemetryStore


class RetentionPolicy(StrictModel):
    """What to keep. Unset fields keep everything along that dimension."""

    max_age_days: float | None = Field(default=None, gt=0)
    keep_latest_runs: int | None = Field(default=None, ge=0)
    keep_compacted_episodes: int | None = Field(default=None, ge=0)


@dataclass
class RetentionReport:
    telemetry_runs_removed: list[str] = field(default_factory=list)
    audit_runs_removed: list[str] = field(default_factory=list)
    tool_results_removed: int = 0
    episodes_removed: list[str] = field(default_factory=list)


def apply_retention(
    run_root: Path,
    policy: RetentionPolicy,
    *,
    now: datetime | None = None,
    telemetry: TelemetryStore | None = None,
    audit_logs: AuditTranscriptStore | None = None,
) -> RetentionReport:
    """Prune every ledger under ``run_root`` according to ``policy``.

    Pass the host's open ``telemetry`` / ``audit_logs`` stores so their caches
    stay consistent; otherwise short-lived ones are opened for the run.
    """

    cutoff = (
        (now or datetime.now(UTC)) - timedelta(days=policy.max_age_days)
        if policy.max_age_days is not None
        else None
    )
    report = RetentionReport()
    owned_telemetry = telemetry is None
    telemetry = telemetry or TelemetryStore(run_root)
    try:
        report.telemetry_runs_removed = telemetry.prune(
            older_than=cutoff, keep_latest_runs=policy.keep_latest_runs
        )
    finally:
        if owned_telemetry:
            telemetry.close()
    owned_audit = audit_logs is None
    audit_logs = audit_logs or AuditTranscriptStore(run_root)
    try:
        report.audit_runs_removed = audit_logs.prune(
            older_than=cutoff, keep_latest_runs=policy.keep_latest_runs
        )
    finally:
        if owned_audit:
            audit_logs.close()
    if cutoff is not None and (run_root / ".agent-tool-results").is_dir():
        report.tool_results_removed = FileToolResultJournal(run_root).prune(older_than=cutoff)
    if (
        policy.keep_compacted_episodes is not None
        and (run_root / ".agent-memory" / "episodes.json").is_file()
    ):
        episodes = FileEpisodeStore(run_root)
        episodes.load()
        report.episodes_removed = episodes.prune_compacted(
            keep_latest=policy.keep_compacted_episodes
        )
    return report
