# Copyright (c) 2026 David Michael Indraputra

"""Tamper LangGraph's persisted message record; its only check is that the record still loads."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import messages_from_dict

from ..tamper import CLASSES, TamperReport, TamperTrial, positions, tamper_lines
from .langgraph_arm import LgRun

SYSTEM = "langgraph"


def _loads(lines: list[str]) -> tuple[str, ...]:
    try:
        messages_from_dict([json.loads(line) for line in lines])
    except Exception as error:
        return (f"unreadable:{type(error).__name__}",)
    return ()


def _mutate(line: str) -> str:
    body = json.loads(line)
    data = body.get("data", {})
    data["content"] = f"{data.get('content', '')} [forged]"
    body["data"] = data
    return json.dumps(body, sort_keys=True)


def native_failures(run: LgRun) -> list[str]:
    raw = json.loads(run.records_path.read_text(encoding="utf-8"))
    return list(_loads([json.dumps(item, sort_keys=True) for item in raw]))


def tamper_langgraph(run: LgRun, workdir: Path, wanted: int = 10) -> TamperReport:
    report = TamperReport()
    lines = [
        json.dumps(item, sort_keys=True) for item in json.loads(run.records_path.read_text("utf-8"))
    ]
    report.pristine_checks += 1
    clean = _loads(lines)
    if clean:
        report.false_alarms.append(f"{run.run_id}: {', '.join(clean)}")
    for klass in CLASSES:
        for position in positions(len(lines), wanted):
            tampered = tamper_lines(lines, klass, position, _mutate, None)
            if tampered is None:
                continue
            failures = _loads(tampered)
            report.trials.append(
                TamperTrial(SYSTEM, "messages", klass, position, bool(failures), failures)
            )
    return report
