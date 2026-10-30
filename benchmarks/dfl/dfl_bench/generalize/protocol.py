"""Folds and the per-fold runner for protocol dfl-generalize-1. SYNTHETIC ORACLE ONLY.

Every number produced here carries ``evidence_kind="synthetic"``: outcomes are Bernoulli draws from
a closed-form quality model, not agent runs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from nailong_agent_sdk.memory.utility_features import FEATURE_SCHEMA_VERSION as V1
from nailong_agent_sdk.memory.utility_features_v2 import (
    FEATURE_SCHEMA_VERSION_V2 as V2,
)
from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMAS

from ..experiment import evaluate_candidates, score_selection
from ..instances import Instance
from ..methods import FeatureCache, pair_accuracy_for_scores, train_ranker, train_set_regression
from ..oracle import SyntheticOracleExecutor
from ..preferences import PreferencePolicy, build_preferences
from ..selection import LEARNED, STATIC, static_utilities
from ..splits import assert_no_leakage, split_instances
from .advantage import fit_gated, static_selections
from .learners import (
    BENCH_VERSION,
    Method,
    SchemaCache,
    build_calibrated,
    decision_quality,
    evaluate_boundary,
    fit_detector,
    fit_method,
    fit_shrunk,
    merge_outcomes,
    run_method,
    tune_static,
)
from .worlds import (
    FAMILIES,
    FAMILY_NAMES,
    build_family,
    build_world,
    family_of,
    fast_optimum_quality,
)

SPLIT_KEYS = ("template", "query", "content")
ALPHAS = (0.25, 0.5, 0.75, 1.0)
LOW_RATIO, HIGH_RATIO = 0.30, 0.90


@dataclass(frozen=True)
class Config:
    templates: int = 4
    per_template: int = 4
    train_trials: int = 8
    validation_trials: int = 16
    preference_policy: PreferencePolicy = PreferencePolicy()
    boundary: bool = True
    include_mlp: bool = False
    plan: str = "full"
    gate_v2_only: bool = False
    support_templates: int = 2


def fold_names() -> list[str]:
    return [
        "iid-template",
        *[f"lofo:{name}" for name in FAMILY_NAMES],
        "holdout-long",
        "budget-low",
        "budget-high",
    ]


def make_fold(
    seed: int, fold: str, cfg: Config
) -> tuple[list[Instance], list[Instance], list[Instance]]:
    world = build_world(seed, cfg.templates, cfg.per_template)
    if fold == "iid-template":
        train, val, test = split_instances(world, (0.6, 0.15, 0.25), seed, SPLIT_KEYS)
    elif fold.startswith("lofo:") or fold == "holdout-long":
        held = (
            {fold.split(":", 1)[1]}
            if fold.startswith("lofo:")
            else {f.name for f in FAMILIES if f.long_history}
        )
        pool = [i for i in world if family_of(i) not in held]
        test = [i for i in world if family_of(i) in held]
        train, val, _ = split_instances(pool, (0.8, 0.2, 0.0), seed, SPLIT_KEYS)
    elif fold in ("budget-low", "budget-high"):
        ratio = LOW_RATIO if fold == "budget-low" else HIGH_RATIO
        train, val, _ = split_instances(world, (0.8, 0.2, 0.0), seed, SPLIT_KEYS)
        test = [
            i
            for spec in FAMILIES
            for i in build_family(spec, seed, cfg.templates, cfg.per_template, ratio, tag="b")
        ]
    else:
        raise ValueError(f"Unknown fold {fold!r}; expected one of {fold_names()}.")
    assert_no_leakage(train, val, test, keys=(*SPLIT_KEYS, "instance"))
    if not (train and val and test):
        raise ValueError(
            f"Fold {fold} produced an empty split ({len(train)}/{len(val)}/{len(test)})."
        )
    return train, val, test


def run_job(job: tuple[int, str, Config]) -> dict:
    if job[2].plan == "fewshot":
        from .fewshot import run_fewshot_fold

        return run_fewshot_fold(job[0], job[1], job[2])
    return run_fold(*job)


def run_fold(seed: int, fold: str, cfg: Config = Config()) -> dict:
    started = time.perf_counter()
    executor = SyntheticOracleExecutor()
    train, val, test = make_fold(seed, fold, cfg)
    everything = train + val + test
    caches = {schema: SchemaCache(everything, schema) for schema in FEATURE_SCHEMAS}
    train_ids = {i.instance_id for i in train}
    outcomes = evaluate_candidates(train + val, executor, cfg.train_trials, seed)
    train_out = [o for o in outcomes if o.instance_id in train_ids]
    val_out = [o for o in outcomes if o.instance_id not in train_ids]
    policy = cfg.preference_policy
    methods: dict[str, Method] = {}
    methods["static-pckp"] = Method("static-pckp", "static", STATIC)
    methods["tuned-static-pckp"] = tune_static(train + val, executor, cfg.validation_trials, seed)

    legacy = FeatureCache(everything)
    prefs = build_preferences(outcomes, policy)
    train_prefs = [p for p in prefs if p.instance_id in train_ids]
    val_prefs = [p for p in prefs if p.instance_id not in train_ids]
    for name, kind in (("linear-dfl", "linear"), ("mlp-dfl", "mlp")):
        if kind == "mlp" and not cfg.include_mlp:
            continue
        trained = train_ranker(name, kind, legacy, train_prefs, val_prefs, seed=seed)
        methods[name] = _legacy(trained)
    regression = train_set_regression("regression-pckp", legacy, train_out, val_out)
    methods["regression-pckp"] = _legacy(regression)

    def fit(name, objective, schema, outcomes_train=train_out, **kwargs):
        return fit_method(
            name,
            objective,
            kwargs.pop("model_type", "linear"),
            schema,
            caches,
            outcomes_train,
            val_out,
            val,
            executor,
            preference_policy=policy,
            seed=seed,
            trials=cfg.validation_trials,
            **kwargs,
        )

    plan = [
        ("pairwise-v1", "pairwise", V1, {}),
        ("regret-pairwise-v1", "regret-pairwise", V1, {}),
        (
            "regret-pairwise-v1-unnormalized",
            "regret-pairwise",
            V1,
            {"normalize": False, "cap_ratio": None},
        ),
        ("listwise-v1", "listwise", V1, {}),
        ("regression-v1", "regression", V1, {}),
        ("regression-v2", "regression", V2, {}),
        ("listwise-v2", "listwise", V2, {}),
        ("regret-pairwise-v2", "regret-pairwise", V2, {}),
        ("dro-pairwise-v1", "pairwise", V1, {"group_dro": True}),
        ("dro-listwise-v2", "listwise", V2, {"group_dro": True}),
    ]
    if cfg.plan in ("shrink", "gate"):
        plan = [row for row in plan if row[0] in ("pairwise-v1", "regression-v1")]

    failures: dict[str, str] = {}
    for name, objective, schema, extra in plan:
        try:
            methods[name] = fit(name, objective, schema, **extra)
        except ValueError as error:
            failures[name] = str(error)

    shrunk_plan = [
        ("shrunk-regression-v1", "regression", V1, {}),
        ("shrunk-listwise-v1", "listwise", V1, {}),
        ("shrunk-regret-pairwise-v1", "regret-pairwise", V1, {}),
        ("shrunk-dro-pairwise-v1", "pairwise", V1, {"group_dro": True}),
        ("shrunk-regression-v2", "regression", V2, {}),
        ("shrunk-listwise-v2", "listwise", V2, {}),
    ]
    if cfg.plan == "gate":
        try:
            methods["pairwise-v1-equalcard"] = fit(
                "pairwise-v1-equalcard", "pairwise", V1, equal_cardinality=True
            )
        except ValueError as error:
            failures["pairwise-v1-equalcard"] = str(error)
        try:
            methods["shrunk-regression-v2"] = fit_shrunk(
                "shrunk-regression-v2",
                "regression",
                V2,
                caches,
                train,
                train_out,
                executor,
                preference_policy=policy,
                seed=seed,
                trials=cfg.validation_trials,
            )
        except ValueError as error:
            failures["shrunk-regression-v2"] = str(error)
        for schema, tag in ((V1, "v1"), (V2, "v2")):
            if cfg.gate_v2_only and schema == V1:
                continue
            gated, ungated = fit_gated(
                f"advantage-{tag}",
                schema,
                caches,
                train,
                train_out,
                executor,
                preference_policy=policy,
                seed=seed,
                trials=cfg.validation_trials,
            )
            methods[gated.name] = gated
            methods[ungated.name] = ungated
    if cfg.plan == "shrink":
        for name, objective, schema, extra in shrunk_plan:
            try:
                methods[name] = fit_shrunk(
                    name,
                    objective,
                    schema,
                    caches,
                    train,
                    train_out,
                    executor,
                    preference_policy=policy,
                    seed=seed,
                    trials=cfg.validation_trials,
                    **extra,
                )
            except ValueError as error:
                failures[name] = str(error)

    validation_scores = {
        name: decision_quality(m, val, executor, cfg.validation_trials)
        for name, m in methods.items()
    }
    if cfg.boundary and "regression-v1" in methods:
        extra_outcomes = evaluate_boundary(
            train, methods["regression-v1"], executor, cfg.train_trials, seed
        )
        enlarged = merge_outcomes(train_out, extra_outcomes)
        for name, objective, schema in (
            ("listwise-v1+boundary", "listwise", V1),
            ("regret-pairwise-v1+boundary", "regret-pairwise", V1),
        ):
            try:
                methods[name] = fit(name, objective, schema, outcomes_train=enlarged)
                validation_scores[name] = decision_quality(
                    methods[name], val, executor, cfg.validation_trials
                )
            except ValueError as error:
                failures[name] = str(error)

    shrunk = [m for n, m in methods.items() if n.startswith("shrunk-")]
    if shrunk and cfg.plan == "shrink":

        def inner_quality(m: Method) -> float:
            d = m.detail
            return (
                d["inner_static_quality"]
                if d["fell_back_to_static"]
                else d["inner_mean_quality"][d["shrink_l2"]]
            )

        best = max(shrunk, key=inner_quality)
        methods["shrunk-selected"] = _alias(best, "shrunk-selected")
        methods["shrunk-selected"].detail["chosen"] = best.name
        methods["shrunk-selected"].detail["inner_quality"] = inner_quality(best)
        validation_scores["shrunk-selected"] = decision_quality(
            methods["shrunk-selected"], val, executor, cfg.validation_trials
        )
    new_learned = [
        m
        for m in methods.values()
        if m.family == "learned" and m.detail.get("objective") and not m.name.startswith("shrunk-")
    ]
    if new_learned and cfg.plan == "full":
        base = max(
            new_learned,
            key=lambda m: (
                validation_scores.get(m.name)
                or decision_quality(m, val, executor, cfg.validation_trials)
            ),
        )
        schema = base.schema or V1
        detector = fit_detector(train, val, schema)
        alpha, inner = _choose_alpha(
            base, train, train_out, val, val_out, caches, executor, cfg, policy, seed, detector
        )
        methods["calibrated-pckp"] = build_calibrated(
            base, alpha, detector, FEATURE_SCHEMAS[schema][1]
        )
        methods["calibrated-pckp"].detail["inner_lofo"] = inner
    for name, m in methods.items():
        validation_scores.setdefault(
            name, decision_quality(m, val, executor, cfg.validation_trials)
        )
    selectable = [n for n in methods if n != "combined-validation"]
    chosen = max(selectable, key=lambda n: validation_scores[n])
    methods["combined-validation"] = _alias(methods[chosen], "combined-validation")
    methods["combined-validation"].detail["chosen"] = chosen

    static_ids = static_selections(test)
    optimum = {i.instance_id: fast_optimum_quality(i) for i in test}
    per_method: dict[str, list[dict]] = {}
    for name, method in methods.items():
        rows = []
        for instance in test:
            selection = run_method(method, instance)
            row = score_selection(instance, selection, optimum[instance.instance_id], 1)
            row["family"] = family_of(instance)
            row["template"] = instance.template_id
            row["calibration_mode"] = selection.meta.get("calibration_mode")
            row["differs_from_static"] = selection.ids != static_ids[instance.instance_id]
            row["gate_accepted"] = selection.meta.get("gate_accepted")
            rows.append(row)
        per_method[name] = rows

    test_candidates = evaluate_candidates(test, executor, cfg.train_trials, seed + 99)
    test_prefs = build_preferences(test_candidates, policy)
    pair_accuracy = {"n_test_pairs": len(test_prefs)}
    static_scores = {i.instance_id: static_utilities(i) for i in test}
    pair_accuracy["static-pckp"] = pair_accuracy_for_scores(static_scores, test_prefs)
    for name, method in methods.items():
        if method.model is not None and method.schema is not None:
            cache = caches[method.schema]
            scores = {
                i.instance_id: {
                    e: method.model.score(v) for e, v in cache.get(i.instance_id).items()
                }
                for i in test
            }
            pair_accuracy[name] = pair_accuracy_for_scores(scores, test_prefs)
    calibration_error = _regression_calibration(methods, caches, test, test_candidates, cfg)
    return {
        "evidence_kind": "synthetic",
        "protocol": BENCH_VERSION,
        "seed": seed,
        "fold": fold,
        "sizes": {"train": len(train), "validation": len(val), "test": len(test)},
        "test_families": sorted({family_of(i) for i in test}),
        "per_method": per_method,
        "validation_quality": validation_scores,
        "pair_accuracy": pair_accuracy,
        "calibration_error": calibration_error,
        "training": {
            n: {"seconds": m.train_seconds, **{k: v for k, v in m.detail.items() if k != "config"}}
            for n, m in methods.items()
        },
        "failures": failures,
        "seconds": time.perf_counter() - started,
    }


def _legacy(trained) -> Method:
    from ..methods import TrainedMethod

    assert isinstance(trained, TrainedMethod)
    return Method(
        trained.name,
        "learned",
        LEARNED,
        trained.predictor,
        None,
        trained.model,
        V1,
        trained.train_seconds,
        {"legacy": True},
    )


def _alias(method: Method, name: str) -> Method:
    return Method(
        name,
        method.family,
        method.policy,
        method.predictor,
        method.quantization,
        method.model,
        method.schema,
        method.train_seconds,
        dict(method.detail),
    )


def _choose_alpha(
    base, train, train_out, val, val_out, caches, executor, cfg, policy, seed, detector
):
    """Pick the learned/static mixing weight by inner leave-one-family-out on training families,
    maximising the worst held-out training family. The outer test fold is never consulted."""

    families = sorted({family_of(i) for i in train})
    if len(families) < 3 or not base.detail.get("objective"):
        return 1.0, {"skipped": "fewer than 3 training families or non-refittable base"}
    schema = base.schema or V1
    results: dict[float, list[float]] = {a: [] for a in ALPHAS}
    for held in families:
        inner_train = [i for i in train if family_of(i) != held]
        inner_held = [i for i in train if family_of(i) == held]
        ids = {i.instance_id for i in inner_train}
        refit = fit_method(
            base.name,
            base.detail["objective"],
            base.detail["model_type"],
            schema,
            caches,
            [o for o in train_out if o.instance_id in ids],
            val_out,
            val,
            executor,
            preference_policy=policy,
            seed=seed,
            trials=cfg.validation_trials,
            group_dro=base.detail["group_dro"],
            l2_grid=(base.detail["l2"],),
        )
        inner_detector = fit_detector(inner_train, val, schema)
        for alpha in ALPHAS:
            candidate = build_calibrated(refit, alpha, inner_detector, FEATURE_SCHEMAS[schema][1])
            results[alpha].append(
                decision_quality(candidate, inner_held, executor, cfg.validation_trials)
            )
    summary = {a: {"worst": min(v), "mean": sum(v) / len(v)} for a, v in results.items()}
    best = max(ALPHAS, key=lambda a: (summary[a]["worst"], summary[a]["mean"]))
    return best, {"alphas": {str(a): s for a, s in summary.items()}, "chosen": best}


def _regression_calibration(methods, caches, test, candidates, cfg) -> dict[str, float]:
    result: dict[str, float] = {}
    by_id = {i.instance_id for i in test}
    pool = [
        o
        for o in candidates
        if o.instance_id in by_id and len(o.outcomes) >= cfg.preference_policy.min_trials
    ]
    for name, method in methods.items():
        if method.detail.get("objective") != "regression" or method.model is None:
            continue
        cache = caches[method.schema or V1]
        errors = []
        for outcome in pool:
            features = cache.get(outcome.instance_id)
            predicted = sum(method.model.score(features[e]) for e in outcome.selection)
            errors.append(abs(predicted - outcome.mean_outcome))
        if errors:
            result[name] = sum(errors) / len(errors)
    return result
