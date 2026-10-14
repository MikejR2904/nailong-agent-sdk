# Copyright (c) 2026 David Michael Indraputra

"""Per-run write fence that retention raises before it deletes a run's records.

Telemetry, audit and tool-result writers check the fence inside their own critical section.
Retention creates the fence, then enters each of those sections once, so every record a
writer already accepted is included in the final footprint and none is accepted afterwards.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..foundations.errors import AgentSdkError
from ..foundations.identifiers import file_safe_name

FENCE_DIRECTORY = Path(".agent-retention") / "fences"
FENCE_SUFFIX = ".fence"
DEFAULT_FENCE_WAIT_SECONDS = 10.0
_POLL_SECONDS = 0.02


@dataclass(frozen=True)
class FenceRecord:
    run_id: str
    holder: str
    pid: int
    started_at_utc: str


class RunFence:
    def __init__(self, run_root: Path, *, wait_seconds: float = DEFAULT_FENCE_WAIT_SECONDS) -> None:
        if wait_seconds < 0:
            raise ValueError("wait_seconds may not be negative.")
        self._directory = run_root.resolve() / FENCE_DIRECTORY
        self.wait_seconds = wait_seconds

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, run_id: str) -> Path:
        return self._directory / f"{file_safe_name(run_id)}{FENCE_SUFFIX}"

    def holder_of(self, run_id: str) -> FenceRecord | None:
        path = self.path_for(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return FenceRecord(run_id=run_id, holder="unreadable", pid=0, started_at_utc="")
        return FenceRecord(
            run_id=str(payload.get("run_id", run_id)),
            holder=str(payload.get("holder", "unknown")),
            pid=int(payload.get("pid", 0)),
            started_at_utc=str(payload.get("started_at_utc", "")),
        )

    def fenced_runs(self) -> list[FenceRecord]:
        if not self._directory.is_dir():
            return []
        records = []
        for path in sorted(self._directory.glob(f"*{FENCE_SUFFIX}")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                records.append(
                    FenceRecord(
                        run_id=str(payload["run_id"]),
                        holder=str(payload.get("holder", "unknown")),
                        pid=int(payload.get("pid", 0)),
                        started_at_utc=str(payload.get("started_at_utc", "")),
                    )
                )
            except (OSError, ValueError, KeyError):
                continue
        return records

    def is_fenced(self, run_id: str) -> bool:
        return self.path_for(run_id).is_file()

    def raise_if_fenced(self, run_id: str, operation: str) -> None:
        if not self.is_fenced(run_id):
            return
        holder = self.holder_of(run_id)
        since = f" since {holder.started_at_utc}" if holder and holder.started_at_utc else ""
        by = f" by {holder.holder}" if holder else ""
        raise AgentSdkError(
            "RUN_PRUNING",
            f'Run "{run_id}" is being pruned by the retention policy (fenced{since}{by}), so '
            f'"{operation}" was refused: nothing accepted after the fence would survive the '
            "deletion. Retry after the prune finishes, finish an interrupted prune by running "
            "the same retention policy again, or use a new run id.",
            {"run_id": run_id, "operation": operation},
        )

    def wait_until_open(self, run_id: str, operation: str) -> None:
        deadline = time.monotonic() + self.wait_seconds
        while self.is_fenced(run_id):
            if time.monotonic() >= deadline:
                self.raise_if_fenced(run_id, operation)
            time.sleep(_POLL_SECONDS)

    def begin(self, run_id: str, *, holder: str) -> FenceRecord:
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(run_id)
        record = FenceRecord(
            run_id=run_id,
            holder=holder,
            pid=os.getpid(),
            started_at_utc=datetime.now(UTC).isoformat(),
        )
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return self.holder_of(run_id) or record
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "run_id": record.run_id,
                        "holder": record.holder,
                        "pid": record.pid,
                        "started_at_utc": record.started_at_utc,
                    },
                    sort_keys=True,
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def end(self, run_id: str) -> None:
        self.path_for(run_id).unlink(missing_ok=True)
