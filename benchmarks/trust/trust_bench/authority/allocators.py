# Copyright (c) 2026 David Michael Indraputra

"""Allocation arms: which class of decider each ticket of a case is sent to."""

from __future__ import annotations

import random
from collections.abc import Mapping

from .policy import Allocation, evaluate
from .tickets import OpsCase

RULE, MODEL, ALL_HUMAN, NONE, RANDOM = "rule", "model", "all-human", "none", "random"
ARMS = (RULE, MODEL, ALL_HUMAN, NONE, RANDOM)
ModelTable = Mapping[str, Allocation]


def model_key(case_id: str, ticket_id: str) -> str:
    return f"{case_id}/{ticket_id}"


def allocations(
    arm: str, case: OpsCase, model_table: ModelTable | None = None
) -> dict[str, Allocation]:
    if arm == RULE:
        return {t.id: evaluate(t.view) for t in case.tickets}
    if arm == ALL_HUMAN:
        return {t.id: Allocation("H", "all-human") for t in case.tickets}
    if arm == NONE:
        return {t.id: Allocation("D", "auto-approve") for t in case.tickets}
    if arm == RANDOM:
        classes = [evaluate(t.view).cls for t in case.tickets]
        random.Random(f"{case.case_id}-random").shuffle(classes)
        return {t.id: Allocation(c, "random") for t, c in zip(case.tickets, classes, strict=True)}
    if arm == MODEL:
        found: dict[str, Allocation] = {}
        for ticket in case.tickets:
            key = model_key(case.case_id, ticket.id)
            if model_table is None or key not in model_table:
                raise ValueError(
                    f'the model allocation table has no entry for "{key}"; run the judge '
                    "allocation command for this case before the model arm."
                )
            found[ticket.id] = model_table[key]
        return found
    raise ValueError(f'unknown allocation arm "{arm}"; known: {", ".join(ARMS)}.')
