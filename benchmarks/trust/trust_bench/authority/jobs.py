# Copyright (c) 2026 David Michael Indraputra

"""Authority jobs: run a case's tickets on a deployment under one allocation arm."""

from __future__ import annotations

import json
import tempfile
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..splits import Split
from .allocators import ARMS, MODEL, ModelTable, allocations
from .chains import generate as generate_chain
from .policy import Allocation, evaluate
from .runs import AuthorityRun
from .tickets import generate

SYSTEMS = ("sdk", "langgraph", "langgraph-plain", "sdk-preapproved")
AuthorityJob = tuple[int, str, str, str, str | None]


def enumerate_authority_jobs(
    split: Split,
    systems: Iterable[str],
    arms: Iterable[str],
    seeds: Iterable[int],
    table: str | None = None,
) -> list[AuthorityJob]:
    jobs: list[AuthorityJob] = []
    for system in systems:
        if system not in SYSTEMS:
            raise ValueError(f'unknown system "{system}"; known: {", ".join(SYSTEMS)}.')
    for arm in arms:
        if arm not in ARMS:
            raise ValueError(f'unknown allocation arm "{arm}"; known: {", ".join(ARMS)}.')
        if arm == MODEL and table is None:
            raise ValueError(
                "the model allocation arm needs --model-table, the file the judge allocation "
                "command wrote."
            )
    for seed in seeds:
        for system in systems:
            for arm in arms:
                jobs.append((seed, system, arm, split.value, table if arm == MODEL else None))
    return jobs


def load_table(path: str | Path) -> dict[str, Allocation]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {key: Allocation(value["class"], value["rule"]) for key, value in raw.items()}


def _deploy(system: str, arm: str, case: Any, table: ModelTable | None, base: Path) -> AuthorityRun:
    chosen = allocations(arm, case, table)
    if system in {"sdk", "sdk-preapproved"}:
        from ..systems.nailong_authority import PREAPPROVED, WAITED, run_sdk_tickets

        deployment = WAITED if system == "sdk" else PREAPPROVED
        return run_sdk_tickets(case, arm, chosen, base, deployment=deployment)
    from ..systems.langgraph_authority import run_langgraph_tickets

    return run_langgraph_tickets(case, arm, chosen, base, annotate=system == "langgraph")


def ticket_row(outcome: Any, run: AuthorityRun) -> dict[str, Any]:
    ticket = outcome.ticket
    native = outcome.native
    recomputed = None if native.meta is None else evaluate(native.meta)
    return {
        "id": ticket.id,
        "pair": ticket.pair,
        "member": ticket.member,
        "bad": ticket.bad,
        "label": ticket.label.cls,
        "label_rule": ticket.label.rule,
        "assigned": outcome.allocation.cls,
        "assigned_rule": outcome.allocation.rule,
        "decisions": [
            {k: e[k] for k in ("class", "decider", "rule", "approved")}
            for e in run.log.of(ticket.id)
        ],
        "executed": outcome.executed,
        "launched": outcome.launched,
        "status": outcome.status,
        "native": {
            "class": native.cls,
            "decider": native.decider,
            "rule": native.rule,
            "approved": native.approved,
        },
        "meta_found": native.meta is not None,
        "recomputed": None
        if recomputed is None
        else {"class": recomputed.cls, "rule": recomputed.rule},
    }


def authority_job(job: AuthorityJob) -> dict[str, Any]:
    seed, system, arm, _split, table_path = job
    warnings.simplefilter("ignore")
    case = generate(seed)
    table = load_table(table_path) if table_path is not None else None
    with tempfile.TemporaryDirectory(prefix="trustbench_authority_") as directory:
        run = _deploy(system, arm, case, table, Path(directory))
        return {
            "kind": "authority",
            "case_id": case.case_id,
            "arm": system,
            "allocation": arm,
            "log_intact": run.log.intact(),
            "tickets": [ticket_row(outcome, run) for outcome in run.outcomes],
        }


CHAIN_SYSTEMS = ("sdk", "langgraph")
ChainJob = tuple[int, str, str]


def enumerate_chain_jobs(
    split: Split, systems: Iterable[str], seeds: Iterable[int]
) -> list[ChainJob]:
    chosen = list(systems)
    for system in chosen:
        if system not in CHAIN_SYSTEMS:
            raise ValueError(
                f'unknown system "{system}" for the approval-scope probe; '
                f"known: {', '.join(CHAIN_SYSTEMS)}."
            )
    return [(seed, system, split.value) for seed in seeds for system in chosen]


def chain_job(job: ChainJob) -> dict[str, Any]:
    seed, system, _split = job
    warnings.simplefilter("ignore")
    case = generate_chain(seed)
    with tempfile.TemporaryDirectory(prefix="trustbench_chain_") as directory:
        base = Path(directory)
        if system == "sdk":
            from ..systems.nailong_authority import run_sdk_chain

            return run_sdk_chain(case, base)
        from ..systems.langgraph_authority import run_langgraph_chain

        return run_langgraph_chain(case, base)
