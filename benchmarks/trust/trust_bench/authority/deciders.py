# Copyright (c) 2026 David Michael Indraputra

"""The deciders of each class, the simulated reviewer and the harness's own decision log."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import digest_of
from .tickets import Ticket


@dataclass(frozen=True)
class Reviewer:
    """How often a decider rejects a bad action (sensitivity) and approves a good one."""

    sensitivity: float = 1.0
    specificity: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (("sensitivity", self.sensitivity), ("specificity", self.specificity)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"reviewer {name} must lie in [0, 1], got {value}.")


IDEAL = Reviewer()


def approves(ticket: Ticket, cls: str, case_id: str, reviewers: Mapping[str, Reviewer]) -> bool:
    reviewer = reviewers.get(cls, IDEAL)
    draw = random.Random(f"{case_id}-{ticket.id}-{cls}").random()
    if ticket.bad:
        return draw >= reviewer.sensitivity
    return draw < reviewer.specificity


class DecisionLog:
    """Every approval or rejection the harness's deciders made: the ground truth for authority."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._previous = ""
        self.entries: list[dict[str, Any]] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def record(
        self,
        ticket_id: str,
        cls: str,
        decider: str,
        rule: str,
        approved: bool,
        scope: str = "call",
    ) -> None:
        entry: dict[str, Any] = {
            "n": len(self.entries) + 1,
            "ticket": ticket_id,
            "class": cls,
            "decider": decider,
            "rule": rule,
            "approved": approved,
            "scope": scope,
            "prev": self._previous,
        }
        entry["hash"] = digest_of(entry)
        self._previous = entry["hash"]
        self.entries.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def of(self, ticket_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["ticket"] == ticket_id]

    def intact(self) -> bool:
        previous = ""
        for number, entry in enumerate(self.entries, start=1):
            body = {key: value for key, value in entry.items() if key != "hash"}
            if body["n"] != number or body["prev"] != previous or digest_of(body) != entry["hash"]:
                return False
            previous = entry["hash"]
        return True
