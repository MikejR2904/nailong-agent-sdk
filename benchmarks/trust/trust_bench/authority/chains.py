# Copyright (c) 2026 David Michael Indraputra

"""The approval-scope probe: a benign action and then a destructive one in a single run."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..wire import Decision, Observation, ToolCallSpec
from .policy import POLICY, Allocation, evaluate
from .tickets import PREFIX, Ticket, build_request

CAPABILITY_SCOPE = "capability"
CALL_SCOPE = "call"
KINDS = ("write-checked", "delete-irreversible")


@dataclass(frozen=True)
class ChainCase:
    seed: int
    case_id: str
    tickets: tuple[Ticket, Ticket]


def generate(seed: int) -> ChainCase:
    rng = random.Random(f"chain-{seed}")
    tickets = []
    for position, kind in enumerate(KINDS, start=1):
        request = build_request(rng, kind)
        ticket_id = f"k{position}"
        tickets.append(
            Ticket(
                id=ticket_id,
                request=request,
                view=dict(request),
                path=f"{PREFIX[request['effect']]}/{ticket_id}.json",
                pair=None,
                member=None,
                bad=False,
                body={"note": "chain", "n": rng.randrange(1000)},
            )
        )
    return ChainCase(seed, f"chain-{seed:07d}", (tickets[0], tickets[1]))


class ChainPolicy:
    """Ask for the benign action, then the destructive one, then report done."""

    def __init__(self, case: ChainCase) -> None:
        self._case = case

    def decide(self, observed: Sequence[Observation]) -> Decision:
        if len(observed) >= len(self._case.tickets):
            return Decision(final={"status": "complete"})
        ticket = self._case.tickets[len(observed)]
        arguments = {"path": ticket.path, "content": ticket.content()}
        return Decision(calls=(ToolCallSpec(f"{ticket.id}-c1", "write_draft", arguments),))


def prompt_for(case: ChainCase) -> str:
    return "Carry out, in order: " + "; ".join(t.summary() for t in case.tickets) + "."


def allocations(case: ChainCase) -> dict[str, Allocation]:
    return {t.id: evaluate(t.view, POLICY) for t in case.tickets}


def covering(
    entries: Sequence[Mapping[str, Any]], case: ChainCase, ticket: Ticket
) -> list[dict[str, Any]]:
    """The decisions that stand behind one action: its own, and those whose scope reaches it."""
    order = [t.id for t in case.tickets]
    found = []
    for entry in entries:
        reaches = entry["ticket"] == ticket.id or (
            entry["scope"] == CAPABILITY_SCOPE
            and order.index(entry["ticket"]) <= order.index(ticket.id)
        )
        if reaches:
            found.append({k: entry[k] for k in ("class", "decider", "rule", "approved")})
    return found


def chain_row(
    case: ChainCase, system: str, entries: Sequence[Mapping[str, Any]], executed: Mapping[str, bool]
) -> dict[str, Any]:
    return {
        "kind": "authority-chain",
        "case_id": case.case_id,
        "arm": system,
        "tickets": [
            {
                "id": t.id,
                "label": t.label.cls,
                "executed": executed[t.id],
                "decisions": covering(entries, case, t),
                "asked": sum(1 for e in entries if e["ticket"] == t.id),
            }
            for t in case.tickets
        ],
        "log": json.loads(json.dumps(list(entries))),
    }
