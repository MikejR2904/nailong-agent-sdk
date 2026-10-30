# Copyright (c) 2026 David Michael Indraputra

"""Ground truth for one run: the recorder's events plus each artifact's true provenance chain."""

from __future__ import annotations

from .cases import Case
from .schema import ArtifactTruth, Event, Kind, RunTruth


def build_truth(case: Case, events: list[Event]) -> RunTruth:
    calls = {event.call_id: event for event in events if event.kind is Kind.TOOL_CALL}
    artifacts: dict[str, ArtifactTruth] = {}
    for path, sources in case.sources().items():
        writes = [event for event in events if event.kind is Kind.WRITE and event.path == path]
        if not writes:
            continue
        last = writes[-1]
        call = calls.get(last.call_id)
        if call is None or last.digest is None or call.agent_id is None or call.model_index is None:
            continue
        artifacts[path] = ArtifactTruth(
            path=path,
            digest=last.digest,
            call_id=str(last.call_id),
            agent_id=call.agent_id,
            model_index=call.model_index,
            sources=sources,
        )
    return RunTruth(case.case_id, events, artifacts)
