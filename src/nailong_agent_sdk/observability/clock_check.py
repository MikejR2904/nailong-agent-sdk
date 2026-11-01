# Copyright (c) 2026 David Michael Indraputra

"""Detecting a ledger whose recorded times run backwards."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import Field

from ..foundations.contracts import StrictModel


class TimeRegression(StrictModel):
    sequence: int = Field(ge=1)
    previous_at_utc: str
    at_utc: str
    seconds_back: float


class TimeOrderReport(StrictModel):
    checked: int = Field(ge=0)
    unparsable: int = Field(ge=0)
    regressions: list[TimeRegression]
    total_regressions: int = Field(ge=0)

    @property
    def ordered(self) -> bool:
        return self.total_regressions == 0 and self.unparsable == 0


def parse_utc(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def find_time_regressions(
    times: Iterable[tuple[int, str]], *, tolerance_seconds: float = 0.0, keep: int = 5
) -> TimeOrderReport:
    previous: tuple[datetime, str] | None = None
    checked = unparsable = total = 0
    kept: list[TimeRegression] = []
    for sequence, text in times:
        checked += 1
        moment = parse_utc(text)
        if moment is None:
            unparsable += 1
            continue
        if previous is not None:
            back = (previous[0] - moment).total_seconds()
            if back > tolerance_seconds:
                total += 1
                if len(kept) < keep:
                    kept.append(
                        TimeRegression(
                            sequence=sequence,
                            previous_at_utc=previous[1],
                            at_utc=text,
                            seconds_back=back,
                        )
                    )
        previous = (moment, text)
    return TimeOrderReport(
        checked=checked, unparsable=unparsable, regressions=kept, total_regressions=total
    )
