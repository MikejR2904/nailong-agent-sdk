"""Selections through the SDK's own compaction path, feasibility validation, and candidate sets."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from nailong_agent_sdk.memory.episode_models import (
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.learned_retention import FixedUtilityPredictor, QuantizationPolicy
from nailong_agent_sdk.memory.utility_features import RetentionContext

from .instances import BuiltStore, Instance, replay

STATIC = PaskCompactionPolicy(strategy=CompactionStrategy.EXACT_PCKP)
PASK = PaskCompactionPolicy(strategy=CompactionStrategy.PASK)
LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)


@dataclass
class Selection:
    ids: frozenset[str]
    source: str
    solver_status: str | None = None
    optimality_gap: str | None = None
    latency_ms: float = 0.0
    learned_status: str | None = None
    meta: dict = field(default_factory=dict)


def retention_context(built: BuiltStore, instance: Instance) -> RetentionContext:
    live = [r for r in built.store.list() if r.state is not EpisodeState.COMPACTED]
    scorer = built.store.relevance_scorer
    return RetentionContext(
        records=tuple(live),
        token_costs={r.id: _episode_tokens(r) for r in live},
        relevance={r.id: scorer.score(instance.query, r) for r in live},
        query=instance.query,
        token_budget=instance.budget,
    )


def select_via_store(
    instance: Instance,
    policy: PaskCompactionPolicy,
    predictor=None,
    source: str = "",
    quantization: QuantizationPolicy | None = None,
) -> tuple[Selection, BuiltStore, dict]:
    built = replay(
        instance,
        utility_predictor=predictor,
        **({"utility_quantization": quantization} if quantization is not None else {}),
    )
    started = time.perf_counter()
    result = built.store.compact(
        instance.budget,
        protected_episode_ids=built.protected,
        relevance_query=instance.query,
        policy=policy,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    retained = frozenset(r.id for r in built.store.list() if r.state is not EpisodeState.COMPACTED)
    solver = result.dossier.get("solver") or {}
    learned = result.dossier.get("learned_ranking") or {}
    selection = Selection(
        ids=retained,
        source=source or policy.strategy.name,
        solver_status=solver.get("status"),
        optimality_gap=solver.get("optimality_gap"),
        latency_ms=elapsed,
        learned_status=learned.get("status"),
    )
    return selection, built, result.dossier


def static_utilities(instance: Instance) -> dict[str, float]:
    _, built, dossier = select_via_store(instance, STATIC)
    rows = dossier.get("static_utilities")
    if rows is None:
        raise ValueError(
            f'Instance "{instance.instance_id}" produced no static utilities '
            f"(status {dossier.get('solver', {}).get('status')!r}); it is not a usable instance."
        )
    return {row["episode_id"]: row["utility"] for row in rows}


def mandatory_closure(built: BuiltStore) -> set[str]:
    return _closure(built, set(built.protected))


def _closure(built: BuiltStore, seeds: set[str]) -> set[str]:
    closure = set(seeds)
    pending = list(seeds)
    while pending:
        for dependency in built.store.get(pending.pop()).depends_on:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def cost_of(built: BuiltStore, ids: set[str] | frozenset[str]) -> int:
    return sum(_episode_tokens(built.store.get(i)) for i in ids)


def assert_feasible(
    instance: Instance, built: BuiltStore, ids: frozenset[str], source: str
) -> None:
    mandatory = mandatory_closure(built)
    if not mandatory <= ids:
        raise ValueError(
            f"{source} selection for {instance.instance_id} drops mandatory episodes "
            f"{sorted(mandatory - ids)}."
        )
    for episode_id in sorted(ids):
        missing = [d for d in built.store.get(episode_id).depends_on if d not in ids]
        if missing:
            raise ValueError(
                f"{source} selection for {instance.instance_id} retains {episode_id} without "
                f"prerequisites {missing}."
            )
    if cost_of(built, ids) > instance.budget:
        raise ValueError(
            f"{source} selection for {instance.instance_id} costs {cost_of(built, ids)} tokens, "
            f"over the budget of {instance.budget}."
        )


def is_usable(instance: Instance) -> bool:
    built = replay(instance)
    return cost_of(built, mandatory_closure(built)) <= int(instance.budget * 0.85)


def random_feasible(instance: Instance, built: BuiltStore, rng: random.Random) -> frozenset[str]:
    chosen = set(mandatory_closure(built))
    order = list(built.ids)
    rng.shuffle(order)
    for episode_id in order:
        addition = _closure(built, {episode_id}) - chosen
        if addition and cost_of(built, chosen | addition) <= instance.budget:
            chosen |= addition
    return frozenset(chosen)


def edit_selection(
    instance: Instance, built: BuiltStore, base: frozenset[str], rng: random.Random
) -> frozenset[str]:
    """Remove one non-mandatory episode with its retained dependents, then try one substitute."""

    mandatory = mandatory_closure(built)
    removable = sorted(base - mandatory)
    chosen = set(base)
    if removable:
        victim = rng.choice(removable)
        doomed = {victim}
        changed = True
        while changed:
            changed = False
            for episode_id in sorted(chosen - doomed):
                if any(d in doomed for d in built.store.get(episode_id).depends_on):
                    doomed.add(episode_id)
                    changed = True
        chosen -= doomed
    outside = sorted(set(built.ids) - chosen)
    rng.shuffle(outside)
    for episode_id in outside:
        addition = _closure(built, {episode_id}) - chosen
        if addition and cost_of(built, chosen | addition) <= instance.budget:
            chosen |= addition
            break
    return frozenset(chosen)


def perturbed_values(
    base: Mapping[str, float], rng: random.Random, sigma: float
) -> dict[str, float]:
    return {k: v * math.exp(rng.gauss(0.0, sigma)) for k, v in base.items()}


def generate_candidates(
    instance: Instance,
    rng: random.Random,
    *,
    random_count: int = 6,
    perturbed_count: int = 4,
    edit_count: int = 4,
    learned_predictors: Mapping[str, object] | None = None,
    quantization: QuantizationPolicy | None = None,
) -> list[Selection]:
    candidates: list[Selection] = []
    static, _, _ = select_via_store(instance, STATIC, source="static-pckp")
    built = replay(instance)
    candidates.append(static)
    pask, _, _ = select_via_store(instance, PASK, source="greedy-pask")
    candidates.append(pask)
    for index in range(random_count):
        candidates.append(
            Selection(random_feasible(instance, built, rng), source="random-feasible")
        )
    base_values = static_utilities(instance)
    perturbed: list[Selection] = []
    for index in range(perturbed_count):
        sigma = 0.5 if index % 2 == 0 else 1.0
        values = perturbed_values(base_values, rng, sigma)
        selection, _, _ = select_via_store(
            instance,
            LEARNED,
            FixedUtilityPredictor(values, predictor_id="perturbed", version="1"),
            source="perturbed-pckp",
        )
        perturbed.append(selection)
    candidates.extend(perturbed)
    pool = [static, *perturbed]
    for _ in range(edit_count):
        base = rng.choice(pool)
        candidates.append(Selection(edit_selection(instance, built, base.ids, rng), "edit"))
    for name, predictor in sorted((learned_predictors or {}).items()):
        selection, _, _ = select_via_store(
            instance, LEARNED, predictor, source=f"learned-policy:{name}", quantization=quantization
        )
        candidates.append(selection)
    unique: dict[frozenset[str], Selection] = {}
    for candidate in candidates:
        assert_feasible(instance, built, candidate.ids, candidate.source)
        if candidate.ids in unique:
            existing = unique[candidate.ids]
            existing.meta.setdefault("also", []).append(candidate.source)
        else:
            unique[candidate.ids] = candidate
    return list(unique.values())
