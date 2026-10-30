# Copyright (c) 2026 David Michael Indraputra

"""Rank where a failed run began, from what stopped it back to the earliest checked origin."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.suffix_search import SuffixStrategy
from ..memory.context_projection import FileToolResultJournal
from ..tools.artifacts import ArtifactStore, ArtifactVersion
from .attribute_defect import DefectWriter, _writer, attribute_defect, content_check
from .attribute_failure import FailureAttribution, RootAttribution, attribute_failure
from .attribute_lineage import ContentCheck, InputRef, OriginKind, ResultCheck, attribute_lineage
from .attribute_result import attribute_result_defect
from .decision_links import DecisionRef

ROOT_CAUSE_SCHEMA_VERSION = "agent-sdk-root-cause-v1"
DEFAULT_MAX_RUNS = 5
MAX_CANDIDATES = 50

type DefectCheck = Callable[[bytes, ArtifactVersion], bool]
_NO_VERSION = cast(ArtifactVersion, None)


class CauseKind(StrEnum):
    RECORD_INCONSISTENCY = "record-inconsistency"
    UPSTREAM_ORIGIN = "upstream-origin"
    DEFECTIVE_WRITE = "defective-write"
    DEFECTIVE_RESULT = "defective-result"
    NODE_FAILURE = "node-failure"
    FIRST_ERROR = "first-error"
    RUN_TERMINAL = "run-terminal"


class CauseBasis(StrEnum):
    RECORDED = "recorded"
    CHECKED_EXACT = "checked-exact"
    CHECKED_ASSUMED = "checked-assumed"
    UNCHECKED = "unchecked"
    TRACED = "traced"
    AMBIGUOUS = "ambiguous"


_DEPTH = {
    CauseKind.RECORD_INCONSISTENCY: -1,
    CauseKind.UPSTREAM_ORIGIN: 0,
    CauseKind.DEFECTIVE_WRITE: 1,
    CauseKind.DEFECTIVE_RESULT: 2,
    CauseKind.NODE_FAILURE: 3,
    CauseKind.FIRST_ERROR: 4,
    CauseKind.RUN_TERMINAL: 5,
}
_PRIMARY_BASES = frozenset({CauseBasis.RECORDED, CauseBasis.CHECKED_EXACT, CauseBasis.TRACED})


class RootCauseCandidate(StrictModel):
    """One place a failure may have begun, how it was established and what ties it to the stop."""

    rank: int = Field(ge=1)
    kind: CauseKind
    basis: CauseBasis
    subject: str
    run_id: str | None = None
    summary: str
    responsibility: float | None = None
    decision: DecisionRef | None = None
    writer: DefectWriter | None = None
    input: InputRef | None = None
    evidence: list[str] = Field(default_factory=list)
    position: int | None = Field(default=None, ge=1)


class RootCauseAnalysis(StrictModel):
    schema_version: str = ROOT_CAUSE_SCHEMA_VERSION
    run_id: str
    verdict: str
    ok: bool
    evidence_intact: bool | None = None
    primary: RootCauseCandidate | None = None
    primary_reason: str
    candidates: list[RootCauseCandidate] = Field(default_factory=list)
    candidates_omitted: int = Field(default=0, ge=0)
    not_examined: list[str] = Field(default_factory=list)
    summary: str


def attribute_root_cause(
    run_root: Path,
    run_id: str,
    *,
    workspace: Path | None = None,
    artifact_checks: Mapping[str, DefectCheck] | None = None,
    content_check: ContentCheck | None = None,
    result_check: ResultCheck | None = None,
    strategy: SuffixStrategy | str = SuffixStrategy.CONFIRMED,
    max_runs: int = DEFAULT_MAX_RUNS,
    assume_fault: bool = False,
    verify_records: bool = False,
    related_runs: Sequence[str] = (),
    trace_unchecked: bool = False,
) -> RootCauseAnalysis:
    if max_runs < 1:
        raise ValueError(f"max_runs must be at least 1, got {max_runs}.")
    if (artifact_checks or content_check is not None) and workspace is None:
        raise ValueError(
            "artifact_checks and content_check search the artifact write history, which lives "
            "in a workspace: pass workspace=<the directory that holds .agent-artifacts>."
        )
    failure = attribute_failure(run_root, run_id)
    not_examined: list[str] = []
    if failure.ok and not assume_fault:
        return RootCauseAnalysis(
            run_id=run_id,
            verdict=failure.verdict,
            ok=True,
            evidence_intact=failure.evidence_intact,
            primary_reason="The run completed and its ledgers are intact, so there is no fault.",
            summary=failure.summary,
        )
    candidates = _stop_candidates(failure, max_runs)
    investigated = _investigated_runs(failure, run_id, max_runs)
    if result_check is None:
        not_examined.append("No result check was given, so no tool result was scanned.")
    if workspace is None or not (artifact_checks or content_check is not None):
        not_examined.append(
            "No workspace with an artifact check or content check was given, so no write history "
            "was searched and no data was traced upstream."
        )
    traced_runs = list(dict.fromkeys([*investigated, *related_runs]))
    if content_check is not None and workspace is not None:
        for investigated_run in investigated:
            found, hops = _lineage_candidates(
                run_root, workspace, investigated_run, content_check, result_check, not_examined
            )
            candidates.extend(found)
            traced_runs.extend(item for item in hops if item not in traced_runs)
    if trace_unchecked and content_check is None:
        if workspace is None:
            not_examined.append(
                "trace_unchecked follows the artifacts a run read to the runs that wrote them, "
                "which lives in a workspace; none was given, so no writer was listed."
            )
        else:
            for investigated_run in investigated:
                candidates.extend(
                    _unchecked_candidates(run_root, workspace, investigated_run, not_examined)
                )
    if verify_records:
        if workspace is None:
            not_examined.append(
                "verify_records compares the journal with the artifact store, which lives in a "
                "workspace; none was given, so no record was compared."
            )
        else:
            for traced_run in traced_runs:
                candidates.extend(_record_candidates(run_root, workspace, traced_run))
    if result_check is not None:
        for traced_run in traced_runs:
            candidates.extend(_result_candidates(run_root, traced_run, result_check, not_examined))
    if workspace is not None and artifact_checks:
        for path, check in artifact_checks.items():
            candidates.extend(_write_candidates(workspace, path, check, strategy, not_examined))
    not_examined.append(
        "Why the model chose an action is not examined: its reasoning is not recorded, and "
        "nothing replays a run with a different decision."
    )
    if failure.evidence_intact is False:
        not_examined.append(
            "The ledgers fail their own integrity check, so every candidate rests on records "
            "that may have been altered."
        )
    ordered = _ranked(_positioned(run_root, candidates))
    primary, reason = _primary(ordered, failure.evidence_intact)
    listed = ordered[:MAX_CANDIDATES]
    return RootCauseAnalysis(
        run_id=run_id,
        verdict=failure.verdict,
        ok=False,
        evidence_intact=failure.evidence_intact,
        primary=primary,
        primary_reason=reason,
        candidates=listed,
        candidates_omitted=len(ordered) - len(listed),
        not_examined=not_examined,
        summary=_summary(run_id, failure.verdict, primary, reason, len(ordered)),
    )


def _investigated_runs(failure: FailureAttribution, run_id: str, max_runs: int) -> list[str]:
    if failure.graph is None:
        return [run_id]
    runs = [root.agent_run_id for root in failure.graph.roots if root.agent_run_id is not None]
    return list(dict.fromkeys(runs))[:max_runs]


def _stop_candidates(failure: FailureAttribution, max_runs: int) -> list[RootCauseCandidate]:
    if failure.graph is not None:
        return [_node_candidate(root) for root in failure.graph.roots[:max_runs]]
    found: list[RootCauseCandidate] = []
    terminal, first_error = failure.terminal, failure.first_error
    if terminal is not None:
        found.append(
            _candidate(
                CauseKind.RUN_TERMINAL,
                CauseBasis.RECORDED,
                terminal.subject or terminal.event_type,
                failure.run_id,
                f"The run ended at event {terminal.sequence}"
                f"{f' ({terminal.code})' if terminal.code else ''}: {terminal.message}",
                decision=failure.terminal_decision,
                evidence=[f"telemetry event {terminal.sequence} {terminal.event_type}"],
            )
        )
    if first_error is not None and first_error != terminal:
        found.append(
            _candidate(
                CauseKind.FIRST_ERROR,
                CauseBasis.RECORDED,
                first_error.subject or first_error.event_type,
                failure.run_id,
                f"The first error was event {first_error.sequence}"
                f"{f' ({first_error.code})' if first_error.code else ''}: {first_error.message}",
                decision=failure.first_error_decision,
                evidence=[f"telemetry event {first_error.sequence} {first_error.event_type}"],
            )
        )
    return found


def _node_candidate(root: RootAttribution) -> RootCauseCandidate:
    held = f"; it holds up {root.blocked_count} other node(s)" if root.blocked_count else ""
    code = f" ({root.failure_code})" if root.failure_code else ""
    evidence = [f"graph node {root.node_id} is {root.status}"]
    if root.released_alone:
        evidence.append(f"repairing it alone releases {', '.join(root.released_alone[:5])}")
    if root.also_requires:
        evidence.append(f"others also needed: {', '.join(root.also_requires[:5])}")
    return _candidate(
        CauseKind.NODE_FAILURE,
        CauseBasis.RECORDED,
        root.node_id,
        root.agent_run_id,
        f'Node "{root.node_id}" {root.status}{code}{held}: {root.reason or "no reason recorded"}',
        responsibility=root.responsibility,
        decision=root.agent_terminal_decision,
        evidence=evidence,
    )


def _result_candidates(
    run_root: Path, run_id: str, check: ResultCheck, not_examined: list[str]
) -> list[RootCauseCandidate]:
    try:
        found = attribute_result_defect(run_root, run_id, check)
    except ValueError as error:
        not_examined.append(f'Run "{run_id}": tool results were not scanned: {error}')
        return []
    if found.first_defective is None:
        return []
    item = found.first_defective
    return [
        _candidate(
            CauseKind.DEFECTIVE_RESULT,
            CauseBasis.CHECKED_EXACT,
            f"{item.tool} call {item.tool_call_id}",
            run_id,
            found.summary,
            decision=item.decision,
            position=item.position,
            evidence=[
                f"result {item.position} of {found.results} fails the check ({found.check})",
                f"{found.evaluations} result(s) examined in the order they were produced",
            ],
        )
    ]


def _record_candidates(run_root: Path, workspace: Path, run_id: str) -> list[RootCauseCandidate]:
    if (
        not (run_root / ".agent-tool-results").is_dir()
        or not (workspace / ".agent-artifacts").is_dir()
    ):
        return []
    journal = FileToolResultJournal(run_root, read_only=True)
    store = ArtifactStore(workspace, read_only=True)
    try:
        handles = journal.handles_of(run_id)
    except (OSError, ValueError):
        return []
    versions = [version for path in store.paths() for version in store.history(path)]
    by_id: dict[str, list[ArtifactVersion]] = {}
    for version in versions:
        by_id.setdefault(version.artifact_id, []).append(version)
    found: list[RootCauseCandidate] = []
    for position, handle in enumerate(handles, start=1):
        record = journal.read(handle)
        call, result = record.get("call"), record.get("result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            continue
        if result.get("status") != "succeeded":
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        call_id = str(call.get("id"))
        name = str(call.get("name"))
        if name == "read_artifact" and isinstance(output.get("content"), str):
            held = by_id.get(str(output.get("artifact_id") or arguments.get("artifact_id")), [])
            if held and all(
                store.read_bytes(item.sha256).decode("utf-8", errors="replace") != output["content"]
                for item in held
            ):
                found.append(
                    _candidate(
                        CauseKind.RECORD_INCONSISTENCY,
                        CauseBasis.RECORDED,
                        f"{name} call {call_id}",
                        run_id,
                        f'The content that read_artifact call "{call_id}" of run "{run_id}" '
                        f'returned differs from the stored artifact "{held[-1].relative_path}" '
                        "it names.",
                        position=position,
                        evidence=[
                            f"result {position} of the run's journal",
                            "the journal holds what the agent was shown; the artifact store "
                            "holds what exists",
                        ],
                    )
                )
        if name == "write_draft" and isinstance(arguments.get("content"), str):
            written = [
                item
                for item in store.history(str(arguments.get("path", "")))
                if item.manifest.get("tool_call_id") == call_id
                and item.manifest.get("agent_run_id") == run_id
            ]
            if written and all(
                store.read_bytes(item.sha256).decode("utf-8", errors="replace")
                != arguments["content"]
                for item in written
            ):
                found.append(
                    _candidate(
                        CauseKind.RECORD_INCONSISTENCY,
                        CauseBasis.RECORDED,
                        f"{name} call {call_id}",
                        run_id,
                        f'The file "{written[-1].relative_path}" stored by write_draft call '
                        f'"{call_id}" of run "{run_id}" differs from the content the agent '
                        "asked to write.",
                        writer=_writer(written[-1]),
                        position=position,
                        evidence=[
                            f"call {position} of the run's journal",
                            "the call's arguments hold what the agent asked for; the artifact "
                            "store holds what was written",
                        ],
                    )
                )
    return found


def _write_candidates(
    workspace: Path,
    path: str,
    check: DefectCheck,
    strategy: SuffixStrategy | str,
    not_examined: list[str],
) -> list[RootCauseCandidate]:
    try:
        found = attribute_defect(workspace, path, check, strategy=strategy)
    except ValueError as error:
        not_examined.append(f'Artifact "{path}": its write history was not searched: {error}')
        return []
    if found.ok or found.introduced_by is None:
        return []
    basis = CauseBasis.CHECKED_EXACT if found.proven else CauseBasis.CHECKED_ASSUMED
    evidence = [
        f"{found.evaluations} version(s) examined with strategy {found.strategy}",
        "the probes prove the boundary" if found.proven else "the boundary is not proven",
    ]
    if found.born_defective:
        evidence.append("the first version was already defective")
    return [
        _candidate(
            CauseKind.DEFECTIVE_WRITE,
            basis,
            path,
            found.introduced_by.agent_run_id,
            found.summary,
            writer=found.introduced_by,
            evidence=evidence,
        )
    ]


def _lineage_candidates(
    run_root: Path,
    workspace: Path,
    run_id: str,
    content_check: ContentCheck,
    result_check: ResultCheck | None,
    not_examined: list[str],
) -> tuple[list[RootCauseCandidate], list[str]]:
    try:
        traced = attribute_lineage(
            run_root,
            workspace,
            run_id,
            content_check,
            is_defective_result=result_check,
        )
    except ValueError as error:
        not_examined.append(f'Run "{run_id}": data was not traced upstream: {error}')
        return [], []
    if traced.ok:
        return [], []
    ambiguous = any(hop.ambiguous for hop in traced.hops)
    if traced.origin_kind is OriginKind.NONE and not ambiguous:
        not_examined.append(
            f'Run "{run_id}": the data trace found a defective input but no origin: '
            f"{traced.origin_reason}"
        )
        return [], []
    inferred = [item for item in traced.relations if item.basis.value == "inferred"]
    evidence = [
        f"{len(traced.hops)} hop(s) from run {run_id}",
        traced.origin_reason,
        *(item.detail for item in inferred[:3]),
    ]
    if traced.origin is not None:
        subject = traced.origin.tool_call_id or run_id
    elif traced.origin_input is not None:
        subject = traced.origin_input.tool_call_id
    else:
        read = traced.hops[-1].defective_read
        subject = read.tool_call_id if read is not None else run_id
    hop_runs = [hop.run_id for hop in traced.hops]
    if traced.origin is not None and traced.origin.agent_run_id is not None:
        hop_runs.append(traced.origin.agent_run_id)
    candidate = [
        _candidate(
            CauseKind.UPSTREAM_ORIGIN,
            CauseBasis.AMBIGUOUS if ambiguous else CauseBasis.TRACED,
            subject,
            traced.origin.agent_run_id if traced.origin is not None else run_id,
            traced.summary,
            decision=traced.origin_decision,
            writer=traced.origin,
            origin_input=traced.origin_input,
            evidence=evidence,
        )
    ]
    return candidate, hop_runs


def _unchecked_candidates(
    run_root: Path, workspace: Path, run_id: str, not_examined: list[str]
) -> list[RootCauseCandidate]:
    try:
        traced = attribute_lineage(run_root, workspace, run_id, lambda path, content: True)
    except ValueError as error:
        not_examined.append(f'Run "{run_id}": upstream writers were not listed: {error}')
        return []
    found: list[RootCauseCandidate] = []
    for hop in traced.hops:
        read = hop.defective_read
        if read is None or not read.unique:
            continue
        writer = read.candidates[0]
        if writer.agent_run_id is None or writer.tool_call_id is None:
            continue
        found.append(
            _candidate(
                CauseKind.UPSTREAM_ORIGIN,
                CauseBasis.UNCHECKED,
                writer.tool_call_id,
                writer.agent_run_id,
                f'Run "{hop.run_id}" read "{read.path}", written by call "{writer.tool_call_id}" '
                f'of run "{writer.agent_run_id}"; nothing checked that file, so it is listed as '
                "a place the failure may have come from, not as a finding.",
                writer=writer,
                evidence=[f"journaled read {read.position} of run {hop.run_id}"],
            )
        )
    return found


def _candidate(
    kind: CauseKind,
    basis: CauseBasis,
    subject: str,
    run_id: str | None,
    summary: str,
    *,
    responsibility: float | None = None,
    decision: DecisionRef | None = None,
    writer: DefectWriter | None = None,
    origin_input: InputRef | None = None,
    evidence: list[str] | None = None,
    position: int | None = None,
) -> RootCauseCandidate:
    return RootCauseCandidate(
        rank=1,
        kind=kind,
        basis=basis,
        subject=subject,
        run_id=run_id,
        summary=summary,
        responsibility=responsibility,
        decision=decision,
        writer=writer,
        input=origin_input,
        evidence=evidence or [],
        position=position,
    )


def _positioned(run_root: Path, candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
    if not any(item.writer is not None for item in candidates):
        return candidates
    journal = FileToolResultJournal(run_root, read_only=True)
    positions: dict[tuple[str, str], int] = {}
    enriched: list[RootCauseCandidate] = []
    for item in candidates:
        writer = item.writer
        if writer is None or writer.agent_run_id is None or writer.tool_call_id is None:
            enriched.append(item)
            continue
        key = (writer.agent_run_id, writer.tool_call_id)
        if key not in positions:
            try:
                handles = journal.handles_of(writer.agent_run_id)
            except (OSError, ValueError):
                handles = []
            for position, handle in enumerate(handles, start=1):
                call = journal.read(handle).get("call")
                if isinstance(call, dict) and call.get("id") == writer.tool_call_id:
                    positions[key] = position
                    break
        enriched.append(
            item.model_copy(update={"position": positions[key]}) if key in positions else item
        )
    return enriched


def _depth(item: RootCauseCandidate, candidates: list[RootCauseCandidate]) -> float:
    depth = float(_DEPTH[item.kind])
    if item.kind is not CauseKind.DEFECTIVE_RESULT or item.position is None:
        return depth
    for other in candidates:
        if (
            other.kind in {CauseKind.UPSTREAM_ORIGIN, CauseKind.DEFECTIVE_WRITE}
            and other.writer is not None
            and other.writer.agent_run_id == item.run_id
            and other.position is not None
            and item.position < other.position
        ):
            depth = min(depth, _DEPTH[other.kind] - 0.5)
    return depth


def _ranked(candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.basis not in _PRIMARY_BASES,
            _depth(item, candidates),
            -(item.responsibility or 0.0),
            item.writer.written_at_utc if item.writer is not None else "",
            item.subject,
        ),
    )
    return [item.model_copy(update={"rank": position}) for position, item in enumerate(ordered, 1)]


def _primary(
    ordered: list[RootCauseCandidate], intact: bool | None
) -> tuple[RootCauseCandidate | None, str]:
    if not ordered:
        return None, "Nothing in the ledgers names a cause: no stop or error finding was recorded."
    first = ordered[0]
    if first.basis not in _PRIMARY_BASES:
        return None, (
            f"The best-supported candidate rests on {first.basis.value} evidence, which is not "
            "enough to name an origin; the candidates are listed for a person to judge."
        )
    stem = (
        "Ranked by how far upstream the evidence reaches among recorded, exactly checked and "
        "traced candidates; this orders facts and does not prove that one caused the stop."
    )
    if intact is False:
        stem += " The ledgers fail their integrity check."
    return first, stem


def _summary(
    run_id: str,
    verdict: str,
    primary: RootCauseCandidate | None,
    reason: str,
    count: int,
) -> str:
    if primary is None:
        return f'Run "{run_id}" {verdict}: {count} candidate(s), none named as the origin. {reason}'
    return (
        f'Run "{run_id}" {verdict}: the earliest supported origin is the {primary.kind.value} '
        f"{primary.subject} ({primary.basis.value}) among {count} candidate(s). {primary.summary}"
    )


def text_checks(
    *,
    contains: str | None = None,
    lacks: str | None = None,
    matches: str | None = None,
    sha256: str | None = None,
) -> tuple[DefectCheck, ContentCheck, str]:
    check, description = content_check(
        contains=contains, lacks=lacks, matches=matches, sha256=sha256
    )
    return check, (lambda path, content: check(content, _NO_VERSION)), description
