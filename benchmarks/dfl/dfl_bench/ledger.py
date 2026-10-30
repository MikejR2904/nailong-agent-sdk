"""Measured downstream outcomes: an append-only ledger and the executor that replays it.

A real evaluation runs an agent on each (instance, candidate selection, trial) and scores it with an
independent verifier. ``OutcomeLedger`` persists one JSON line per run, immediately, so an
interrupted evaluation resumes without repeating a paid call. ``LedgerExecutor`` serves recorded
lines with ``evidence_kind == "measured"`` and never invents an outcome.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from .instances import Instance
from .oracle import MEASURED


def selection_key(selection: Collection[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(selection)).encode()).hexdigest()[:20]


class OutcomeLedger:
    def __init__(self, path: Path, *, create: bool = False) -> None:
        self._path = Path(path)
        self._outcomes: dict[tuple[str, str, int], float] = {}
        if not self._path.exists():
            if not create:
                raise FileNotFoundError(f'Outcome ledger "{self._path}" does not exist.')
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                self._ingest(line, number)

    def _ingest(self, line: str, number: int) -> None:
        try:
            row = json.loads(line)
            key = (row["instance_id"], selection_key(row["selection"]), int(row["trial"]))
            outcome = float(row["outcome"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f'Outcome ledger "{self._path}" line {number} is malformed: '
                f"{type(error).__name__}: {error}"
            ) from error
        if not 0.0 <= outcome <= 1.0:
            raise ValueError(
                f'Outcome ledger "{self._path}" line {number} has outcome {outcome}, '
                "outside [0, 1]."
            )
        if key in self._outcomes and self._outcomes[key] != outcome:
            raise ValueError(
                f'Outcome ledger "{self._path}" line {number} repeats instance "{key[0]}" trial '
                f"{key[2]} for the same selection with a different outcome."
            )
        self._outcomes[key] = outcome

    def __len__(self) -> int:
        return len(self._outcomes)

    def lookup(self, instance_id: str, selection: Collection[str], trial: int) -> float | None:
        return self._outcomes.get((instance_id, selection_key(selection), trial))

    def record(
        self,
        instance_id: str,
        selection: Collection[str],
        trial: int,
        outcome: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not 0.0 <= float(outcome) <= 1.0:
            raise ValueError(f'Outcome {outcome!r} for instance "{instance_id}" is outside [0, 1].')
        line = json.dumps(
            {
                "instance_id": instance_id,
                "selection": sorted(selection),
                "trial": int(trial),
                "outcome": float(outcome),
                **({"detail": detail} if detail else {}),
            }
        )
        self._ingest(line, len(self._outcomes) + 1)
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def write_outcomes(path: Path, rows: Iterable[dict]) -> int:
    """Append rows ``{instance_id, selection, trial, outcome}``; outcome must be in [0, 1]."""

    ledger = OutcomeLedger(path, create=True)
    count = 0
    for row in rows:
        ledger.record(
            row["instance_id"], row["selection"], row["trial"], row["outcome"], row.get("detail")
        )
        count += 1
    return count


class LedgerExecutor(OutcomeLedger):
    evidence_kind = MEASURED

    def __init__(self, path: Path) -> None:
        super().__init__(path, create=False)

    def run(self, instance: Instance, selection: Collection[str], seed: int) -> float:
        outcome = self.lookup(instance.instance_id, selection, seed)
        if outcome is None:
            raise KeyError(
                f'No recorded outcome for instance "{instance.instance_id}", trial {seed}, and '
                f"selection {sorted(selection)}; a measured executor never fabricates outcomes."
            )
        return outcome
