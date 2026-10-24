"""Training and selection for the generalization protocol. Every learned method is run through the
SDK's LEARNED_PCKP path, so feasibility, closure and certificates come from the exact solver."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from nailong_agent_sdk.memory.episode_models import PaskCompactionPolicy
from nailong_agent_sdk.memory.learned_retention import (
    FixedUtilityPredictor,
    LearnedUtilityPredictor,
    QuantizationPolicy,
)
from nailong_agent_sdk.memory.utility_calibration import (
    CalibratedUtilityPredictor,
    FallbackPolicy,
    FeatureShiftDetector,
    PolicyStaticPredictor,
    static_prior_weights,
)
from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMAS
from nailong_agent_sdk.memory.utility_models import (
    LinearRankingModel,
    RankingModel,
    model_to_artifact,
)
from nailong_agent_sdk.memory.utility_objectives import (
    ObjectiveConfig,
    ObjectiveDataset,
    build_objective_dataset,
    effect_weights,
    train_objective,
)

from ..instances import Instance, replay
from ..oracle import DownstreamExecutor
from ..preferences import CandidateOutcome, PreferencePolicy, build_preferences
from ..selection import (
    LEARNED,
    Selection,
    edit_selection,
    retention_context,
    select_via_store,
)

BENCH_VERSION = "dfl-generalize-1"
FLOORS = (0, 1)
L2_GRID = (1e-3, 1e-2, 1e-1)


class SchemaCache:
    def __init__(self, instances: Sequence[Instance], schema: str) -> None:
        self._instances = {i.instance_id: i for i in instances}
        self._extract = FEATURE_SCHEMAS[schema][1]
        self.schema = schema
        self.dim = len(FEATURE_SCHEMAS[schema][0])
        self._cache: dict[str, dict[str, tuple[float, ...]]] = {}

    def get(self, instance_id: str) -> dict[str, tuple[float, ...]]:
        if instance_id not in self._cache:
            instance = self._instances[instance_id]
            self._cache[instance_id] = self._extract(
                retention_context(replay(instance), instance), ()
            )
        return self._cache[instance_id]


@dataclass
class Method:
    name: str
    family: str
    policy: PaskCompactionPolicy
    predictor: object | None = None
    quantization: QuantizationPolicy | None = None
    model: RankingModel | None = None
    schema: str | None = None
    train_seconds: float = 0.0
    detail: dict = field(default_factory=dict)
    selector: Callable[[Instance], Selection] | None = None


def run_method(method: Method, instance: Instance) -> Selection:
    if method.selector is not None:
        return method.selector(instance)
    selection, _, _ = select_via_store(
        instance,
        method.policy,
        method.predictor,
        source=method.name,
        quantization=method.quantization,
    )
    audit = getattr(method.predictor, "last_audit", None)
    if audit:
        selection.meta["calibration_mode"] = audit["mode"]
        selection.meta["calibration_reasons"] = audit["reasons"]
    return selection


QualityMany = Callable[[Sequence[tuple["Method", Sequence[Instance]]]], list[float]]


def sequential_quality(executor: DownstreamExecutor, trials: int) -> QualityMany:
    def evaluate(jobs: Sequence[tuple[Method, Sequence[Instance]]]) -> list[float]:
        return [decision_quality(method, instances, executor, trials) for method, instances in jobs]

    return evaluate


def decision_quality(
    method: Method, instances: Sequence[Instance], executor: DownstreamExecutor, trials: int
) -> float:
    total = 0.0
    for instance in instances:
        selection = run_method(method, instance)
        total += sum(executor.run(instance, selection.ids, t) for t in range(trials)) / trials
    return total / len(instances)


def objective_data(
    objective: str,
    cache: SchemaCache,
    outcomes: Sequence[CandidateOutcome],
    policy: PreferencePolicy,
    *,
    gamma: float = 1.0,
    normalize: bool = True,
    cap_ratio: float | None = 5.0,
    equal_cardinality: bool = False,
) -> ObjectiveDataset:
    ids = sorted({o.instance_id for o in outcomes})
    features = {i: cache.get(i) for i in ids}
    labels = {i: family_of_id(i) for i in ids}
    if objective in ("pairwise", "regret-pairwise"):
        preferences = build_preferences(list(outcomes), policy)
        if equal_cardinality:
            preferences = [
                p
                for p in preferences
                if len(p.positive - p.negative) == len(p.negative - p.positive)
            ]
        if objective == "regret-pairwise" and preferences:
            weights = effect_weights(
                [p.effect for p in preferences],
                [p.weight for p in preferences],
                gamma=gamma,
                normalize=normalize,
                cap_ratio=cap_ratio,
            )
        else:
            weights = [p.weight for p in preferences]
        pairs = [
            (p.instance_id, sorted(p.positive), sorted(p.negative), w)
            for p, w in zip(preferences, weights, strict=True)
        ]
        return build_objective_dataset(features, labels, pairs=pairs)
    by_instance: dict[str, list[CandidateOutcome]] = {}
    for outcome in outcomes:
        if len(outcome.outcomes) >= policy.min_trials:
            by_instance.setdefault(outcome.instance_id, []).append(outcome)
    if objective == "listwise":
        lists = []
        for instance_id, pool in sorted(by_instance.items()):
            means = [o.mean_outcome for o in pool]
            if len(pool) < 2 or max(means) - min(means) < policy.min_effect:
                continue
            lists.append(
                (
                    instance_id,
                    [sorted(o.selection) for o in pool],
                    means,
                    1.0 / math.log(len(pool)) if len(pool) > 2 else 1.0,
                )
            )
        return build_objective_dataset(features, labels, lists=lists)
    if objective == "regression":
        points = [
            (instance_id, sorted(o.selection), o.mean_outcome, 1.0 / len(pool))
            for instance_id, pool in sorted(by_instance.items())
            for o in pool
        ]
        return build_objective_dataset(features, labels, points=points)
    raise ValueError(f"Unknown objective {objective!r}.")


def family_of_id(instance_id: str) -> str:
    return instance_id.split(":", 1)[0]


def wrap(
    name: str, model: RankingModel, schema: str, floor: int, seconds: float, detail: dict
) -> Method:
    digest = model_to_artifact(model, {"name": name, "feature_schema_version": schema})["digest"]
    predictor = LearnedUtilityPredictor(
        model,
        predictor_id=name,
        version=BENCH_VERSION,
        artifact_digest=digest,
        feature_schema=schema,
    )
    return Method(
        name,
        "learned",
        LEARNED,
        predictor,
        QuantizationPolicy(floor=floor),
        model,
        schema,
        seconds,
        detail,
    )


def fit_method(
    name: str,
    objective: str,
    model_type: str,
    schema: str,
    caches: Mapping[str, SchemaCache],
    train_outcomes: Sequence[CandidateOutcome],
    val_outcomes: Sequence[CandidateOutcome],
    val_instances: Sequence[Instance],
    executor: DownstreamExecutor,
    *,
    preference_policy: PreferencePolicy,
    seed: int,
    trials: int,
    group_dro: bool = False,
    gamma: float = 1.0,
    normalize: bool = True,
    cap_ratio: float | None = 5.0,
    equal_cardinality: bool = False,
    l2_grid: Sequence[float] = L2_GRID,
) -> Method:
    cache = caches[schema]
    started = time.perf_counter()
    train = objective_data(
        objective,
        cache,
        train_outcomes,
        preference_policy,
        gamma=gamma,
        normalize=normalize,
        cap_ratio=cap_ratio,
        equal_cardinality=equal_cardinality,
    )
    validation = objective_data(
        objective,
        cache,
        val_outcomes,
        preference_policy,
        gamma=gamma,
        normalize=normalize,
        cap_ratio=cap_ratio,
        equal_cardinality=equal_cardinality,
    )
    if not train.terms:
        raise ValueError(f"{name}: no {objective} terms in the training outcomes.")
    best: tuple[float, Method] | None = None
    for l2 in l2_grid:
        config = ObjectiveConfig(
            objective=objective,
            model_type=model_type,
            l2=l2,
            seed=seed,
            group_dro=group_dro,
            tau_quality=0.1,
            max_iterations=300 if model_type == "linear" else 200,
            learning_rate=0.05,
            patience=30,
        )
        model, report = train_objective(
            train,
            config,
            feature_dim=cache.dim,
            validation=validation if validation.terms else None,
        )
        for floor in FLOORS:
            candidate = wrap(name, model, schema, floor, 0.0, {})
            quality = decision_quality(candidate, val_instances, executor, trials)
            if best is None or quality > best[0]:
                candidate.detail = {
                    "objective": objective,
                    "model_type": model_type,
                    "feature_schema": schema,
                    "group_dro": group_dro,
                    "l2": l2,
                    "floor": floor,
                    "validation_quality": quality,
                    "degenerate": not any(model.parameters),
                    "train_terms": len(train.terms),
                    "iterations": report.training.iterations,
                    "gradient_norm": report.training.gradient_norm,
                    "converged": report.training.converged,
                    "group_losses": report.group_losses,
                    "group_weights": report.group_weights,
                    "gamma": gamma,
                    "normalize": normalize,
                    "cap_ratio": cap_ratio,
                    "equal_cardinality": equal_cardinality,
                    "bias_weight": model.parameters[0],
                    "config": config.model_dump(),
                }
                best = (quality, candidate)
    assert best is not None
    best[1].train_seconds = time.perf_counter() - started
    return best[1]


def tune_static(
    instances: Sequence[Instance],
    executor: DownstreamExecutor,
    trials: int,
    seed: int,
    draws: int = 40,
    quality_many: QualityMany | None = None,
) -> Method:
    """Random search over the five static weights on non-test instances; the default policy is a
    candidate, so the tuned policy is never worse than the default on the tuning set."""

    rng = random.Random(f"{seed}/tune-static")
    started = time.perf_counter()
    default = PaskCompactionPolicy()
    candidates = [default]
    for _ in range(draws):
        raw = [rng.random() for _ in range(5)]
        total = sum(raw)
        w = [x / total for x in raw]
        candidates.append(
            PaskCompactionPolicy(
                task_relevance_weight=w[0],
                dependency_centrality_weight=w[1],
                provenance_weight=w[2],
                recency_weight=w[3],
                frequency_weight=w[4],
            )
        )
    methods = [Method("tuned-static-pckp", "static", policy) for policy in candidates]
    evaluate = quality_many or sequential_quality(executor, trials)
    qualities = evaluate([(method, instances) for method in methods])
    best: tuple[float, PaskCompactionPolicy] | None = None
    for policy, quality in zip(candidates, qualities, strict=True):
        if best is None or quality > best[0]:
            best = (quality, policy)
    assert best is not None
    return Method(
        "tuned-static-pckp",
        "static",
        best[1],
        train_seconds=time.perf_counter() - started,
        detail={"tuning_quality": best[0], "weights": best[1].model_dump(mode="json")},
    )


def boundary_candidates(
    instance: Instance,
    base: Method,
    rng: random.Random,
    *,
    perturbed: int = 3,
    edits: int = 2,
    sigmas: Sequence[float] = (0.3, 0.8),
) -> list[Selection]:
    """Selections near the current learned decision: the learned choice, perturbed-utility
    re-solves and single-episode edits of the learned choice."""

    built = replay(instance)
    context = retention_context(built, instance)
    raw = dict(base.predictor.predict(context))
    spread = math.sqrt(
        sum((v - sum(raw.values()) / len(raw)) ** 2 for v in raw.values()) / len(raw)
    )
    chosen = run_method(base, instance)
    out = [Selection(chosen.ids, "boundary-learned")]
    for index in range(perturbed):
        sigma = sigmas[index % len(sigmas)]
        values = {k: v + rng.gauss(0.0, sigma * (spread or 1.0)) for k, v in raw.items()}
        selection, _, _ = select_via_store(
            instance,
            LEARNED,
            FixedUtilityPredictor(values, predictor_id="boundary", version="1"),
            source="boundary-perturbed",
            quantization=QuantizationPolicy(floor=1),
        )
        out.append(selection)
    for _ in range(edits):
        out.append(Selection(edit_selection(instance, built, chosen.ids, rng), "boundary-edit"))
    return out


def evaluate_boundary(
    instances: Sequence[Instance],
    base: Method,
    executor: DownstreamExecutor,
    trials: int,
    seed: int,
) -> list[CandidateOutcome]:
    outcomes: list[CandidateOutcome] = []
    for instance in instances:
        rng = random.Random(f"{seed}/{instance.instance_id}/boundary")
        seen: set[frozenset[str]] = set()
        for candidate in boundary_candidates(instance, base, rng):
            if candidate.ids in seen:
                continue
            seen.add(candidate.ids)
            outcomes.append(
                CandidateOutcome(
                    instance.instance_id,
                    candidate.ids,
                    (candidate.source,),
                    tuple(executor.run(instance, candidate.ids, t) for t in range(trials)),
                    executor.evidence_kind,
                )
            )
    return outcomes


def merge_outcomes(
    base: Sequence[CandidateOutcome], extra: Sequence[CandidateOutcome]
) -> list[CandidateOutcome]:
    merged = {(o.instance_id, o.selection): o for o in base}
    for item in extra:
        key = (item.instance_id, item.selection)
        if key in merged:
            old = merged[key]
            merged[key] = CandidateOutcome(
                old.instance_id,
                old.selection,
                tuple(dict.fromkeys((*old.sources, *item.sources))),
                old.outcomes,
                old.evidence_kind,
            )
        else:
            merged[key] = item
    return list(merged.values())


def build_calibrated(
    base: Method,
    alpha: float,
    detector: FeatureShiftDetector,
    schema_extractor: Callable,
    *,
    max_clip_fraction: float = 0.5,
    name: str = "calibrated-pckp",
) -> Method:
    predictor = CalibratedUtilityPredictor(
        base.predictor,
        PolicyStaticPredictor(PaskCompactionPolicy()),
        FallbackPolicy(
            alpha=alpha,
            max_shift=detector.threshold,
            max_clip_fraction=max_clip_fraction,
            min_dispersion=0.02,
        ),
        detector=detector,
        shift_features=lambda context: schema_extractor(context, ()),
        predictor_id=name,
        version=BENCH_VERSION,
    )
    return Method(
        name,
        "calibrated",
        LEARNED,
        predictor,
        base.quantization,
        None,
        base.schema,
        0.0,
        {"alpha": alpha, "base": base.name, "max_shift": detector.threshold},
    )


def fit_detector(
    instances_train: Sequence[Instance],
    instances_val: Sequence[Instance],
    schema: str,
    quantile: float = 0.95,
) -> FeatureShiftDetector:
    extract = FEATURE_SCHEMAS[schema][1]

    def contexts(instances):
        return [extract(retention_context(replay(i), i), ()) for i in instances]

    detector = FeatureShiftDetector().fit(contexts(instances_train))
    detector.calibrate(contexts(instances_val), quantile)
    return detector


SHRINK_GRID = (0.03, 0.3, 3.0, 30.0)


def fit_shrunk(
    name: str,
    objective: str,
    schema: str,
    caches: Mapping[str, SchemaCache],
    train_instances: Sequence[Instance],
    train_outcomes: Sequence[CandidateOutcome],
    executor: DownstreamExecutor,
    *,
    preference_policy: PreferencePolicy,
    seed: int,
    trials: int,
    group_dro: bool = False,
    grid: Sequence[float] = SHRINK_GRID,
) -> Method:
    """A linear model regularised towards the static policy, with the amount of shrinkage chosen by
    leave-one-family-out decision quality on the training families.

    The static policy is a point of the linear model class, so infinite shrinkage is static PCKP.
    The learned model is returned only if its mean quality on the held-out training families beats
    static on those same families; otherwise the static prior itself is returned.
    """

    cache = caches[schema]
    names = FEATURE_SCHEMAS[schema][0]
    prior = tuple(static_prior_weights(PaskCompactionPolicy(), names))
    started = time.perf_counter()
    families = sorted({family_of_id(i.instance_id) for i in train_instances})

    def train(outcomes: Sequence[CandidateOutcome], l2: float) -> RankingModel | None:
        data = objective_data(objective, cache, outcomes, preference_policy)
        if not data.terms:
            return None
        config = ObjectiveConfig(
            objective=objective,
            model_type="linear",
            l2=l2,
            seed=seed,
            group_dro=group_dro,
            prior=prior,
            tau_quality=0.1,
            max_iterations=200,
            learning_rate=0.02,
            patience=40,
            keep_initial=True,
        )
        return train_objective(data, config, feature_dim=cache.dim)[0]

    static_method = wrap(name, LinearRankingModel(list(prior)), schema, 1, 0.0, {})
    inner: dict[float, list[float]] = {l2: [] for l2 in grid}
    static_inner: list[float] = []
    for held in families:
        held_instances = [i for i in train_instances if family_of_id(i.instance_id) == held]
        ids = {i.instance_id for i in held_instances}
        rest = [o for o in train_outcomes if o.instance_id not in ids]
        static_inner.append(decision_quality(static_method, held_instances, executor, trials))
        for l2 in grid:
            model = train(rest, l2)
            inner[l2].append(
                static_inner[-1]
                if model is None
                else decision_quality(
                    wrap(name, model, schema, 1, 0.0, {}), held_instances, executor, trials
                )
            )
    static_mean = sum(static_inner) / len(static_inner)
    means = {l2: sum(v) / len(v) for l2, v in inner.items()}
    best_l2 = max(grid, key=lambda l2: means[l2])
    chosen: RankingModel | None = None
    if means[best_l2] > static_mean:
        chosen = train(train_outcomes, best_l2)
    method = wrap(
        name,
        chosen if chosen is not None else LinearRankingModel(list(prior)),
        schema,
        1,
        time.perf_counter() - started,
        {
            "objective": objective,
            "feature_schema": schema,
            "group_dro": group_dro,
            "shrink_l2": best_l2,
            "inner_mean_quality": means,
            "inner_static_quality": static_mean,
            "fell_back_to_static": chosen is None,
            "degenerate": False,
        },
    )
    return method
