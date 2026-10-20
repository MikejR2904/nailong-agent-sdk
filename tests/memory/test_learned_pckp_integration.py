import json
import random

import pytest

from nailong_agent_sdk.foundations.optimization import PckpStatus
from nailong_agent_sdk.memory.episode_models import (
    CompactionStatus,
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import (
    LearnedUtilityPredictor,
    QuantizationPolicy,
    quantize_utilities,
)
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import LinearRankingModel
from tests.support.learned_memory import (
    ConstantFeaturePredictor,
    MappingPredictor,
    RaisingPredictor,
    build_store,
)

LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)
STATIC = PaskCompactionPolicy(strategy=CompactionStrategy.EXACT_PCKP)


def live_ids(store):
    return {r.id for r in store.list() if r.state is not EpisodeState.COMPACTED}


def brute_force_utility(store, utilities, budget, mandatory, scale=1_000_000):
    records = {r.id: r for r in store.list() if r.state is not EpisodeState.COMPACTED}
    ids = sorted(records)
    best = None
    for mask in range(1 << len(ids)):
        chosen = {ids[k] for k in range(len(ids)) if mask >> k & 1}
        if not mandatory <= chosen:
            continue
        if any(p not in chosen for i in chosen for p in records[i].depends_on):
            continue
        if sum(_episode_tokens(records[i]) for i in chosen) > budget:
            continue
        value = sum(round(max(0.0, utilities[i]) * scale) for i in chosen)
        best = value if best is None else max(best, value)
    return best


def mandatory_closure(store, protected, active, ids):
    records = {r.id: r for r in store.list()}
    seed = set(protected) | ({active} if active else set())
    seed |= {i for i in ids if records[i].state is EpisodeState.OPEN}
    closure, pending = set(seed), list(seed)
    while pending:
        for p in records[pending.pop()].depends_on:
            if p not in closure:
                closure.add(p)
                pending.append(p)
    return closure


@pytest.mark.parametrize("seed", range(25))
def test_learned_selection_is_optimal_for_the_quantized_learned_objective(seed):
    rng = random.Random(seed)
    store, explore, actions = build_store(rng, rng.randint(2, 5), rng.randint(1, 4))
    ids = explore + actions
    utilities = {i: rng.uniform(-0.2, 3.0) for i in ids}
    store = rebuild(store, MappingPredictor(utilities))
    protected = set(rng.sample(ids, rng.choice([0, 0, 1])))
    active = rng.choice([None, rng.choice(ids)])
    budget = rng.randint(1, store.estimate_tokens())
    before = {r.id: r for r in store.list()}
    mandatory = mandatory_closure(store, protected, active, ids)
    result = store.compact(
        budget, protected_episode_ids=protected, active_episode_id=active, policy=LEARNED
    )
    assert result.strategy is CompactionStrategy.LEARNED_PCKP
    solver = result.dossier["solver"]
    if solver["status"] == PckpStatus.INFEASIBLE_MANDATORY.value:
        assert result.status in {
            CompactionStatus.PROTECTED_OVER_BUDGET,
            CompactionStatus.CONTEXT_DEADLOCK,
        }
        return
    retained = live_ids(store)
    assert mandatory <= retained
    assert all(p in retained for i in retained for p in before[i].depends_on)
    assert store.estimate_tokens() <= budget
    assert solver["status"] == "optimal" and solver["optimality_gap"] == "0"
    expected = brute_force_utility(store_from(before), utilities, budget, mandatory)
    assert solver["utility"] == expected


def store_from(records):
    store = InMemoryEpisodeStore()
    store._records = dict(records)
    return store


def rebuild(store, predictor, **kwargs):
    clone = InMemoryEpisodeStore(utility_predictor=predictor, **kwargs)
    clone._records = dict(store._records)
    clone._next_id = store._next_id
    return clone


def small_store(predictor=None, **kwargs):
    store, explore, actions = build_store(
        random.Random(7), 4, 3, utility_predictor=predictor, **kwargs
    )
    return store, explore, actions


def test_static_default_is_untouched_by_an_attached_predictor():
    plain, _, _ = small_store()
    armed, _, _ = small_store(MappingPredictor(default=5.0))
    budget = plain.estimate_tokens() // 2
    a = plain.compact(budget, relevance_query="explore")
    b = armed.compact(budget, relevance_query="explore")
    assert a.strategy is CompactionStrategy.EXACT_PCKP
    assert a.model_dump() == b.model_dump()
    assert armed.utility_predictor.calls == 0


def test_applied_dossier_is_auditable_and_never_stores_the_query_text():
    predictor = MappingPredictor({}, default=1.0)
    store, explore, _ = small_store(predictor)
    secret = "customer-ssn-123-45-6789 rotate keys"
    budget = store.estimate_tokens() // 2
    result = store.compact(budget, relevance_query=secret, policy=LEARNED)
    audit = result.dossier["learned_ranking"]
    assert audit["status"] == "applied"
    assert audit["predictor"]["predictor_id"] == "stub" and audit["predictor"]["version"] == "1"
    assert audit["artifact_digest"] == "0" * 64
    assert audit["feature_schema_version"] == "episode-utility-features-v1"
    assert audit["task_digest"] == result.dossier["relevance_query_hash"]
    assert set(audit["predicted_utilities"]) == set(audit["quantized_utilities"])
    assert audit["selected_episode_ids"] == result.dossier["retained_episode_ids"]
    assert audit["solver_status"] == "optimal" and audit["optimality_gap"] == "0"
    assert audit["token_budget"] == budget
    assert audit["selected_token_cost"] == result.dossier["solver"]["token_cost"]
    assert "customer-ssn" not in json.dumps(result.model_dump(mode="json"))


def test_quantization_clips_and_records_out_of_range_values():
    quantized, below, above = quantize_utilities(
        {"a": -1.0, "b": 0.0, "c": 0.0000004, "d": 5e6},
        QuantizationPolicy(scale=1000, ceiling=10.0),
    )
    assert quantized == {"a": 0, "b": 0, "c": 0, "d": 10_000}
    assert below == ["a"] and above == ["d"]


def test_negative_predictions_are_clipped_and_reported():
    store, explore, _ = small_store(MappingPredictor({}, default=-2.0))
    result = store.compact(store.estimate_tokens() // 2, policy=LEARNED)
    audit = result.dossier["learned_ranking"]
    assert set(audit["clipped_below_zero"]) == set(audit["quantized_utilities"])
    assert set(audit["quantized_utilities"].values()) == {0}


def test_all_zero_utilities_retain_only_the_mandatory_closure():
    store, explore, actions = small_store(MappingPredictor({}, default=0.0))
    store.compact(store.estimate_tokens() // 3, policy=LEARNED, active_episode_id=actions[0])
    keep = mandatory_closure(store, set(), actions[0], live_ids(store) | set())
    assert live_ids(store) == keep


def test_infeasible_mandatory_set_is_reported_with_learned_strategy():
    store, explore, actions = small_store(MappingPredictor())
    result = store.compact(1, protected_episode_ids=set(explore), policy=LEARNED)
    assert result.status is CompactionStatus.PROTECTED_OVER_BUDGET
    assert result.dossier["solver"]["status"] == "infeasible-mandatory"
    assert result.strategy is CompactionStrategy.LEARNED_PCKP
    assert set(explore) <= live_ids(store)


def dag_store(predictor, nodes):
    store = InMemoryEpisodeStore(utility_predictor=predictor)
    explore = []
    for index in range(nodes):
        record = store.open_exploratory("a", content={"result": {"text": "x" * (80 + 10 * index)}})
        store.close(record.id, description=f"e{index}")
        explore.append(record.id)
    for index in range(nodes - 1):
        record = store.open_action("a", explore[index : index + 2], content={"result": "y" * 90})
        store.close(record.id)
    return store


def test_best_effort_status_is_preserved_when_the_branch_limit_stops_the_search():
    store = dag_store(MappingPredictor({}, default=1.0), 8)
    policy = PaskCompactionPolicy(
        strategy=CompactionStrategy.LEARNED_PCKP, exact_max_branch_nodes=1
    )
    result = store.compact(store.estimate_tokens() // 2, policy=policy)
    solver = result.dossier["solver"]
    assert solver["status"] == "best-effort"
    assert result.dossier["learned_ranking"]["solver_status"] == "best-effort"
    assert "not proven optimal" in solver["diagnostics"][0]
    retained = live_ids(store)
    assert all(p in retained for r in store.list() if r.id in retained for p in r.depends_on)


@pytest.mark.parametrize(
    ("predictor", "code"),
    [
        (None, "UTILITY_PREDICTOR_MISSING"),
        (RaisingPredictor(), "UTILITY_PREDICTOR_FAILED"),
        (ConstantFeaturePredictor(float("nan")), "UTILITY_PREDICTION_NONFINITE"),
        (ConstantFeaturePredictor(float("inf")), "UTILITY_PREDICTION_NONFINITE"),
        (ConstantFeaturePredictor("high"), "UTILITY_PREDICTION_NONFINITE"),
    ],
)
def test_predictor_failures_fall_back_to_the_static_solution_with_a_named_error(predictor, code):
    store, _, _ = small_store(predictor)
    reference, _, _ = small_store()
    budget = store.estimate_tokens() // 2
    result = store.compact(budget, policy=LEARNED)
    expected = reference.compact(budget, policy=STATIC)
    assert result.strategy is CompactionStrategy.EXACT_PCKP
    assert result.compacted_episode_ids == expected.compacted_episode_ids
    audit = result.dossier["learned_ranking"]
    assert audit["status"] == "fallback-static-pckp" and audit["error_code"] == code
    assert audit["error"]


def test_failure_message_names_the_predictor_and_exception():
    store, _, _ = small_store(RaisingPredictor())
    audit = store.compact(store.estimate_tokens() // 2, policy=LEARNED).dossier["learned_ranking"]
    assert 'Predictor "stub" version 1 raised RuntimeError: model exploded' == audit["error"]


def test_incomplete_predictions_name_the_missing_episodes():
    class Partial(MappingPredictor):
        def predict(self, context):
            return {r.id: 1.0 for r in context.records[:-1]}

    store, _, _ = small_store(Partial())
    audit = store.compact(store.estimate_tokens() // 2, policy=LEARNED).dossier["learned_ranking"]
    assert audit["error_code"] == "UTILITY_PREDICTION_INCOMPLETE"
    assert store.list()[-1].id in audit["error"]


def test_compaction_never_trains_or_mutates_the_model(monkeypatch):
    import nailong_agent_sdk.memory.utility_training as training

    def forbidden(*args, **kwargs):
        raise AssertionError("training was invoked during inference")

    monkeypatch.setattr(training, "train_ranking_model", forbidden)
    model = LinearRankingModel([0.01] * len(FEATURE_NAMES))
    predictor = LearnedUtilityPredictor(
        model, predictor_id="p", version="1", artifact_digest="d" * 64
    )
    store, _, _ = small_store(predictor)
    before = model.parameters
    result = store.compact(store.estimate_tokens() // 2, relevance_query="explore", policy=LEARNED)
    assert result.dossier["learned_ranking"]["status"] == "applied"
    assert model.parameters == before


def test_a_learned_policy_with_no_static_weight_is_rejected():
    with pytest.raises(ValueError, match="LEARNED_PCKP requires a positive additive utility"):
        PaskCompactionPolicy(
            strategy=CompactionStrategy.LEARNED_PCKP,
            task_relevance_weight=0,
            dependency_centrality_weight=0,
            provenance_weight=0,
            recency_weight=0,
            frequency_weight=0,
            diversity_weight=1,
        )


def test_within_budget_short_circuits_before_the_predictor_runs():
    predictor = MappingPredictor()
    store, _, _ = small_store(predictor)
    result = store.compact(store.estimate_tokens() * 2, policy=LEARNED)
    assert result.status is CompactionStatus.WITHIN_BUDGET and predictor.calls == 0


def test_utility_ordering_drives_retention():
    store, explore, actions = small_store(MappingPredictor())
    target = explore[2]
    store._utility_predictor = MappingPredictor({target: 100.0}, default=0.001)
    budget = max(_episode_tokens(store.get(i)) for i in explore) * 2
    store.compact(budget, policy=LEARNED)
    assert target in live_ids(store)
