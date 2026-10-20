# Copyright (c) 2026 David Michael Indraputra

"""Enumerate the jobs of a split and run them, optionally on a process pool."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .evaluate import ARMS, FAMILIES, evaluate_job
from .splits import Split, families_for, operators_for, seeds_for

Job = tuple[str, int, str, str | None, str, frozenset[str]]


def enumerate_jobs(
    split: Split,
    families: Iterable[str],
    arms: Iterable[str],
    seeds: Iterable[int],
) -> list[Job]:
    operators = operators_for(split)
    allowed = families_for(split, frozenset(FAMILIES))
    jobs: list[Job] = []
    for family in families:
        if family not in allowed:
            raise ValueError(
                f'family "{family}" is not available in the {split.value} split; '
                f"available: {sorted(allowed)}."
            )
        for seed in seeds:
            case = FAMILIES[family](seed)
            variants: list[str | None] = [None, *(f.operator_id for f in case.faults(operators))]
            for arm in arms:
                if arm not in ARMS:
                    raise ValueError(f'unknown arm "{arm}"; known: {", ".join(ARMS)}.')
                jobs.extend(
                    (family, seed, arm, variant, split.value, operators) for variant in variants
                )
    return jobs


def parse_seeds(text: str, split: Split) -> list[int]:
    allowed = seeds_for(split)
    chosen: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            low, high = (int(item) for item in part.split("-", 1))
            chosen.extend(range(low, high + 1))
        elif part:
            chosen.append(int(part))
    outside = [seed for seed in chosen if seed not in allowed]
    if outside:
        raise ValueError(
            f"seeds {outside[:5]} are outside the {split.value} range "
            f"[{allowed.start}, {allowed.stop - 1}]."
        )
    return chosen


def run_jobs(jobs: list[Job], workers: int = 1) -> Iterator[dict[str, Any]]:
    if workers <= 1:
        for job in jobs:
            yield evaluate_job(job)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(evaluate_job, jobs, chunksize=4)


def write_results(results: Iterable[dict[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, sort_keys=True) + "\n")
            handle.flush()
            count += 1
    return count


def read_results(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
