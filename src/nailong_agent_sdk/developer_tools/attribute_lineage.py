# Copyright (c) 2026 David Michael Indraputra

"""Trace a failing run back through what it read to the write or the retrieval that began it.

Everything this module reports is one of two kinds, and says which. An *observed* fact is in the
records: this run called this tool at this position, and the call returned this. An *inferred*
relation is something a reader might suspect, such as a write whose text repeats a line of an
earlier retrieval; it is produced only when asked for, only from exact string containment and
never from similarity, and it never claims causation. A relation that merely says one thing came
before another is observed and carries ``causal=False`` too: the records do not show what the
model used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.hashing import canonical_json, sha256_hex
from ..foundations.text import split_lines
from ..memory.context_projection import FileToolResultJournal
from ..observability.audit_log import AuditTranscriptStore
from ..tools.artifacts import ArtifactStore, ArtifactVersion
from .attribute_defect import DefectWriter, _writer
from .decision_links import DecisionRef, decision_sentence, link_call

LINEAGE_SCHEMA_VERSION = "agent-sdk-lineage-attribution-v2"
DEFAULT_MAX_DEPTH = 8
DEFAULT_MIN_VERBATIM_CHARS = 40
MAX_LISTED_RELATIONS = 50
READ_TOOLS = frozenset({"read_artifact", "grep_artifact", "read_file", "diff_declared_artifacts"})
WRITE_TOOLS = frozenset({"write_draft", "edit_draft", "notebook_edit"})
WEB_FETCH_TOOLS = frozenset({"web_fetch", "render_pdf_page"})
_PROCESS_KEYS = frozenset({"template_name", "output", "exit_kind"})

type ContentCheck = Callable[[str, bytes], bool]
type ResultCheck = Callable[[str, dict[str, Any], dict[str, Any]], bool]


class InputKind(StrEnum):
    ARTIFACT = "artifact"
    STORED_RESULT = "stored-result"
    PROCESS_OUTPUT = "process-output"
    WEB_FETCH = "web-fetch"
    WEB_SEARCH = "web-search"


class FlowBasis(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"


class FlowRelationKind(StrEnum):
    PRECEDES = "precedes"
    VERBATIM_CONTAINMENT = "verbatim-containment"


class OriginKind(StrEnum):
    ARTIFACT_WRITE = "artifact-write"
    EXTERNAL_INPUT = "external-input"
    NONE = "none"


class InputRef(StrictModel):
    """One retrieval a run made that brought data into its context, as the journal recorded it."""

    kind: InputKind
    basis: FlowBasis = FlowBasis.OBSERVED
    position: int = Field(ge=1)
    handle_id: str
    tool: str
    tool_call_id: str
    source: str | None = None
    content_sha256: str
    derived_from_handle: str | None = None
    derived_from_tool: str | None = None
    derived_from_call_id: str | None = None
    defective: bool = False


class FlowRelation(StrictModel):
    """How an input and a later write of the same run relate, and on what evidence."""

    kind: FlowRelationKind
    basis: FlowBasis
    causal: bool = False
    input_call_id: str
    write_call_id: str
    write_path: str | None = None
    detail: str


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
    """A run on the path, the defective input that led out of it and where it came from."""

    run_id: str
    reads: int = Field(ge=0)
    defective_read: LineageRead | None = None
    defective_input: InputRef | None = None
    ambiguous: bool = False


class LineageAttribution(StrictModel):
    """Where a defect entered the data a run consumed, and what recorded how."""

    schema_version: str = LINEAGE_SCHEMA_VERSION
    run_id: str
    ok: bool
    hops: list[LineageHop] = Field(default_factory=list)
    origin_kind: OriginKind = OriginKind.NONE
    origin: DefectWriter | None = None
    origin_input: InputRef | None = None
    origin_decision: DecisionRef | None = None
    relations: list[FlowRelation] = Field(default_factory=list)
    origin_reason: str
    summary: str


@dataclass(frozen=True)
class _Index:
    store: ArtifactStore
    by_artifact: dict[str, list[ArtifactVersion]]
    by_path: dict[str, list[ArtifactVersion]]


@dataclass(frozen=True)
class _Entry:
    position: int
    handle: str
    tool: str
    call_id: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    @property
    def output(self) -> dict[str, Any]:
        output = self.result.get("output")
        return output if isinstance(output, dict) else {}

    @property
    def succeeded(self) -> bool:
        return self.result.get("status") == "succeeded"


def attribute_lineage(
    run_root: Path,
    workspace: Path,
    run_id: str,
    is_defective: ContentCheck,
    *,
    is_defective_result: ResultCheck | None = None,
    infer_verbatim: bool = False,
    min_verbatim_chars: int = DEFAULT_MIN_VERBATIM_CHARS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> LineageAttribution:
    """Follow the defective inputs of ``run_id`` upstream, reading only.

    A hop is the first journaled input of a run that fails a check: an artifact version that
    fails ``is_defective``, or, when ``is_defective_result`` is given, a retrieved result (the
    output of a registered command, a fetched page, a search, or a stored result fetched again
    with ``get_tool_result``) that fails it. An artifact version names the run that wrote it, and
    the trace goes on there. A stored result names the run that recorded it, and the trace goes on
    there. A command output, a page or a search is data from outside the SDK's records, so the
    trace ends at the call that retrieved it. It also ends at the writer of a version whose own
    run read nothing defective, at a version several writes could have produced, at a writer or
    a stored result with no known run, and at ``max_depth`` hops.
    """

    if max_depth < 1:
        raise ValueError(f"max_depth must be at least 1, got {max_depth}.")
    if min_verbatim_chars < 1:
        raise ValueError(f"min_verbatim_chars must be at least 1, got {min_verbatim_chars}.")
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
    origin_input: InputRef | None = None
    origin_kind = OriginKind.NONE
    relations: list[FlowRelation] = []
    reason = ""
    while True:
        seen.add(current)
        entries = _entries(journal, current)
        reads = _reads(journal, index, current, is_defective)
        inputs = _inputs(journal, entries, is_defective_result)
        first_read = next((item for item in reads if item.defective), None)
        first_input = next((item for item in inputs if item.defective), None)
        if first_input is not None and (
            first_read is None or first_input.position < first_read.position
        ):
            hops.append(
                LineageHop(
                    run_id=current,
                    reads=len(reads) + len(inputs),
                    defective_input=first_input,
                )
            )
            if first_input.kind is not InputKind.STORED_RESULT:
                origin_kind, origin_input = OriginKind.EXTERNAL_INPUT, first_input
                relations = _relations(entries, first_input, infer_verbatim, min_verbatim_chars)
                reason = (
                    f"The defective {first_input.kind.value} came from call "
                    f'"{first_input.tool_call_id}" ({first_input.tool}) of run "{current}"; '
                    "its content comes from outside the SDK's records, so the trace ends at the "
                    "call that retrieved it."
                )
                break
            owner = journal.owner_of(first_input.derived_from_handle or "")
            origin_input = first_input
            if owner is None or owner in seen:
                reason = (
                    f'Run "{current}" fetched stored result "{first_input.derived_from_handle}" '
                    "and the run that recorded it is not identified."
                    if owner is None
                    else f'The trace returned to run "{owner}".'
                )
                break
            if len(hops) >= max_depth:
                reason = f"The trace stopped at the depth limit of {max_depth} hops."
                break
            origin_input = None
            current = owner
            continue
        if first_read is None:
            reason = (
                f'Run "{current}" read no version that fails the check and made no retrieval that '
                "fails the result check, so the defect began in its own output."
                if hops
                else f'Run "{run_id}" read no version that fails the check and made no '
                "retrieval that fails the result check."
            )
            break
        ambiguous = not first_read.unique
        hops.append(
            LineageHop(
                run_id=current,
                reads=len(reads) + len(inputs),
                defective_read=first_read,
                ambiguous=ambiguous,
            )
        )
        if ambiguous:
            reason = (
                f"{len(first_read.candidates)} writes produced the defective version read by "
                f'"{current}"; the records cannot tell which one was served.'
            )
            break
        writer = first_read.candidates[0]
        origin, origin_kind = writer, OriginKind.ARTIFACT_WRITE
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
    decision = None
    if origin_kind is OriginKind.ARTIFACT_WRITE and origin is not None:
        decision = _call_decision(run_root, origin.agent_run_id, origin.tool_call_id, origin.tool)
    elif origin_kind is OriginKind.EXTERNAL_INPUT and origin_input is not None:
        decision = _call_decision(run_root, current, origin_input.tool_call_id, origin_input.tool)
    return LineageAttribution(
        run_id=run_id,
        ok=ok,
        hops=hops,
        origin_kind=origin_kind,
        origin=origin if origin_kind is OriginKind.ARTIFACT_WRITE else None,
        origin_input=origin_input,
        origin_decision=decision,
        relations=relations[:MAX_LISTED_RELATIONS],
        origin_reason=reason,
        summary=_summary(run_id, ok, hops, origin_kind, origin, origin_input, decision, reason),
    )


def collect_inputs(run_root: Path, run_id: str) -> list[InputRef]:
    """The typed retrievals a run made, as the journal recorded them, in the order they ran."""

    if not (run_root / ".agent-tool-results").is_dir():
        raise ValueError(
            f'"{run_root}" holds no .agent-tool-results directory: pass the run root of the '
            "service that ran the agent."
        )
    journal = FileToolResultJournal(run_root, read_only=True)
    return _inputs(journal, _entries(journal, run_id), None)


def _entries(journal: FileToolResultJournal, run_id: str) -> list[_Entry]:
    entries: list[_Entry] = []
    for position, handle in enumerate(journal.handles_of(run_id), start=1):
        record = journal.read(handle)
        call, result = record.get("call"), record.get("result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            continue
        arguments = call.get("arguments")
        entries.append(
            _Entry(
                position=position,
                handle=handle,
                tool=str(call.get("name")),
                call_id=str(call.get("id")),
                arguments=arguments if isinstance(arguments, dict) else {},
                result=result,
            )
        )
    return entries


def _inputs(
    journal: FileToolResultJournal, entries: list[_Entry], check: ResultCheck | None
) -> list[InputRef]:
    refs: list[InputRef] = []
    for entry in entries:
        ref = _classify(journal, entry)
        if ref is None:
            continue
        if check is not None:
            subject = _checked_subject(journal, entry, ref)
            ref = ref.model_copy(update={"defective": bool(check(*subject))})
        refs.append(ref)
    return refs


def _classify(journal: FileToolResultJournal, entry: _Entry) -> InputRef | None:
    output = entry.output
    tool = entry.tool
    base = {
        "position": entry.position,
        "handle_id": entry.handle,
        "tool": tool,
        "tool_call_id": entry.call_id,
        "content_sha256": sha256_hex(canonical_json(output)),
    }
    if tool == "run_registered_command" or _PROCESS_KEYS <= output.keys():
        source = output.get("template_name") or entry.arguments.get("template_name")
        return InputRef(kind=InputKind.PROCESS_OUTPUT, source=_text(source), **base)
    if not entry.succeeded:
        return None
    if tool in WEB_FETCH_TOOLS:
        return InputRef(kind=InputKind.WEB_FETCH, source=_text(entry.arguments.get("url")), **base)
    if tool == "web_search":
        return InputRef(
            kind=InputKind.WEB_SEARCH, source=_text(entry.arguments.get("query")), **base
        )
    if tool == "get_tool_result":
        handle = _text(entry.arguments.get("handle_id"))
        original = _original(journal, handle)
        call = original.get("call") if original else None
        return InputRef(
            kind=InputKind.STORED_RESULT,
            source=handle,
            derived_from_handle=handle,
            derived_from_tool=_text(call.get("name")) if isinstance(call, dict) else None,
            derived_from_call_id=_text(call.get("id")) if isinstance(call, dict) else None,
            **base,
        )
    return None


def _original(journal: FileToolResultJournal, handle: str | None) -> dict[str, Any] | None:
    if handle is None:
        return None
    try:
        return journal.read(handle)
    except (ValueError, OSError):
        return None


def _checked_subject(
    journal: FileToolResultJournal, entry: _Entry, ref: InputRef
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if ref.kind is InputKind.STORED_RESULT:
        original = _original(journal, ref.derived_from_handle)
        call = original.get("call") if original else None
        result = original.get("result") if original else None
        if isinstance(call, dict) and isinstance(result, dict):
            arguments = call.get("arguments")
            return (
                str(call.get("name")),
                arguments if isinstance(arguments, dict) else {},
                result,
            )
    return entry.tool, entry.arguments, entry.result


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _relations(
    entries: list[_Entry], ref: InputRef, infer_verbatim: bool, min_chars: int
) -> list[FlowRelation]:
    found: list[FlowRelation] = []
    lines = _input_lines(entries, ref, min_chars) if infer_verbatim else []
    for entry in entries:
        if entry.tool not in WRITE_TOOLS or entry.position <= ref.position:
            continue
        path = _text(entry.arguments.get("path"))
        found.append(
            FlowRelation(
                kind=FlowRelationKind.PRECEDES,
                basis=FlowBasis.OBSERVED,
                input_call_id=ref.tool_call_id,
                write_call_id=entry.call_id,
                write_path=path,
                detail=(
                    f'Call "{ref.tool_call_id}" ({ref.tool}) ran before call "{entry.call_id}" '
                    f"({entry.tool}); the records do not show whether the write used it."
                ),
            )
        )
        written = _written_text(entry)
        copied = [line for line in lines if line in written]
        if copied:
            found.append(
                FlowRelation(
                    kind=FlowRelationKind.VERBATIM_CONTAINMENT,
                    basis=FlowBasis.INFERRED,
                    input_call_id=ref.tool_call_id,
                    write_call_id=entry.call_id,
                    write_path=path,
                    detail=(
                        f"{len(copied)} line(s) of at least {min_chars} characters of the "
                        "retrieved text appear in the written text as an exact match; an exact "
                        "match is a hint, not proof that the write came from the retrieval."
                    ),
                )
            )
    return found


def _written_text(entry: _Entry) -> str:
    parts = [entry.arguments.get(name) for name in ("content", "new_text", "source")]
    return "\n".join(part for part in parts if isinstance(part, str))


def _input_lines(entries: list[_Entry], ref: InputRef, min_chars: int) -> list[str]:
    source = next((entry for entry in entries if entry.handle == ref.handle_id), None)
    if source is None:
        return []
    strings: list[str] = []
    _collect_strings(source.output, strings)
    lines = {
        line.strip()
        for text in strings
        for line in split_lines(text)
        if len(line.strip()) >= min_chars
    }
    return sorted(lines)


def _collect_strings(value: Any, into: list[str]) -> None:
    if isinstance(value, str):
        into.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, into)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, into)


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
    for entry in _entries(journal, run_id):
        if entry.tool not in READ_TOOLS or not entry.succeeded:
            continue
        base = {
            "position": entry.position,
            "handle_id": entry.handle,
            "tool": entry.tool,
            "tool_call_id": entry.call_id,
        }
        for path, artifact_id, versions, why in _served(
            entry.tool, entry.arguments, entry.output, index
        ):
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


def _call_decision(
    run_root: Path, run_id: str | None, call_id: str | None, tool: str | None
) -> DecisionRef | None:
    if run_id is None or call_id is None:
        return None
    audit = (
        AuditTranscriptStore(run_root, read_only=True)
        if (run_root / ".agent-audit-logs").is_dir()
        else None
    )
    try:
        return link_call(audit, run_id, call_id, tool or "write")
    finally:
        if audit is not None:
            audit.close()


def _summary(
    run_id: str,
    ok: bool,
    hops: list[LineageHop],
    origin_kind: OriginKind,
    origin: DefectWriter | None,
    origin_input: InputRef | None,
    decision: DecisionRef | None,
    reason: str,
) -> str:
    if ok:
        return (
            f'No input read by run "{run_id}" fails the check ({reason}); only the inputs the '
            "journal recorded were checked."
        )
    first = hops[0]
    if first.defective_read is not None:
        subject = (
            f"a defective version of "
            f"{first.defective_read.path or first.defective_read.artifact_id} "
            f'({first.defective_read.tool} call "{first.defective_read.tool_call_id}")'
        )
    else:
        assert first.defective_input is not None
        subject = (
            f"a defective {first.defective_input.kind.value} "
            f'({first.defective_input.tool} call "{first.defective_input.tool_call_id}")'
        )
    text = f'Run "{run_id}" read {subject}; {len(hops)} hop(s) upstream'
    if origin_kind is OriginKind.ARTIFACT_WRITE and origin is not None:
        where = f'version written by "{origin.agent_identity or origin.agent_run_id or "unknown"}"'
        if origin.iteration is not None:
            where += f" in iteration {origin.iteration}"
        text += f", the trace ends at the {where}. {reason}"
    elif origin_kind is OriginKind.EXTERNAL_INPUT and origin_input is not None:
        retrieved = f'{origin_input.kind.value} call "{origin_input.tool_call_id}"'
        text += f", the trace ends at {retrieved}. {reason}"
    else:
        return f"{text}, no single origin is identified: {reason}"
    return f"{text} {decision_sentence(decision)}" if decision is not None else text
