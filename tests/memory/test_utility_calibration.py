import random

import pytest

from nailong_agent_sdk.memory.episode_models import (
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy
from nailong_agent_sdk.memory.utility_calibration import (
    CalibratedUtilityPredictor,
    FallbackPolicy,
    FeatureShiftDetector,
    PolicyStaticPredictor,
    dispersion,
    ensemble_disagreement,
    mix_utilities,
    scale_normalize,
)
from nailong_agent_sdk.memory.utility_features import extract_features
from tests.support.learned_memory import build_store, episode_ids, fixed_predictor

STATIC = PaskCompactionPolicy()
LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)
QUERY = "explore alpha"


def fresh(seed=7, predictor=None):
    store, _, _ = build_store(random.Random(seed), 6, 4, utility_predictor=predictor)
    return store


def live_ids(store):
    return {r.id for r in store.list() if r.state is not EpisodeState.COMPACTED}


def test_scale_normalisation_is_a_pure_rescaling_and_mixing_validates_its_inputs():
    assert scale_normalize({"a": -4.0, "b": 2.0}) == {"a": -1.0, "b": 0.5}
    assert scale_normalize({"a": 0.0}) == {"a": 0.0}
    learned, static = {"a": 2.0, "b": -2.0}, {"a": 1.0, "b": 3.0}
    assert mix_utilities(learned, static, 1.0) == {"a": 1.0, "b": -1.0}
    assert mix_utilities(learned, static, 0.0) == pytest.approx({"a": 1 / 3, "b": 1.0})
    assert mix_utilities(learned, static, 0.5)["a"] == pytest.approx(0.5 * 1.0 + 0.5 / 3)
    with pytest.raises(ValueError, match="alpha must be in"):
        mix_utilities(learned, static, 1.5)
    with pytest.raises(ValueError, match="different episodes"):
        mix_utilities({"a": 1.0}, {"b": 1.0}, 0.5)


def test_policy_static_predictor_reproduces_the_stores_static_utilities():
    store = fresh()
    result = store.compact(store.estimate_tokens() // 2, relevance_query=QUERY, policy=STATIC)
    rows = {r["episode_id"]: r["utility"] for r in result.dossier["static_utilities"]}
    other = fresh()
    context = other._retention_context(other.estimate_tokens() // 2, QUERY)
    predicted = PolicyStaticPredictor(STATIC).predict(context)
    assert predicted == pytest.approx(rows)


def test_alpha_zero_calibration_selects_exactly_what_static_pckp_selects():
    budget = fresh().estimate_tokens() // 2
    static_store = fresh()
    static_store.compact(budget, relevance_query=QUERY, policy=STATIC)
    learned = fixed_predictor({}, ids=episode_ids(10), default=5.0)
    calibrated = CalibratedUtilityPredictor(
        learned, PolicyStaticPredictor(STATIC), FallbackPolicy(alpha=0.0)
    )
    store = fresh(predictor=calibrated)
    result = store.compact(budget, relevance_query=QUERY, policy=LEARNED)
    assert live_ids(store) == live_ids(static_store)
    audit = result.dossier["learned_ranking"]["calibration"]
    assert audit["mode"] == "mixed" and audit["alpha"] == 0.0
    assert result.dossier["learned_ranking"]["solver_status"] == "optimal"


def test_clip_fraction_triggers_a_logged_static_fallback_that_matches_static_pckp():
    budget = fresh().estimate_tokens() // 2
    static_store = fresh()
    static_store.compact(budget, relevance_query=QUERY, policy=STATIC)
    negative = fixed_predictor({}, ids=episode_ids(10), default=-3.0)
    calibrated = CalibratedUtilityPredictor(
        negative, PolicyStaticPredictor(STATIC), FallbackPolicy(alpha=1.0, max_clip_fraction=0.5)
    )
    store = fresh(predictor=calibrated)
    result = store.compact(budget, relevance_query=QUERY, policy=LEARNED)
    audit = result.dossier["learned_ranking"]["calibration"]
    assert audit["mode"] == "static-fallback" and audit["alpha"] == 0.0
    assert audit["reasons"] and "clip_fraction 1.000 > 0.5" in audit["reasons"][0]
    assert audit["evidence"]["clip_fraction"] == 1.0
    assert live_ids(store) == live_ids(static_store)


def test_low_dispersion_triggers_fallback_and_healthy_predictions_do_not():
    flat = fixed_predictor({}, ids=episode_ids(10), default=1.0)
    varied = fixed_predictor({f"episode-{n}": float(n) for n in range(1, 11)})
    policy = FallbackPolicy(alpha=1.0, min_dispersion=0.05)
    context = fresh()._retention_context(1_000, QUERY)
    flat_cal = CalibratedUtilityPredictor(flat, PolicyStaticPredictor(STATIC), policy)
    flat_cal.predict(context)
    assert flat_cal.last_audit["mode"] == "static-fallback"
    varied_cal = CalibratedUtilityPredictor(varied, PolicyStaticPredictor(STATIC), policy)
    varied_cal.predict(context)
    assert varied_cal.last_audit["mode"] == "mixed" and varied_cal.last_audit["alpha"] == 1.0
    assert dispersion({"a": 1.0, "b": 1.0}) == 0.0


def test_calibrated_selection_keeps_every_pckp_guarantee():
    budget = fresh().estimate_tokens() // 3
    learned = fixed_predictor({f"episode-{n}": float(n) for n in range(1, 11)})
    calibrated = CalibratedUtilityPredictor(
        learned, PolicyStaticPredictor(STATIC), FallbackPolicy(alpha=0.5)
    )
    store = fresh(predictor=calibrated)
    protected = ["episode-2"]
    result = store.compact(
        budget, protected_episode_ids=protected, relevance_query=QUERY, policy=LEARNED
    )
    retained = live_ids(store)
    assert "episode-2" in retained
    assert store.estimate_tokens() <= budget
    for record in store.list():
        if record.id in retained:
            assert set(record.depends_on) <= retained
    assert result.dossier["solver"]["status"] == "optimal"
    assert result.dossier["learned_ranking"]["status"] == "applied"


def test_shift_detector_flags_a_context_far_from_training_and_calibrates_on_validation():
    rng = random.Random(0)

    def features(centre):
        return {f"e{i}": (1.0, centre + rng.gauss(0, 0.05), rng.random()) for i in range(8)}

    training = [features(0.3) for _ in range(30)]
    validation = [features(0.3) for _ in range(30)]
    detector = FeatureShiftDetector().fit(training)
    threshold = detector.calibrate(validation, quantile=0.95)
    assert threshold == detector.threshold
    assert detector.score(features(0.3)) <= threshold * 1.5
    assert detector.score(features(0.9)) > threshold
    with pytest.raises(ValueError, match="not been fitted"):
        FeatureShiftDetector().score(training[0])
    with pytest.raises(ValueError, match="at least 2 training contexts"):
        FeatureShiftDetector().fit(training[:1])
    with pytest.raises(ValueError, match="at least one validation context"):
        detector.calibrate([])


def test_shift_evidence_drives_a_fallback_with_the_evidence_in_the_audit():
    stores = [fresh(seed) for seed in range(12)]
    contexts = [s._retention_context(s.estimate_tokens() // 2, QUERY) for s in stores]
    vectors = [extract_features(c) for c in contexts]
    detector = FeatureShiftDetector().fit(vectors[:8])
    detector.calibrate(vectors[8:], quantile=1.0)
    learned = fixed_predictor({}, ids=episode_ids(10), default=2.0)
    policy = FallbackPolicy(alpha=1.0, max_shift=detector.threshold * 0.01)
    calibrated = CalibratedUtilityPredictor(
        learned,
        PolicyStaticPredictor(STATIC),
        policy,
        detector=detector,
        shift_features=extract_features,
    )
    calibrated.predict(contexts[0])
    assert calibrated.last_audit["mode"] == "static-fallback"
    assert "shift" in calibrated.last_audit["evidence"]
    with pytest.raises(ValueError, match="max_shift needs"):
        CalibratedUtilityPredictor(
            learned, PolicyStaticPredictor(STATIC), FallbackPolicy(max_shift=1.0)
        )


def test_ensemble_disagreement_is_zero_for_identical_members_and_positive_otherwise():
    a = {"x": 1.0, "y": 2.0}
    assert ensemble_disagreement([a, dict(a)]) == 0.0
    assert ensemble_disagreement([a, {"x": 2.0, "y": 1.0}]) > 0.0
    with pytest.raises(ValueError, match="at least 2"):
        ensemble_disagreement([a])


def test_incomplete_static_coverage_fails_by_name_through_the_store():
    partial = fixed_predictor({"episode-1": 1.0})
    calibrated = CalibratedUtilityPredictor(
        fixed_predictor({}, ids=episode_ids(10), default=1.0), partial, FallbackPolicy()
    )
    store = fresh(predictor=calibrated)
    result = store.compact(store.estimate_tokens() // 2, relevance_query=QUERY, policy=LEARNED)
    ranking = result.dossier["learned_ranking"]
    assert ranking["status"] == "fallback-static-pckp"
    assert (
        ranking["error_code"] == "UTILITY_CALIBRATION_INCOMPLETE"
        or ranking["error_code"] == "UTILITY_PREDICTION_INCOMPLETE"
    )


def test_quantization_floor_still_applies_to_calibrated_utilities():
    learned = fixed_predictor({}, ids=episode_ids(10), default=-1.0)
    calibrated = CalibratedUtilityPredictor(
        learned, PolicyStaticPredictor(STATIC), FallbackPolicy(alpha=1.0)
    )
    store, _, _ = build_store(
        random.Random(7),
        6,
        4,
        utility_predictor=calibrated,
        utility_quantization=QuantizationPolicy(floor=1),
    )
    result = store.compact(store.estimate_tokens() // 2, relevance_query=QUERY, policy=LEARNED)
    assert all(v >= 1 for v in result.dossier["learned_ranking"]["quantized_utilities"].values())


def test_calibrated_selection_matches_exhaustive_enumeration_of_the_quantized_objective():
    from itertools import combinations

    from nailong_agent_sdk.memory.episode_scoring import _episode_tokens

    learned = fixed_predictor({f"episode-{n}": float((n * 7) % 5 - 1) for n in range(1, 8)})
    calibrated = CalibratedUtilityPredictor(
        learned, PolicyStaticPredictor(STATIC), FallbackPolicy(alpha=0.6)
    )
    store, _, _ = build_store(
        random.Random(3),
        4,
        3,
        utility_predictor=calibrated,
        size=120,
        utility_quantization=QuantizationPolicy(floor=1),
    )
    records = {r.id: r for r in store.list()}
    costs = {i: _episode_tokens(r) for i, r in records.items()}
    budget = sum(costs.values()) // 2
    result = store.compact(budget, relevance_query=QUERY, policy=LEARNED)
    quantized = result.dossier["learned_ranking"]["quantized_utilities"]
    chosen = live_ids(store)
    best = 0
    for size in range(len(records) + 1):
        for subset in combinations(sorted(records), size):
            members = set(subset)
            if sum(costs[i] for i in members) > budget:
                continue
            if any(not set(records[i].depends_on) <= members for i in members):
                continue
            best = max(best, sum(quantized[i] for i in members))
    assert sum(quantized[i] for i in chosen) == best
    assert result.dossier["solver"]["status"] == "optimal"


def test_the_static_policy_is_a_linear_model_and_its_prior_weights_reproduce_it():
    from nailong_agent_sdk.memory.utility_calibration import static_prior_weights
    from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
    from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_NAMES_V2, extract_features_v2

    policy = PaskCompactionPolicy(task_relevance_weight=0.5, recency_weight=0.2)
    store = fresh()
    context = store._retention_context(store.estimate_tokens() // 2, QUERY)
    static = PolicyStaticPredictor(policy).predict(context)
    for names, extract in (
        (FEATURE_NAMES, extract_features),
        (FEATURE_NAMES_V2, extract_features_v2),
    ):
        weights = static_prior_weights(policy, names)
        features = extract(context)
        for episode_id, value in static.items():
            assert sum(
                w * x for w, x in zip(weights, features[episode_id], strict=True)
            ) == pytest.approx(value)
    with pytest.raises(ValueError, match="required to express"):
        static_prior_weights(policy, ("bias",))
