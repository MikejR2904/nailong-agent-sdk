# Copyright (c) 2026 David Michael Indraputra

"""Tamper operators over append-only line records, applied at fixed positions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

CLASSES = ("edit", "delete", "reorder", "insert", "truncate", "reseal")

Mutate = Callable[[str], str]
Reseal = Callable[[list[str], int], list[str]]


def positions(count: int, wanted: int = 10) -> list[int]:
    if wanted < 2:
        raise ValueError(f"positions needs at least 2 wanted positions, got {wanted}.")
    if count <= 0:
        return []
    return sorted({round(i * (count - 1) / (wanted - 1)) for i in range(wanted)})


def tamper_lines(
    lines: list[str], klass: str, position: int, mutate: Mutate, reseal: Reseal | None = None
) -> list[str] | None:
    result = list(lines)
    last = len(result) - 1
    if klass == "edit":
        result[position] = mutate(result[position])
    elif klass == "delete":
        del result[position]
    elif klass == "reorder":
        other = position + 1 if position < last else position - 1
        if other < 0:
            return None
        result[position], result[other] = result[other], result[position]
    elif klass == "insert":
        result.insert(position + 1, mutate(result[position]))
    elif klass == "truncate":
        if position == 0:
            return None
        result = result[:position]
    elif klass == "reseal":
        if reseal is None:
            return None
        result[position] = mutate(result[position])
        result = reseal(result, position)
    else:
        raise ValueError(f'unknown tamper class "{klass}"; known: {", ".join(CLASSES)}.')
    return result


@dataclass(frozen=True)
class TamperTrial:
    system: str
    store: str
    klass: str
    position: int
    detected: bool
    failures: tuple[str, ...] = ()


@dataclass
class TamperReport:
    trials: list[TamperTrial] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)
    pristine_checks: int = 0


class LinkOnlyChain:
    """The reference store of the published 'chain' configuration: links, no per-entry check."""

    def __init__(self, entries: int) -> None:
        self.lines: list[str] = []
        previous = ""
        for index in range(entries):
            body = {"n": index, "event": f"event-{index}", "prev": previous}
            line = json.dumps(body, sort_keys=True)
            self.lines.append(line)
            previous = hashlib.sha256(line.encode("utf-8")).hexdigest()

    @staticmethod
    def mutate(line: str) -> str:
        body = json.loads(line)
        body["event"] = body["event"] + "-forged"
        return json.dumps(body, sort_keys=True)

    @staticmethod
    def reseal(lines: list[str], start: int) -> list[str]:
        sealed = list(lines)
        previous = (
            hashlib.sha256(sealed[start - 1].encode("utf-8")).hexdigest() if start > 0 else ""
        )
        for index in range(start, len(sealed)):
            body = json.loads(sealed[index])
            body["prev"] = previous
            sealed[index] = json.dumps(body, sort_keys=True)
            previous = hashlib.sha256(sealed[index].encode("utf-8")).hexdigest()
        return sealed

    @staticmethod
    def verify(lines: list[str]) -> bool:
        previous = ""
        for line in lines:
            body = json.loads(line)
            if body["prev"] != previous:
                return False
            previous = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return True
