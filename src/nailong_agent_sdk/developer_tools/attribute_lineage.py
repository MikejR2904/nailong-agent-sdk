# Copyright (c) 2026 David Michael Indraputra

"""Trace a failing run back through the artifact versions it read to the write that began it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.text import split_lines
from ..memory.context_projection import FileToolResultJournal
from ..observability.audit_log import AuditTranscriptStore
from ..tools.artifacts import ArtifactStore, ArtifactVersion
from .attribute_defect import DefectWriter, _writer
from .decision_links import DecisionRef, decision_sentence, link_call

LINEAGE_SCHEMA_VERSION = "agent-sdk-lineage-attribution-v1"
DEFAULT_MAX_DEPTH = 8
MAX_LISTED_READS = 50
READ_TOOLS = frozenset({"read_artifact", "grep_artifact", "read_file", "diff_declared_artifacts"})

type ContentCheck = Callable[[str, bytes], bool]


class LineageRead(StrictModel):
    """One journaled read of an artifact and every version that could have served it."""

    position: int = Field(ge=1)
    handle_id: str
    tool: str
    tool_call_id: str
    path: str | None = None
    artifact_id: str | None = None
    candidates: list[DefectWriter] = Field(default_factory=list)
    defective: bool = False
    unmatched_reason: str | None = None

    @property
    def unique(self) -> bool:
        return len(self.candidates) == 1


class LineageHop(StrictModel):
    """A run on the path, the defective read that led out of it and the version it came from."""

    run_id: str
    reads: int = Field(ge=0)
    defective_read: LineageRead | None = None
    ambiguous: bool = False


class LineageAttribution(StrictModel):
    """Where a defect entered the data a run consumed, and who wrote that version."""

    schema_version: str = LINEAGE_SCHEMA_VERSION
    run_id: str
    ok: bool
    hops: list[LineageHop] = Field(default_factory=list)
    origin: DefectWriter | None = None
    origin_decision: DecisionRef | None = None
    origin_reason: str
    summary: str


@dataclass(frozen=True)
class _Index:
    store: ArtifactStore
    by_artifact: dict[str, list[ArtifactVersion]]
    by_path: dict[str, list[ArtifactVersion]]


def attribute_lineage(
    run_root: Path,
    workspace: Path,
    run_id: str,
    is_defective: ContentCheck,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> LineageAttribution:
    """Follow the defective inputs of ``run_id`` upstream, reading only.

    A hop is the first journaled read of a run whose served version fails ``is_defective``. The
    writer of that version names the next run to inspect. The trace ends at the writer of a
    version whose own run read nothing defective (the defect began in that run's own decision),
    at a version several writers could have produced (the ambiguity is reported, not resolved),
    at a writer that recorded no run, or at ``max_depth`` hops.
    """

    if max_depth < 1:
        raise ValueError(f"max_depth must be at least 1, got {max_depth}.")
    if not (run_root / ".agent-tool-results").is_dir():
        raise ValueError(
            f'"{run_root}" holds no .agent-tool-results directory: pass the run root of the '
            "service that ran the agent."
        )
    if not (workspace / ".agent-artifacts").is_dir():
        raise ValueError(f'"{workspace}" holds no .agent-artifacts directory.')
    index = _index(ArtifactStore(workspace, read_only=True))
    journal = FileToolResultJournal(run_root, read_only=True)
    hops: list[LineageHop] = []
    seen: set[str] = set()
    current = run_id
    origin: DefectWriter | None = None
    reason = ""
    while True:
        seen.add(current)
        reads = _reads(journal, index, current, is_defective)
        bad = next((item for item in reads if item.defective), None)
        if bad is None:
            reason = (
                f'Run "{current}" read no version that fails the check, so the defect began in '
                "its own output."
                if hops
                else f'Run "{run_id}" read no version that fails the check.'
            )
            break
        ambiguous = not bad.unique
        hops.append(
            LineageHop(
                run_id=current,
                reads=len(reads),
                defective_read=bad,
                ambiguous=ambiguous,
            )
        )
        if ambiguous:
            reason = (
                f"{len(bad.candidates)} writes produced the defective version read by "
                f'"{current}"; the records cannot tell which one was served.'
            )
            break
        writer = bad.candidates[0]
        origin = writer
        upstream = writer.agent_run_id
        if upstream is None or upstream in seen:
            reason = (
                "The writer recorded no run id."
                if upstream is None
                else f'The trace returned to run "{upstream}".'
            )
            break
        if len(hops) >= max_depth:
            reason = f"The trace stopped at the depth limit of {max_depth} hops."
            break
        current = upstream
    ok = not hops
    decision = _origin_decision(run_root, origin) if origin is not None else None
    return LineageAttribution(
        run_id=run_id,
        ok=ok,
        hops=hops,
        origin=origin,
        origin_decision=decision,
        origin_reason=reason,
        summary=_summary(run_id, ok, hops, origin, decision, reason),
    )


def _index(store: ArtifactStore) -> _Index:
    by_artifact: dict[str, list[ArtifactVersion]] = {}
    by_path: dict[str, list[ArtifactVersion]] = {}
    for path in store.paths(limit=10_000_000):
        for version in store.history(path):
            by_artifact.setdefault(version.artifact_id, []).append(version)
            by_path.setdefault(version.relative_path, []).append(version)
    return _Index(store, by_artifact, by_path)


def _reads(
    journal: FileToolResultJournal, index: _Index, run_id: str, check: ContentCheck
) -> list[LineageRead]:
    reads: list[LineageRead] = []
    for position, handle in enumerate(journal.handles_of(run_id), start=1):
        record = journal.read(handle)
        call, result = record.get("call"), record.get("result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            continue
        tool = str(call.get("name"))
        if tool not in READ_TOOLS or result.get("status") != "succeeded":
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        base = {
            "position": position,
            "handle_id": handle,
            "tool": tool,
            "tool_call_id": str(call.get("id")),
        }
        for path, artifact_id, versions, why in _served(tool, arguments, output, index):
            candidates = [_writer(version) for version in versions]
            defective = bool(versions) and all(
                check(version.relative_path, index.store.read_bytes(version.sha256))
                for version in versions
            )
            reads.append(
                LineageRead(
                    **base,
                    path=path,
                    artifact_id=artifact_id,
                    candidates=candidates,
                    defective=defective,
                    unmatched_reason=why,
                )
            )
    return reads


def _served(
    tool: str, arguments: dict[str, Any], output: dict[str, Any], index: _Index
) -> list[tuple[str | None, str | None, list[ArtifactVersion], str | None]]:
    if tool in {"read_artifact", "grep_artifact"}:
        return [_by_artifact(arguments.get("artifact_id"), index)]
    if tool == "diff_declared_artifacts":
        return [
            _by_artifact(arguments.get("base_artifact_id"), index),
            _by_artifact(arguments.get("draft_artifact_id"), index),
        ]
    path = arguments.get("path")
    if not isinstance(path, str):
        return [(None, None, [], "the call named no path")]
    lines = output.get("lines")
    offset = output.get("offset", 0)
    if not isinstance(lines, list) or not isinstance(offset, int):
        return [(path, None, [], "the result holds no line window")]
    returned = [item.get("text") for item in lines if isinstance(item, dict)]
    matches = [
        version
        for version in index.by_path.get(path, [])
        if _window(index.store, version, offset, len(returned)) == returned
    ]
    why = None if matches else f'no recorded version of "{path}" has the lines that were returned'
    return [(path, None, matches, why)]


def _by_artifact(
    artifact_id: object, index: _Index
) -> tuple[str | None, str | None, list[ArtifactVersion], str | None]:
    if not isinstance(artifact_id, str):
        return (None, None, [], "the call named no artifact id")
    versions = index.by_artifact.get(artifact_id, [])
    why = None if versions else f'no write of artifact "{artifact_id}" is recorded'
    return (versions[0].relative_path if versions else None, artifact_id, versions, why)


def _window(store: ArtifactStore, version: ArtifactVersion, offset: int, count: int) -> list[str]:
    text = store.read_bytes(version.sha256).decode("utf-8", errors="replace")
    return split_lines(text)[offset : offset + count]


def _origin_decision(run_root: Path, origin: DefectWriter) -> DecisionRef | None:
    if origin.agent_run_id is None or origin.tool_call_id is None:
        return None
    audit = (
        AuditTranscriptStore(run_root, read_only=True)
        if (run_root / ".agent-audit-logs").is_dir()
        else None
    )
    try:
        return link_call(audit, origin.agent_run_id, origin.tool_call_id, origin.tool or "write")
    finally:
        if audit is not None:
            audit.close()


def _summary(
    run_id: str,
    ok: bool,
    hops: list[LineageHop],
    origin: DefectWriter | None,
    decision: DecisionRef | None,
    reason: str,
) -> str:
    if ok:
        return f'No input read by run "{run_id}" fails the check ({reason})'
    first = hops[0].defective_read
    assert first is not None
    text = (
        f'Run "{run_id}" read a defective version of {first.path or first.artifact_id} '
        f'({first.tool} call "{first.tool_call_id}"); {len(hops)} hop(s) upstream'
    )
    if origin is None:
        return f"{text}, no single writer is identified: {reason}"
    where = f'version written by "{origin.agent_identity or origin.agent_run_id or "unknown"}"'
    if origin.iteration is not None:
        where += f" in iteration {origin.iteration}"
    text += f", the trace ends at the {where}. {reason}"
    return f"{text} {decision_sentence(decision)}" if decision is not None else text
