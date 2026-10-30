# Copyright (c) 2026 David Michael Indraputra

"""Escape and false-rejection rates of every acceptance mechanism, and the attack battery."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import stats

LEVELS = (0, 1, 2)
GATED = "held-out gate"
JUDGE = "jev-judge"
MECHANISMS: dict[str, Callable[[Mapping[str, Any]], bool | None]] = {
    "self-report": lambda row: bool(row["ungated"]["accepted"]),
    "format-only": lambda row: bool(row["format_ok"]),
    "visible-check": lambda row: bool(row["visible_ok"]),
    GATED: lambda row: bool(row["gated"]["accepted"]),
    JUDGE: lambda row: row["judge"]["accepted"] if "judge" in row else None,
}


def verification(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    delivered = [r for r in rows if r["delivered"]]
    clean = [r for r in delivered if r["quiet"] is None]
    defective = [r for r in delivered if r["quiet"] is not None]
    out: dict[str, Any] = {
        "clean": len(clean),
        "defective": len(defective),
        "levels": {level: sum(1 for r in defective if r["quiet"] == level) for level in LEVELS},
        "mechanisms": {},
    }
    for name, accepts in MECHANISMS.items():
        judged = [r for r in delivered if accepts(r) is not None]
        if name == JUDGE and not judged:
            continue
        clean_j = [r for r in judged if r["quiet"] is None]
        defective_j = [r for r in judged if r["quiet"] is not None]
        out["mechanisms"][name] = {
            "escape": {
                level: stats.proportion(
                    sum(1 for r in defective_j if r["quiet"] == level and accepts(r)),
                    sum(1 for r in defective_j if r["quiet"] == level),
                )
                for level in LEVELS
            },
            "false_rejection": stats.proportion(
                sum(1 for r in clean_j if not accepts(r)), len(clean_j)
            ),
        }
    out["recorded"] = stats.proportion(
        sum(1 for r in delivered if r["gated"]["record"]), len(delivered)
    )
    out["binds"] = stats.proportion(
        sum(1 for r in delivered if r["gated"]["record_binds"]), len(delivered)
    )
    out["inconsistent"] = sum(
        1 for r in delivered if not r["same_bytes"] or not r["recorder_agrees"]
    )
    return out


def attack_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, stats.Proportion]:
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        if not row["attempted"] or not row["defective"]:
            continue
        cell = cells[f"{row['gate']}/{row['attack']}"]
        cell[0] += int(row["success"])
        cell[1] += 1
    return {name: stats.proportion(hit, total) for name, (hit, total) in sorted(cells.items())}


def held_out_attacks(table: Mapping[str, stats.Proportion]) -> tuple[int, int]:
    wins = sum(p.successes for name, p in table.items() if name.startswith("held-out/"))
    tries = sum(p.trials for name, p in table.items() if name.startswith("held-out/"))
    return wins, tries


def control_attacks(table: Mapping[str, stats.Proportion]) -> tuple[int, int]:
    wins = sum(p.successes for name, p in table.items() if name.startswith("naive-"))
    tries = sum(p.trials for name, p in table.items() if name.startswith("naive-"))
    return wins, tries
