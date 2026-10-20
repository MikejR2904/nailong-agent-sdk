# Copyright (c) 2026 David Michael Indraputra

"""What one deployment of a case leaves behind: outcomes, the decision log and native records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .deciders import DecisionLog
from .policy import Allocation
from .tickets import OpsCase, Ticket

APPROVAL_TIMEOUT = 30.0


@dataclass(frozen=True)
class NativeApproval:
    """What the system's own records say about one decision point."""

    cls: str | None = None
    decider: str | None = None
    rule: str | None = None
    approved: bool | None = None
    meta: dict[str, Any] | None = None


@dataclass
class TicketOutcome:
    ticket: Ticket
    allocation: Allocation
    status: str
    reason: str | None
    executed: bool
    native: NativeApproval = field(default_factory=NativeApproval)
    launched: bool = True


@dataclass
class AuthorityRun:
    case: OpsCase
    system: str
    allocation_arm: str
    outcomes: list[TicketOutcome]
    log: DecisionLog


def task_id_for(case: OpsCase, ticket: Ticket) -> str:
    return f"{case.case_id}.{ticket.id}"


def prompt_for(ticket: Ticket) -> str:
    return f"Carry out ticket {ticket.id}: {ticket.summary()}."


def rule_of(reason: str | None) -> str | None:
    if reason is not None and reason.startswith("rule:"):
        return reason.removeprefix("rule:")
    return None


def meta_of(arguments: dict[str, Any]) -> dict[str, Any] | None:
    try:
        meta = json.loads(str(arguments["content"]))["meta"]
    except (KeyError, ValueError, TypeError):
        return None
    return meta if isinstance(meta, dict) else None
