# Copyright (c) 2026 David Michael Indraputra

"""What the recorder holds and what a comparison with it concludes."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field

from ...foundations.contracts import StrictModel
from ..run_checkpoints import CheckpointSink


class RecordedEntry(StrictModel):
    sequence: int = Field(ge=1)
    hash: str
    previous: str | None = None
    record: str | None = None
    received_at_utc: str | None = None
    claimed_at_utc: str | None = None


class RecorderVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    NO_RECORD = "no-record"
    RECORDER_UNVERIFIED = "recorder-unverified"


class RecorderComparison(StrictModel):
    store: str
    verdict: RecorderVerdict
    recorded: int = Field(ge=0)
    local: int = Field(ge=0)
    first_difference: int | None = None
    removed_tail: int = Field(default=0, ge=0)
    differing: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {RecorderVerdict.MATCHES, RecorderVerdict.ADVANCED}


class RecorderSource(Protocol):
    def entries(
        self, run_id: str, store: str, include_records: bool = True
    ) -> list[RecordedEntry]: ...

    def head_sink(self) -> CheckpointSink: ...
