# Copyright (c) 2026 David Michael Indraputra

"""The confirmatory test plan the lock pins, and the check that a test run stays inside it."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from .authority.allocators import ARMS as ALLOCATION_ARMS
from .authority.jobs import CHAIN_SYSTEMS
from .authority.jobs import SYSTEMS as AUTHORITY_SYSTEMS
from .evaluate import ARMS, FAMILIES
from .judge.client import MODEL as JUDGE_MODEL
from .judge.prepare import ARMS as JUDGE_ARMS
from .judge.prepare import TASKS as JUDGE_TASKS
from .lock import LockError
from .splits import Split, operators_for
from .tamper_jobs import ARMS as TAMPER_ARMS
from .verify_jobs import ATTACK_FAMILIES, VERIFY_ARMS, verify_operators

CONFIRMATORY_SEEDS = range(1_000_000, 1_000_100)
CONFIRMATORY_TAMPER_SEEDS = range(1_000_000, 1_000_080)
VALIDATION_SEEDS = range(1_900_000, 1_900_050)
TAMPER_FAMILIES = ("doc", "data")
AUTHORITY_FAMILIES = ("ops",)
JUDGE_MAX_CALLS = 12_000
JUDGE_MAX_TOKENS = 40_000_000
PREFIXES = {
    "run": "",
    "tamper": "tamper_",
    "verify": "verify_",
    "attack": "attack_",
    "authority": "authority_",
    "judge": "judge_",
    "chain": "chain_",
}


def confirmatory_plan(
    seeds: range = CONFIRMATORY_SEEDS, tamper_seeds: range = CONFIRMATORY_TAMPER_SEEDS
) -> dict[str, Any]:
    return {
        "families": sorted(FAMILIES),
        "arms": list(ARMS),
        "seeds": [seeds.start, seeds.stop - 1],
        "operators": sorted(operators_for(Split.TEST)),
        "tamper_families": list(TAMPER_FAMILIES),
        "tamper_arms": list(TAMPER_ARMS),
        "tamper_seeds": [tamper_seeds.start, tamper_seeds.stop - 1],
        "verify_families": sorted(FAMILIES),
        "verify_arms": list(VERIFY_ARMS),
        "verify_seeds": [seeds.start, seeds.stop - 1],
        "verify_operators": sorted(verify_operators(Split.TEST)),
        "attack_families": list(ATTACK_FAMILIES),
        "attack_arms": list(VERIFY_ARMS),
        "attack_seeds": [seeds.start, seeds.stop - 1],
        "authority_families": list(AUTHORITY_FAMILIES),
        "authority_arms": list(AUTHORITY_SYSTEMS),
        "authority_allocations": list(ALLOCATION_ARMS),
        "authority_seeds": [seeds.start, seeds.stop - 1],
        "chain_families": list(AUTHORITY_FAMILIES),
        "chain_arms": list(CHAIN_SYSTEMS),
        "chain_seeds": [seeds.start, seeds.stop - 1],
        "judge_families": sorted(FAMILIES),
        "judge_arms": list(JUDGE_ARMS),
        "judge_tasks": list(JUDGE_TASKS),
        "judge_seeds": [seeds.start, seeds.stop - 1],
        "judge_model": JUDGE_MODEL,
        "judge_budget": {"max_calls": JUDGE_MAX_CALLS, "max_input_tokens": JUDGE_MAX_TOKENS},
    }


def require_within_plan(
    plan: Mapping[str, Any],
    *,
    kind: str,
    families: Collection[str],
    arms: Collection[str],
    seeds: Collection[int],
    extra: Mapping[str, Collection[str]] | None = None,
) -> None:
    if kind not in PREFIXES:
        raise ValueError(f'unknown plan kind "{kind}"; known: {", ".join(PREFIXES)}.')
    prefix = PREFIXES[kind]
    label = f"{kind} " if kind != "run" else ""
    low, high = plan[f"{prefix}seeds"]
    dimensions = {"families": families, "arms": arms, **(extra or {})}
    for name, requested in dimensions.items():
        allowed = plan[f"{prefix}{name}"]
        outside = sorted(set(requested) - set(allowed))
        if outside:
            raise LockError(
                f"{name} {outside} are not in the locked {label}plan; "
                f"the lock allows {sorted(allowed)}."
            )
    outside_seeds = sorted(seed for seed in seeds if not low <= seed <= high)
    if outside_seeds:
        raise LockError(f"seeds {outside_seeds[:5]} are outside the locked plan [{low}, {high}].")
