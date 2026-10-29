# Copyright (c) 2026 David Michael Indraputra

"""Rank where a failed run began, from what stopped it back to the earliest checked origin."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.suffix_search import SuffixStrategy
from ..tools.artifacts import ArtifactVersion
from .attribute_defect import DefectWriter, attribute_defect, content_check
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
    TRACED = "traced"
    AMBIGUOUS = "ambiguous"


_DEPTH = {
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
    if failure.ok:
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
    for investigated_run in investigated:
        if result_check is not None:
            candidates.extend(
                _result_candidates(run_root, investigated_run, result_check, not_examined)
            )
        if content_check is not None and workspace is not None:
            candidates.extend(
                _lineage_candidates(
                    run_root, workspace, investigated_run, content_check, result_check, not_examined
                )
            )
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
    ordered = _ranked(candidates)
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
            evidence=[
                f"result {item.position} of {found.results} fails the check ({found.check})",
                f"{found.evaluations} result(s) examined in the order they were produced",
            ],
        )
    ]


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
) -> list[RootCauseCandidate]:
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
        return []
    if traced.ok:
        return []
    ambiguous = any(hop.ambiguous for hop in traced.hops)
    if traced.origin_kind is OriginKind.NONE and not ambiguous:
        not_examined.append(
            f'Run "{run_id}": the data trace found a defective input but no origin: '
            f"{traced.origin_reason}"
        )
        return []
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
    return [
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
    )


def _ranked(candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.basis not in _PRIMARY_BASES,
            _DEPTH[item.kind],
            -(item.responsibility or 0.0),
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
