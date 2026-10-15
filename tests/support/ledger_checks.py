import collections
from dataclasses import dataclass

from nailong_agent_sdk.observability.run_checkpoints import TerminalLinkState, verify_terminal_link

TERMINAL_EVENT = "agent.profile-completed"


@dataclass(frozen=True)
class LedgerSummary:
    events: int
    entries: int
    turns_started: int
    model_turns: int
    tool_requests: int
    tool_completions: int
    tool_results: int
    terminal_link: TerminalLinkState
    other_events: dict[str, int]


def ledger_summary(telemetry, audit, run_id) -> LedgerSummary:
    events = list(telemetry.iter_events(run_id))
    entries = list(audit.iter_entries(run_id))
    assert events, f'run "{run_id}" has no telemetry events'
    assert entries, f'run "{run_id}" has no audit entries'
    assert [event.sequence for event in events] == list(range(1, len(events) + 1)), (
        f'telemetry sequences of run "{run_id}" are not 1..{len(events)}'
    )
    assert [entry.sequence for entry in entries] == list(range(1, len(entries) + 1)), (
        f'audit sequences of run "{run_id}" are not 1..{len(entries)}'
    )
    assert telemetry.chain_break(run_id) is None, telemetry.chain_break(run_id)
    assert audit.chain_break(run_id) is None, audit.chain_break(run_id)
    by_id = {event.event_id: event for event in events}
    cited: set[str] = set()
    for entry in entries:
        event_id = entry.payload.get("telemetry_event_id")
        if event_id is None:
            continue
        event = by_id.get(event_id)
        assert event is not None, (
            f'audit entry {entry.sequence} ({entry.event_type}) of run "{run_id}" cites a '
            "telemetry event that is not in the ledger"
        )
        assert event.event_type == f"agent.{entry.event_type}", (
            f"audit entry {entry.sequence} is {entry.event_type} but cites "
            f"{event.event_type} at telemetry sequence {event.sequence}"
        )
        cited.add(event_id)
    uncited = collections.Counter(
        event.event_type
        for event in events
        if event.event_type.startswith("agent.") and event.event_id not in cited
    )
    profiles = sum(entry.event_type == "profile-completed" for entry in entries)
    assert dict(uncited) == ({TERMINAL_EVENT: profiles} if profiles else {}), (
        f'telemetry events of run "{run_id}" that no audit entry cites: {dict(uncited)}; '
        f"profile-completed audit entries: {profiles}"
    )
    types = collections.Counter(event.event_type for event in events)
    audit_types = collections.Counter(entry.event_type for entry in entries)
    link = verify_terminal_link(telemetry, audit, run_id)
    assert link.state is not TerminalLinkState.BROKEN, link.detail
    return LedgerSummary(
        events=len(events),
        entries=len(entries),
        turns_started=types["agent.turn-started"],
        model_turns=audit_types["model-turn"],
        tool_requests=types["agent.tool-requested"],
        tool_completions=types["agent.tool-completed"],
        tool_results=audit_types["tool-result"],
        terminal_link=link.state,
        other_events={
            event_type: count
            for event_type, count in types.items()
            if not event_type.startswith("agent.")
        },
    )
