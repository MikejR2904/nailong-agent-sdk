"""Few-shot adaptation to an unseen family from a small support set of verified outcomes.

Every method may use the support instances and their measured outcomes of the held-out family and is
scored on the family's remaining templates. The non-learning comparator (`tuned-static-support`)
receives the same support set, so the comparison is between ways of using the same evidence. The
number of outcome evaluations each method consumed is recorded.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from nailong_agent_sdk.memory.episode_models import PaskCompactionPolicy
from nailong_agent_sdk.memory.utility_calibration import static_prior_weights
from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMAS
from nailong_agent_sdk.memory.utility_models import LinearRankingModel
from nailong_agent_sdk.memory.utility_objectives import (
    ObjectiveConfig,
    ObjectiveDataset,
    train_objective,
)

from ..experiment import evaluate_candidates, score_selection
from ..instances import Instance
from ..oracle import DownstreamExecutor, SyntheticOracleExecutor
from ..preferences import CandidateOutcome, PreferencePolicy
from ..selection import STATIC
from ..splits import assert_no_leakage
from .learners import (
    BENCH_VERSION,
    Method,
    QualityMany,
    SchemaCache,
    fit_method,
    objective_data,
    run_method,
    sequential_quality,
    tune_static,
    wrap,
)
from .loo import merge_datasets
from .protocol import SPLIT_KEYS, V1, Config, make_fold
from .worlds import family_of, fast_optimum_quality

ADAPT_GRID = (0.1, 1.0, 10.0, 100.0)
TUNE_DRAWS = 40


def split_support(
    family: Sequence[Instance], support_templates: int, seed: int
) -> tuple[list[Instance], list[Instance]]:
    templates = template_ids(family)
    ordered = sorted(templates, key=lambda t: (hash_key(f"{seed}/{t}"), t))
    chosen = set(ordered[:support_templates])
    support = [i for i in family if i.template_id in chosen]
    query = [i for i in family if i.template_id not in chosen]
    if not support or not query:
        raise ValueError(
            f"A family of {len(templates)} templates cannot give {support_templates} support "
            "templates and a non-empty query set."
        )
    return support, query


def template_ids(instances: Sequence[Instance]) -> list[str]:
    return sorted({i.template_id for i in instances})


def hash_key(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def fit_adapted(
    name: str,
    objective: str,
    prior: Sequence[float],
    support: Sequence[Instance],
    support_outcomes: Sequence[CandidateOutcome],
    cache: SchemaCache,
    executor: DownstreamExecutor,
    *,
    preference_policy: PreferencePolicy,
    seed: int,
    trials: int,
    grid: Sequence[float] = ADAPT_GRID,
    quality_many: QualityMany | None = None,
    extra_data: Callable[[Sequence[str]], ObjectiveDataset] | None = None,
) -> Method:
    """Regression or listwise training on the support outcomes, regularised towards ``prior``.
    The regularisation strength is chosen by leave-one-template-out decision quality on the support
    set; the prior itself is returned unless some strength beats it there."""

    started = time.perf_counter()
    schema = cache.schema

    def train(outcomes: Sequence[CandidateOutcome], l2: float) -> LinearRankingModel | None:
        data = objective_data(objective, cache, outcomes, preference_policy)
        if extra_data is not None:
            data = merge_datasets(data, extra_data(sorted({o.instance_id for o in outcomes})))
        if not data.terms:
            return None
        config = ObjectiveConfig(
            objective=objective,
            model_type="linear",
            l2=l2,
            seed=seed,
            prior=tuple(prior),
            tau_quality=0.1,
            max_iterations=200,
            learning_rate=0.02,
            patience=40,
            keep_initial=True,
        )
        model, _ = train_objective(data, config, feature_dim=cache.dim)
        assert isinstance(model, LinearRankingModel)
        return model

    anchor = wrap(name, LinearRankingModel(list(prior)), schema, 1, 0.0, {})
    templates = template_ids(support)
    evaluate = quality_many or sequential_quality(executor, trials)
    jobs: list[tuple[Method, Sequence[Instance]]] = []
    layout: list[tuple[str, float | None]] = []
    if len(templates) >= 2:
        for held in templates:
            held_instances = [i for i in support if i.template_id == held]
            rest_ids = {i.instance_id for i in support if i.template_id != held}
            rest = [o for o in support_outcomes if o.instance_id in rest_ids]
            jobs.append((anchor, held_instances))
            layout.append((held, None))
            for l2 in grid:
                model = train(rest, l2)
                jobs.append(
                    (
                        anchor if model is None else wrap(name, model, schema, 1, 0.0, {}),
                        held_instances,
                    )
                )
                layout.append((held, l2))
    results = evaluate(jobs)
    scores: dict[float, list[float]] = {l2: [] for l2 in grid}
    anchor_scores: list[float] = []
    for (_, l2), value in zip(layout, results, strict=True):
        if l2 is None:
            anchor_scores.append(value)
        else:
            scores[l2].append(value)
    means = {l2: sum(v) / len(v) for l2, v in scores.items() if v}
    anchor_mean = sum(anchor_scores) / len(anchor_scores) if anchor_scores else 0.0
    best = max(means, key=lambda k: (means[k], k)) if means else None
    chosen = (
        train(support_outcomes, best) if best is not None and means[best] > anchor_mean else None
    )
    return wrap(
        name,
        chosen if chosen is not None else LinearRankingModel(list(prior)),
        schema,
        1,
        time.perf_counter() - started,
        {
            "objective": objective,
            "l2": best,
            "cv_quality": means,
            "cv_anchor_quality": anchor_mean,
            "adapted": chosen is not None,
            "degenerate": False,
        },
    )


def run_fewshot_fold(seed: int, fold: str, cfg: Config) -> dict:
    if not fold.startswith("lofo:"):
        raise ValueError(f"Few-shot adaptation is defined for lofo folds only, got {fold!r}.")
    started = time.perf_counter()
    executor = SyntheticOracleExecutor()
    train, val, family = make_fold(seed, fold, cfg)
    support, query = split_support(family, cfg.support_templates, seed)
    assert_no_leakage(support, query, keys=(*SPLIT_KEYS, "instance"))
    assert_no_leakage(train + val, support, keys=(*SPLIT_KEYS, "instance"))
    everything = train + val + support + query
    cache = SchemaCache(everything, V1)
    policy = cfg.preference_policy
    trials = cfg.validation_trials
    train_ids = {i.instance_id for i in train}
    source_out = evaluate_candidates(train + val, executor, cfg.train_trials, seed)
    methods: dict[str, Method] = {"static-pckp": Method("static-pckp", "static", STATIC)}
    methods["tuned-static-pckp"] = tune_static(train + val, executor, trials, seed)
    support_tuned = tune_static(support, executor, trials, seed + 1, draws=TUNE_DRAWS)
    support_tuned.name = "tuned-static-support"
    support_tuned.detail["outcome_evaluations"] = (TUNE_DRAWS + 1) * len(support) * trials
    methods["tuned-static-support"] = support_tuned
    source = fit_method(
        "regression-source",
        "regression",
        "linear",
        V1,
        {V1: cache},
        [o for o in source_out if o.instance_id in train_ids],
        [o for o in source_out if o.instance_id not in train_ids],
        val,
        executor,
        preference_policy=policy,
        seed=seed,
        trials=trials,
    )
    support_out = evaluate_candidates(support, executor, cfg.train_trials, seed + 7)
    evaluations = sum(len(o.outcomes) for o in support_out)
    static_prior = static_prior_weights(PaskCompactionPolicy(), FEATURE_SCHEMAS[V1][0])
    assert source.model is not None
    priors = {"static": static_prior, "source": list(source.model.parameters)}
    for prior_name, prior in priors.items():
        for objective in ("regression", "listwise"):
            name = f"adapt-{objective}-{prior_name}"
            adapted = fit_adapted(
                name,
                objective,
                prior,
                support,
                support_out,
                cache,
                executor,
                preference_policy=policy,
                seed=seed,
                trials=trials,
            )
            adapted.detail["outcome_evaluations"] = evaluations
            methods[name] = adapted
    optimum = {i.instance_id: fast_optimum_quality(i) for i in query}
    from .advantage import static_selections

    static_ids = static_selections(query)
    per_method: dict[str, list[dict]] = {}
    for name, method in methods.items():
        rows = []
        for instance in query:
            selection = run_method(method, instance)
            row = score_selection(instance, selection, optimum[instance.instance_id], 1)
            row["family"] = family_of(instance)
            row["template"] = instance.template_id
            row["calibration_mode"] = None
            row["differs_from_static"] = selection.ids != static_ids[instance.instance_id]
            row["gate_accepted"] = None
            rows.append(row)
        per_method[name] = rows
    return {
        "evidence_kind": "synthetic",
        "protocol": f"{BENCH_VERSION}-fewshot",
        "seed": seed,
        "fold": fold,
        "sizes": {
            "train": len(train),
            "validation": len(val),
            "support": len(support),
            "test": len(query),
        },
        "test_families": sorted({family_of(i) for i in query}),
        "per_method": per_method,
        "validation_quality": {},
        "pair_accuracy": {"n_test_pairs": 0},
        "calibration_error": {},
        "training": {
            n: {"seconds": m.train_seconds, **{k: v for k, v in m.detail.items() if k != "config"}}
            for n, m in methods.items()
        },
        "failures": {},
        "seconds": time.perf_counter() - started,
    }
