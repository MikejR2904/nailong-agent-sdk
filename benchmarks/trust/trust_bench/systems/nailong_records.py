# Copyright (c) 2026 David Michael Indraputra

"""Map the SDK's own stores into the common event schema.

The stores read are the audit transcript, the tool-result journal (full calls and results,
whatever the audit bound cut) and the artifact store with its write history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.audit_log import AuditLogEntry, AuditTranscriptStore
from nailong_agent_sdk.tools.artifacts import ArtifactStore

from ..schema import ArtifactVersion, Event, Kind, SystemRecords, canonical, digest_of
from ..wire import decision_payload
from .nailong import SdkRun

Journaled = dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]


def _payload(entry: AuditLogEntry) -> Mapping[str, Any]:
    return entry.payload or {}


def _truncated(entry: AuditLogEntry) -> bool:
    return bool(_payload(entry).get("truncated"))


def _turn_calls(turn: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if turn.get("type") == "tool-batch":
        return list(turn.get("calls", []))
    if turn.get("type") == "tool-call" and isinstance(turn.get("call"), Mapping):
        return [turn["call"]]
    return []


def _journaled(journal: FileToolResultJournal, run_id: str) -> Journaled:
    found: Journaled = {}
    for handle in journal.handles_of(run_id):
        try:
            record = journal.read(handle)
        except ValueError:
            continue
        call, result = record.get("call"), record.get("result")
        if isinstance(call, Mapping) and isinstance(result, Mapping):
            found[str(call.get("id"))] = (call, result)
    return found


def _spec(call: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": call.get("id"), "name": call.get("name"), "arguments": call.get("arguments", {})}


def _agent_events(
    entries: list[AuditLogEntry], journaled: Journaled, notes: list[str]
) -> list[Event]:
    identity = next(
        (_payload(e).get("identity") for e in entries if e.event_type == "task-received"), None
    )
    requested: dict[int, list[str]] = {}
    for entry in entries:
        if entry.event_type == "tool-requested":
            details = _payload(entry).get("details", {})
            requested.setdefault(entry.iteration or 0, []).append(str(details.get("tool_call_id")))
    events: list[Event] = []
    index_of_iteration: dict[int, int] = {}
    turn_calls: dict[str, Mapping[str, Any]] = {}
    recovered = 0
    for entry in entries:
        if entry.event_type != "model-turn":
            continue
        index = len(index_of_iteration)
        index_of_iteration[entry.iteration or 0] = index
        if _truncated(entry):
            call_ids = requested.get(entry.iteration or 0, [])
            if call_ids and all(call_id in journaled for call_id in call_ids):
                specs = [_spec(journaled[call_id][0]) for call_id in call_ids]
                payload = decision_payload(specs, None)
                for spec in payload["calls"]:
                    turn_calls[str(spec["id"])] = spec
                events.append(
                    Event(
                        Kind.MODEL_CALL,
                        agent_id=identity,
                        model_index=index,
                        digest=digest_of(payload),
                        content=canonical(payload),
                    )
                )
                recovered += 1
            else:
                events.append(
                    Event(Kind.MODEL_CALL, agent_id=identity, model_index=index, truncated=True)
                )
            continue
        turn = _payload(entry).get("turn", {})
        calls = _turn_calls(turn)
        final = turn.get("output") if turn.get("type") == "final" else None
        payload = decision_payload(calls, final)
        for call in calls:
            turn_calls[str(call["id"])] = payload["calls"][calls.index(call)]
        events.append(
            Event(
                Kind.MODEL_CALL,
                agent_id=identity,
                model_index=index,
                digest=digest_of(payload),
                content=canonical(payload),
            )
        )
    results = {
        str(_payload(e).get("tool_call_id")): e
        for e in entries
        if e.event_type == "tool-result" and not _truncated(e)
    }
    for entry in entries:
        details = _payload(entry).get("details", {})
        if entry.event_type == "tool-requested":
            call_id = str(details.get("tool_call_id"))
            known = turn_calls.get(call_id)
            if known is None and call_id in results:
                payload = _payload(results[call_id])
                known = {
                    "id": call_id,
                    "name": payload.get("tool"),
                    "arguments": payload.get("arguments", {}),
                }
            if known is None and call_id in journaled:
                known = _spec(journaled[call_id][0])
                recovered += 1
            events.append(
                Event(
                    Kind.TOOL_CALL,
                    agent_id=identity,
                    model_index=index_of_iteration.get(entry.iteration or 0),
                    call_id=call_id,
                    name=details.get("tool"),
                    digest=None if known is None else digest_of(known),
                    content=None if known is None else canonical(known),
                    truncated=known is None,
                )
            )
        if entry.event_type == "tool-completed":
            call_id = str(details.get("tool_call_id"))
            full = results.get(call_id)
            envelope = None if full is None else _payload(full).get("result")
            if envelope is None and call_id in journaled:
                envelope = journaled[call_id][1]
                recovered += 1
            events.append(
                Event(
                    Kind.TOOL_RESULT,
                    agent_id=identity,
                    call_id=call_id,
                    name=details.get("tool"),
                    status=details.get("status"),
                    digest=None if envelope is None else digest_of(envelope),
                    content=None if envelope is None else canonical(envelope),
                    truncated=envelope is None,
                )
            )
    if recovered:
        notes.append(
            f'{recovered} payloads of "{identity}" were cut by the audit bound and recovered from '
            "the tool-result journal"
        )
    return events


def sdk_records(run: SdkRun) -> SystemRecords:
    audit = AuditTranscriptStore(run.run_root, read_only=True)
    try:
        per_run = {rid: list(audit.iter_entries(rid)) for rid in run.audit_run_ids}
    finally:
        audit.close()
    journal = FileToolResultJournal(run.run_root, read_only=True)
    notes: list[str] = []
    events: list[Event] = []
    for rid, entries in per_run.items():
        events.extend(_agent_events(entries, _journaled(journal, rid), notes))
    store = ArtifactStore(run.workspace, read_only=True)
    versions: dict[str, list[ArtifactVersion]] = {}
    written = sorted(
        (version for path in store.paths() for version in store.history(path)),
        key=lambda version: version.write_sequence,
    )
    for version in written:
        manifest = version.manifest or {}
        writer = manifest.get("tool_call_id")
        agent = manifest.get("agent_identity")
        path = version.relative_path
        versions.setdefault(path, []).append(
            ArtifactVersion(path, version.sha256, writer, agent, version.write_sequence)
        )
        events.append(
            Event(Kind.WRITE, agent_id=agent, call_id=writer, path=path, digest=version.sha256)
        )

    def fetch_blob(sha256: str) -> bytes | None:
        try:
            return ArtifactStore(run.workspace, read_only=True).read_bytes(sha256)
        except (OSError, ValueError, KeyError):
            return None

    return SystemRecords("nailong-agent-sdk", events, versions, fetch_blob, notes)


def event_content(event: Event) -> Any:
    return None if event.content is None else json.loads(event.content)
