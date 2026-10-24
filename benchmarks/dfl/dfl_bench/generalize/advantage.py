"""Static-anchored advantage prediction with a conservative gate.

For each task the reference is the exact static PCKP selection ``S0``. A linear utility
``theta`` is trained so that ``U(S) - U(S0)`` predicts the measured quality gain
``Q(S) - Q(S0)``. At inference the learned selection ``S_L = PCKP(theta)`` is returned only if a
bootstrap ensemble's mean predicted advantage minus ``beta`` standard deviations exceeds
``delta``; otherwise the static selection itself is returned, not a recomputation from rescaled
utilities.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nailong_agent_sdk.memory.utility_models import LinearRankingModel
from nailong_agent_sdk.memory.utility_objectives import (
    ObjectiveConfig,
    ObjectiveDataset,
    ObjectiveTerm,
    train_objective,
)

from ..instances import Instance
from ..oracle import DownstreamExecutor
from ..preferences import CandidateOutcome, PreferencePolicy
from ..selection import STATIC, Selection, select_via_store
from .learners import Method, SchemaCache, family_of_id, run_method, wrap

BETAS = (0.0, 1.0, 2.0)
DELTAS = (0.0, 0.02, 0.05)
ENSEMBLE = 8
L2_GRID = (0.1, 1.0)


def static_selections(instances: Sequence[Instance]) -> dict[str, frozenset[str]]:
    return {i.instance_id: select_via_store(i, STATIC)[0].ids for i in instances}


def advantage_dataset(
    cache: SchemaCache,
    outcomes: Sequence[CandidateOutcome],
    static_ids: Mapping[str, frozenset[str]],
    instance_ids: Sequence[str],
    policy: PreferencePolicy,
) -> ObjectiveDataset:
    """Regression terms ``U(S) - U(S0) ~ Q(S) - Q(S0)``; the S0-only items enter negated, which is
    exact for a linear model."""

    by_instance: dict[str, list[CandidateOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if len(outcome.outcomes) >= policy.min_trials:
            by_instance[outcome.instance_id].append(outcome)
    items: list[tuple[float, ...]] = []
    terms: list[ObjectiveTerm] = []
    for instance_id in instance_ids:
        pool = by_instance.get(instance_id, [])
        reference = static_ids[instance_id]
        base = [o for o in pool if o.selection == reference]
        if not base:
            continue
        others = [o for o in pool if o.selection != reference]
        if not others:
            continue
        features = cache.get(instance_id)
        for outcome in others:
            indices: list[int] = []
            for episode_id in sorted(outcome.selection - reference):
                indices.append(len(items))
                items.append(features[episode_id])
            for episode_id in sorted(reference - outcome.selection):
                indices.append(len(items))
                items.append(tuple(-x for x in features[episode_id]))
            if indices:
                terms.append(
                    ObjectiveTerm(
                        "reg",
                        (tuple(indices),),
                        (outcome.mean_outcome - base[0].mean_outcome,),
                        1.0 / len(others),
                        family_of_id(instance_id),
                    )
                )
    return ObjectiveDataset(tuple(items), tuple(terms))


def train_ensemble(
    cache: SchemaCache,
    outcomes: Sequence[CandidateOutcome],
    instances: Sequence[Instance],
    static_ids: Mapping[str, frozenset[str]],
    policy: PreferencePolicy,
    *,
    l2: float,
    seed: int,
    size: int = ENSEMBLE,
) -> list[LinearRankingModel]:
    import random

    by_template: dict[str, list[str]] = defaultdict(list)
    for instance in instances:
        by_template[instance.template_id].append(instance.instance_id)
    templates = sorted(by_template)
    rng = random.Random(f"{seed}/advantage-bootstrap")
    models: list[LinearRankingModel] = []
    for _ in range(size):
        ids = [i for t in (rng.choice(templates) for _ in templates) for i in by_template[t]]
        data = advantage_dataset(cache, outcomes, static_ids, ids, policy)
        if not data.terms:
            continue
        config = ObjectiveConfig(
            objective="regression",
            model_type="linear",
            l2=l2,
            max_iterations=150,
            learning_rate=0.03,
            patience=30,
        )
        model, _ = train_objective(data, config, feature_dim=cache.dim)
        assert isinstance(model, LinearRankingModel)
        models.append(model)
    return models


@dataclass
class Decision:
    static: Selection
    learned: Selection
    mean: float
    spread: float


def decide(
    instance: Instance, models: Sequence[LinearRankingModel], cache: SchemaCache, learned: Method
) -> Decision:
    static = select_via_store(instance, STATIC, source="static-pckp")[0]
    candidate = run_method(learned, instance)
    features = cache.get(instance.instance_id)
    gain = [
        sum(m.score(features[e]) for e in candidate.ids - static.ids)
        - sum(m.score(features[e]) for e in static.ids - candidate.ids)
        for m in models
    ]
    return Decision(
        static,
        candidate,
        statistics.fmean(gain),
        statistics.pstdev(gain) if len(gain) > 1 else 0.0,
    )


def accepts(decision: Decision, beta: float, delta: float) -> bool:
    return (
        decision.learned.ids != decision.static.ids
        and decision.mean - beta * decision.spread > delta
    )


def mean_model(models: Sequence[LinearRankingModel]) -> LinearRankingModel:
    width = len(models[0].parameters)
    return LinearRankingModel(
        [statistics.fmean(m.parameters[k] for m in models) for k in range(width)]
    )


class GatedSelector:
    def __init__(self, models, cache, learned: Method, beta: float, delta: float, name: str):
        self.models, self.cache, self.learned = models, cache, learned
        self.beta, self.delta, self.name = beta, delta, name

    def __call__(self, instance: Instance) -> Selection:
        decision = decide(instance, self.models, self.cache, self.learned)
        accepted = accepts(decision, self.beta, self.delta)
        chosen = decision.learned if accepted else decision.static
        selection = Selection(chosen.ids, self.name, chosen.solver_status, chosen.optimality_gap)
        selection.meta["gate_accepted"] = accepted
        selection.meta["predicted_advantage"] = decision.mean
        selection.meta["advantage_spread"] = decision.spread
        return selection


def fit_gated(
    name: str,
    schema: str,
    caches: Mapping[str, SchemaCache],
    train_instances: Sequence[Instance],
    train_outcomes: Sequence[CandidateOutcome],
    executor: DownstreamExecutor,
    *,
    preference_policy: PreferencePolicy,
    seed: int,
    trials: int,
) -> tuple[Method, Method]:
    """Returns ``(gated, ungated)``. Gate parameters and ridge strength are chosen by
    leave-one-family-out mean quality gain over static on the training families; if no setting
    gains, the gate never opens."""

    cache = caches[schema]
    started = time.perf_counter()
    static_ids = static_selections(train_instances)
    families = sorted({family_of_id(i.instance_id) for i in train_instances})

    def quality(instance: Instance, ids: frozenset[str]) -> float:
        return sum(executor.run(instance, ids, t) for t in range(trials)) / trials

    scores: dict[tuple[float, float, float], list[float]] = defaultdict(list)
    for held in families:
        held_instances = [i for i in train_instances if family_of_id(i.instance_id) == held]
        rest_instances = [i for i in train_instances if family_of_id(i.instance_id) != held]
        rest_ids = {i.instance_id for i in rest_instances}
        rest_out = [o for o in train_outcomes if o.instance_id in rest_ids]
        for l2 in L2_GRID:
            models = train_ensemble(
                cache, rest_out, rest_instances, static_ids, preference_policy, l2=l2, seed=seed
            )
            if not models:
                continue
            learned = wrap(name, mean_model(models), schema, 1, 0.0, {})
            decisions = [(i, decide(i, models, cache, learned)) for i in held_instances]
            gains = [
                (d, quality(i, d.learned.ids) - quality(i, d.static.ids)) for i, d in decisions
            ]
            for beta in BETAS:
                for delta in DELTAS:
                    scores[(l2, beta, delta)].append(
                        sum(g for d, g in gains if accepts(d, beta, delta)) / len(gains)
                    )
    summary = {key: statistics.fmean(v) for key, v in scores.items() if len(v) == len(families)}
    models_all: dict[float, list[LinearRankingModel]] = {}
    opened = bool(summary) and max(summary.values()) > 0
    if opened:
        best = max(summary, key=lambda k: (summary[k], k[2], k[1]))
        l2, beta, delta = best
    else:
        l2, beta, delta = L2_GRID[-1], 0.0, float("inf")
    models_all[l2] = train_ensemble(
        cache, train_outcomes, train_instances, static_ids, preference_policy, l2=l2, seed=seed
    )
    learned = wrap(name, mean_model(models_all[l2]), schema, 1, 0.0, {})
    detail = {
        "objective": "advantage",
        "feature_schema": schema,
        "l2": l2,
        "beta": beta,
        "delta": delta,
        "gate_open_possible": opened,
        "inner_gain": {str(k): v for k, v in summary.items()},
        "degenerate": False,
    }
    elapsed = time.perf_counter() - started
    gated = Method(name, "gated", STATIC, train_seconds=elapsed, detail=detail, schema=schema)
    gated.selector = GatedSelector(models_all[l2], cache, learned, beta, delta, name)
    ungated = Method(
        f"{name}-ungated", "gated", STATIC, detail={**detail, "gate": "always open"}, schema=schema
    )
    ungated.selector = GatedSelector(
        models_all[l2], cache, learned, float("-inf"), float("-inf"), f"{name}-ungated"
    )
    return gated, ungated
