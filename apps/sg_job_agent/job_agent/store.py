"""Durable job ledger: every discovered posting and where it is in the pipeline.

The ledger is the single source of truth across runs, so re-running discovery
never re-applies to a job and a crashed run resumes where it stopped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .sources import JobPosting, canonical_url, job_id_for


class JobStatus(StrEnum):
    DISCOVERED = "discovered"
    TAILORED = "tailored"
    TAILOR_FAILED = "tailor_failed"
    APPLIED = "applied"
    NEEDS_MANUAL = "needs_manual"
    SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class JobLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        if path.is_file():
            self._jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs", {})

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"jobs": self._jobs}, indent=2, sort_keys=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temp, self.path)

    def find_duplicate(self, posting: JobPosting) -> str | None:
        """Same canonical URL, or the same company + title seen via another site."""

        job_id = posting.id
        if job_id in self._jobs:
            return job_id
        key = (_norm(posting.company), _norm(posting.title))
        for existing_id, record in self._jobs.items():
            if key[0] and (_norm(record["company"]), _norm(record["title"])) == key:
                return existing_id
        return None

    def add(self, posting: JobPosting, **extra: Any) -> tuple[str, bool]:
        """Insert a posting. Returns (job_id, created)."""

        with self._lock:
            duplicate = self.find_duplicate(posting)
            if duplicate is not None:
                record = self._jobs[duplicate]
                # Merge sightings, and keep the richest description we have seen.
                urls = set(record.get("seen_urls", [])) | {canonical_url(posting.url)}
                record["seen_urls"] = sorted(urls)
                if len(posting.description) > len(record.get("description", "")):
                    record["description"] = posting.description
                if extra.get("fit_score", -1) > record.get("fit_score", -1):
                    record.update({k: v for k, v in extra.items() if v is not None})
                self._save()
                return duplicate, False
            record = {
                **posting.to_dict(),
                "seen_urls": [canonical_url(posting.url)],
                "status": JobStatus.DISCOVERED.value,
                "discovered_at": _now(),
                "history": [],
                **{k: v for k, v in extra.items() if v is not None},
            }
            self._jobs[posting.id] = record
            self._save()
            return posting.id, True

    def get(self, job_id: str) -> dict[str, Any]:
        if job_id not in self._jobs:
            raise KeyError(f"Unknown job id {job_id!r}")
        return self._jobs[job_id]

    def get_by_url(self, url: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id_for(url))

    def update(self, job_id: str, *, status: JobStatus | None = None, **fields: Any) -> None:
        with self._lock:
            record = self.get(job_id)
            record.update(fields)
            if status is not None and record.get("status") != status.value:
                record["history"].append(
                    {"at": _now(), "from": record.get("status"), "to": status.value}
                )
                record["status"] = status.value
            self._save()

    def all(self) -> list[dict[str, Any]]:
        return list(self._jobs.values())

    def by_status(self, *statuses: JobStatus) -> list[dict[str, Any]]:
        wanted = {status.value for status in statuses}
        return [job for job in self._jobs.values() if job.get("status") in wanted]

    def ranked(self, *statuses: JobStatus, min_fit: int = 0) -> list[dict[str, Any]]:
        jobs = self.by_status(*statuses) if statuses else self.all()
        return sorted(
            (job for job in jobs if job.get("fit_score", 0) >= min_fit),
            key=lambda job: (-job.get("fit_score", 0), job.get("discovered_at", "")),
        )
