# Copyright (c) 2026 David Michael Indraputra

"""Put prepared questions to Jev, a budget and a cache in the way, and keep every answer."""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import prepare
from .budget import Budget, BudgetExceeded, dollars, estimate_tokens
from .client import JevJudge
from .specs import QUESTION

PURPOSE = {prepare.DIAGNOSIS: "verification", prepare.ACCEPTANCE: "verification"}


def read_inputs(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def read_answers(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            found[record["id"]] = record
    return found


def estimate(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calls, input tokens and dollars the items would cost, without sending anything."""
    per_task: dict[str, list[int]] = {}
    for item in items:
        text = json.dumps(item["state"]) + prepare.spec_of(dict(item)).model_dump_json()
        per_task.setdefault(item["task"], []).append(estimate_tokens(text))
    total = sum(sum(sizes) for sizes in per_task.values())
    return {
        "calls": sum(len(sizes) for sizes in per_task.values()),
        "input_tokens": total,
        "dollars": dollars(total),
        "tasks": {
            task: {"calls": len(sizes), "input_tokens": sum(sizes), "largest": max(sizes)}
            for task, sizes in sorted(per_task.items())
        },
    }


def _record(item: Mapping[str, Any], judge: JevJudge) -> dict[str, Any]:
    answer = judge.ask(
        prepare.spec_of(dict(item)), item["state"], PURPOSE.get(item["task"], "routing")
    )
    mismatch = answer.model is not None and answer.model != judge.model
    return {
        "id": item["id"],
        "succeeded": answer.succeeded and not mismatch,
        "cached": answer.cached,
        "model": answer.model,
        "model_mismatch": mismatch,
        "input_tokens": answer.input_tokens,
        "reason": answer.reason,
        "answer": answer.answers[QUESTION].model_dump(mode="json")
        if answer.succeeded and QUESTION in answer.answers
        else None,
    }


def ask_items(
    items: Sequence[Mapping[str, Any]], judge: JevJudge, out: Path, *, workers: int = 4
) -> Counter[str]:
    """Ask every item not already answered; each answer is appended as soon as it arrives."""
    done = {k for k, v in read_answers(out).items() if v.get("succeeded")}
    todo = [item for item in items if item["id"] not in done]
    counts: Counter[str] = Counter(skipped=len(items) - len(todo))
    lock = threading.Lock()
    out.parent.mkdir(parents=True, exist_ok=True)

    def work(item: Mapping[str, Any]) -> None:
        try:
            record = _record(item, judge)
        except BudgetExceeded:
            with lock:
                counts["over_budget"] += 1
            return
        with lock, out.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            counts["cached" if record["cached"] else "asked"] += 1
            counts["unavailable"] += 0 if record["succeeded"] else 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(work, item) for item in todo]):
            future.result()
    return counts


def make_budget(max_calls: int, max_input_tokens: int) -> Budget:
    return Budget(max_calls, max_input_tokens)
