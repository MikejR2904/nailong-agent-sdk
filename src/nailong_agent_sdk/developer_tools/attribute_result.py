# Copyright (c) 2026 David Michael Indraputra

"""Find the first tool result of an agent run that a check rejects and the turn behind it."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.hashing import canonical_json
from ..memory.context_projection import FileToolResultJournal
from ..observability.audit_log import AuditTranscriptStore
from .decision_links import DecisionRef, decision_sentence, link_call

RESULT_SCHEMA_VERSION = "agent-sdk-result-attribution-v1"
MAX_LISTED_TOOLS = 20

type ResultCheck = Callable[[str, dict[str, Any], dict[str, Any]], bool]


class DefectiveResult(StrictModel):
    """One journaled tool result the check rejected and the model turn that asked for it."""

    position: int = Field(ge=1)
    handle_id: str
    tool: str
    tool_call_id: str
    status: str
    decision: DecisionRef


class ResultAttribution(StrictModel):
    """The first rejected result of a run, or the statement that none was rejected."""

    schema_version: str = RESULT_SCHEMA_VERSION
    run_id: str
    results: int = Field(ge=1)
    scanned: int = Field(ge=1)
    evaluations: int = Field(ge=1)
    ok: bool
    check: str
    tool_filter: str | None = None
    first_defective: DefectiveResult | None = None
    summary: str


@dataclass(frozen=True)
class _Journaled:
    position: int
    handle_id: str
    tool: str
    tool_call_id: str
    arguments: dict[str, Any]
    result: dict[str, Any]


def attribute_result_defect(
    run_root: Path,
    run_id: str,
    is_defective: ResultCheck,
    *,
    tool: str | None = None,
    check: str = "custom check",
) -> ResultAttribution:
    """Scan a run's journaled tool results in the order they were produced, reading only."""

    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    if not (run_root / ".agent-tool-results").is_dir():
        raise ValueError(
            f'"{run_root}" holds no .agent-tool-results directory: pass the run root of the '
            "service that ran the agent."
        )
    journal = FileToolResultJournal(run_root, read_only=True)
    handles = journal.handles_of(run_id)
    if not handles:
        raise ValueError(f'No tool result of run "{run_id}" is journaled under "{run_root}".')
    records = [
        _journaled(journal.read(handle), handle, position)
        for position, handle in enumerate(handles, start=1)
    ]
    scanned = [item for item in records if tool is None or item.tool == tool]
    if not scanned:
        listed = sorted({item.tool for item in records})[:MAX_LISTED_TOOLS]
        raise ValueError(
            f'Run "{run_id}" journaled no result of tool "{tool}"; tools journaled: {listed}.'
        )
    for evaluations, item in enumerate(scanned, start=1):
        if is_defective(item.tool, item.arguments, item.result):
            return _found(run_root, run_id, records, scanned, item, evaluations, tool, check)
    return ResultAttribution(
        run_id=run_id,
        results=len(records),
        scanned=len(scanned),
        evaluations=len(scanned),
        ok=True,
        check=check,
        tool_filter=tool,
        summary=(
            f'None of the {len(scanned)} journaled result(s) of run "{run_id}" fails the check '
            f"({check})."
        ),
    )


def result_check(
    *, contains: str | None = None, lacks: str | None = None, matches: str | None = None
) -> tuple[ResultCheck, str]:
    """Build one of the result checks the command line offers and a sentence describing it."""

    given = {
        name: value
        for name, value in (("contains", contains), ("lacks", lacks), ("matches", matches))
        if value is not None
    }
    if len(given) != 1:
        raise ValueError(
            f"exactly one of contains, lacks or matches is required, got {sorted(given)}."
        )
    name, value = next(iter(given.items()))
    if name == "contains":
        return (
            lambda tool, arguments, result: value in canonical_json(result),
            f'result contains "{value}"',
        )
    if name == "lacks":
        return (
            lambda tool, arguments, result: value not in canonical_json(result),
            f'result lacks "{value}"',
        )
    try:
        pattern = re.compile(value)
    except re.error as error:
        raise ValueError(
            f'matches pattern "{value}" is not a valid regular expression: {error}'
        ) from error
    return (
        lambda tool, arguments, result: pattern.search(canonical_json(result)) is not None,
        f"result matches /{value}/",
    )


def _journaled(record: dict[str, Any], handle_id: str, position: int) -> _Journaled:
    call, result = record.get("call"), record.get("result")
    if not isinstance(call, dict) or not isinstance(result, dict):
        raise ValueError(
            f'Journal record "{handle_id}" holds no call and result objects, got {sorted(record)}.'
        )
    arguments = call.get("arguments")
    return _Journaled(
        position=position,
        handle_id=handle_id,
        tool=str(call.get("name")),
        tool_call_id=str(call.get("id")),
        arguments=arguments if isinstance(arguments, dict) else {},
        result=result,
    )


def _found(
    run_root: Path,
    run_id: str,
    records: list[_Journaled],
    scanned: list[_Journaled],
    item: _Journaled,
    evaluations: int,
    tool: str | None,
    check: str,
) -> ResultAttribution:
    audit = (
        AuditTranscriptStore(run_root, read_only=True)
        if (run_root / ".agent-audit-logs").is_dir()
        else None
    )
    try:
        decision = link_call(audit, run_id, item.tool_call_id, item.tool)
    finally:
        if audit is not None:
            audit.close()
    return ResultAttribution(
        run_id=run_id,
        results=len(records),
        scanned=len(scanned),
        evaluations=evaluations,
        ok=False,
        check=check,
        tool_filter=tool,
        first_defective=DefectiveResult(
            position=item.position,
            handle_id=item.handle_id,
            tool=item.tool,
            tool_call_id=item.tool_call_id,
            status=str(item.result.get("status")),
            decision=decision,
        ),
        summary=(
            f'Run "{run_id}" first returned a rejected result ({check}) from {item.tool} call '
            f'"{item.tool_call_id}", result {item.position} of {len(records)}; '
            f"{decision_sentence(decision)}"
        ),
    )
