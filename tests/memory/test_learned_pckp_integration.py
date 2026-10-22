import ast
import json
import random
from pathlib import Path

import pytest

from nailong_agent_sdk.memory.episode_models import (
    CompactionStatus,
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import (
    FixedUtilityPredictor,
    LearnedUtilityPredictor,
    QuantizationPolicy,
    quantize_utilities,
)
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import LinearRankingModel
from tests.support.learned_memory import (
    build_store,
    episode_ids,
    fixed_predictor,
    mismatched_model_predictor,
)

LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)
STATIC = PaskCompactionPolicy(strategy=CompactionStrategy.EXACT_PCKP)
SOURCE = Path(__file__).resolve().parents[2] / "src" / "nailong_agent_sdk" / "memory"


def snapshot(store):
    return {r.id: r for r in store.list()}


def live_ids(store):
    return {r.id for r in store.list() if r.state is not EpisodeState.COMPACTED}


def closure(records, seeds):
    found, pending = set(seeds), list(seeds)
    while pending:
        for prerequisite in records[pending.pop()].depends_on:
            if prerequisite not in found:
                found.add(prerequisite)
                pending.append(prerequisite)
    return found


def best_quantized_utility(records, utilities, budget, mandatory, scale=1_000_000):
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


def small_store(predictor=None):
    return build_store(random.Random(7), 4, 3, utility_predictor=predictor)


def test_learned_selection_is_optimal_for_the_quantized_learned_objective():
    infeasible = 0
    for seed in range(25):
        rng = random.Random(seed)
        n_exploratory, n_action = rng.randint(2, 5), rng.randint(1, 4)
        ids = episode_ids(n_exploratory + n_action)
        utilities = {i: rng.uniform(-0.2, 3.0) for i in ids}
        store, _, _ = build_store(
            rng, n_exploratory, n_action, utility_predictor=fixed_predictor(utilities)
        )
        protected = set(rng.sample(ids, rng.choice([0, 0, 1])))
        active = rng.choice([None, rng.choice(ids)])
        budget = rng.randint(1, store.estimate_tokens())
        before = snapshot(store)
        mandatory = closure(before, protected | ({active} if active else set()))
        result = store.compact(
            budget, protected_episode_ids=protected, active_episode_id=active, policy=LEARNED
        )
        assert result.strategy is CompactionStrategy.LEARNED_PCKP
        solver = result.dossier["solver"]
        if solver["status"] == "infeasible-mandatory":
            infeasible += 1
            assert result.status in {
                CompactionStatus.PROTECTED_OVER_BUDGET,
                CompactionStatus.CONTEXT_DEADLOCK,
            }
            continue
        retained = live_ids(store)
        assert mandatory <= retained
        assert all(p in retained for i in retained for p in before[i].depends_on)
        assert store.estimate_tokens() <= budget
        assert solver["status"] == "optimal" and solver["optimality_gap"] == "0"
        assert solver["utility"] == best_quantized_utility(before, utilities, budget, mandatory)
    assert infeasible < 25


def test_static_default_is_untouched_by_an_attached_predictor():
    plain, _, _ = small_store()
    predictor = fixed_predictor(ids=episode_ids(7), default=5.0)
    armed, _, _ = small_store(predictor)
    budget = plain.estimate_tokens() // 2
    a = plain.compact(budget, relevance_query="explore")
    b = armed.compact(budget, relevance_query="explore")
    assert a.strategy is CompactionStrategy.EXACT_PCKP
    assert a.model_dump() == b.model_dump()
    assert predictor.prediction_count == 0


def test_within_budget_returns_before_the_predictor_runs():
    predictor = fixed_predictor(ids=episode_ids(7))
    store, _, _ = small_store(predictor)
    result = store.compact(store.estimate_tokens() * 2, policy=LEARNED)
    assert result.status is CompactionStatus.WITHIN_BUDGET and predictor.prediction_count == 0


def test_applied_dossier_is_auditable_and_never_stores_the_query_text():
    store, _, _ = small_store(fixed_predictor(ids=episode_ids(7)))
    budget = store.estimate_tokens() // 2
    result = store.compact(
        budget, relevance_query="customer-ssn-123-45-6789 rotate keys", policy=LEARNED
    )
    audit = result.dossier["learned_ranking"]
    assert audit["status"] == "applied"
    assert audit["predictor"]["predictor_id"] == "fixed-table"
    assert audit["predictor"]["version"] == "1"
    assert audit["artifact_digest"] == store.utility_predictor.identity.artifact_digest
    assert audit["feature_schema_version"] == "fixed-utility-table-v1"
    assert audit["task_digest"] == result.dossier["relevance_query_hash"]
    assert set(audit["predicted_utilities"]) == set(audit["quantized_utilities"])
    assert audit["selected_episode_ids"] == result.dossier["retained_episode_ids"]
    assert audit["solver_status"] == "optimal" and audit["optimality_gap"] == "0"
    assert audit["token_budget"] == budget
    assert audit["selected_token_cost"] == result.dossier["solver"]["token_cost"]
    assert "customer-ssn" not in json.dumps(result.model_dump(mode="json"))


def test_a_trained_model_predictor_records_its_artifact_digest_and_feature_schema():
    model = LinearRankingModel([0.01] * len(FEATURE_NAMES))
    predictor = LearnedUtilityPredictor(
        model, predictor_id="ranker", version="3", artifact_digest="d" * 64
    )
    store, _, _ = small_store(predictor)
    result = store.compact(store.estimate_tokens() // 2, relevance_query="explore", policy=LEARNED)
    audit = result.dossier["learned_ranking"]
    assert audit["status"] == "applied"
    assert audit["artifact_digest"] == "d" * 64
    assert audit["feature_schema_version"] == "episode-utility-features-v1"


def test_quantization_clips_and_records_out_of_range_values():
    quantized, below, above = quantize_utilities(
        {"a": -1.0, "b": 0.0, "c": 0.0000004, "d": 5e6},
        QuantizationPolicy(scale=1000, ceiling=10.0),
    )
    assert quantized == {"a": 0, "b": 0, "c": 0, "d": 10_000}
    assert below == ["a"] and above == ["d"]


def test_a_positive_floor_lifts_every_episode_to_at_least_the_floor():
    quantized, below, above = quantize_utilities(
        {"a": -3.0, "b": 0.0, "c": 0.0000004, "d": 0.5},
        QuantizationPolicy(scale=1000, ceiling=10.0, floor=1),
    )
    assert quantized == {"a": 1, "b": 1, "c": 1, "d": 500}
    assert below == ["a"] and above == []
    assert QuantizationPolicy().floor == 0
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        QuantizationPolicy(floor=-1)


def test_a_floor_makes_a_zero_scoring_model_fill_the_budget_instead_of_keeping_nothing():
    table = {episode_id: 0.0 for episode_id in episode_ids(7)}
    budget = None
    retained = {}
    for floor in (0, 1):
        store = InMemoryEpisodeStore(
            utility_predictor=FixedUtilityPredictor(table, predictor_id="zero", version="1"),
            utility_quantization=QuantizationPolicy(floor=floor),
        )
        for index in range(4):
            record = store.open_exploratory("a", content={"result": {"text": "e" * (90 + index)}})
            store.close(record.id, description=f"explore {index}")
        for dependencies in (["episode-1", "episode-2"], ["episode-3"], []):
            record = store.open_action("a", dependencies, content={"result": {"text": "a" * 70}})
            store.close(record.id)
        budget = budget or store.estimate_tokens() // 2
        result = store.compact(budget, policy=LEARNED)
        assert result.dossier["learned_ranking"]["quantization"]["floor"] == floor
        retained[floor] = live_ids(store)
        assert store.estimate_tokens() <= budget
    assert retained[0] == set()
    assert len(retained[1]) >= 3 and store.estimate_tokens() > budget // 2


def test_a_floor_retains_the_largest_dependency_closed_set_that_fits():
    store = InMemoryEpisodeStore(
        utility_predictor=FixedUtilityPredictor(
            {episode_id: 0.0 for episode_id in episode_ids(7)}, predictor_id="zero", version="1"
        ),
        utility_quantization=QuantizationPolicy(floor=1),
    )
    for index in range(4):
        record = store.open_exploratory("a", content={"result": {"text": "e" * (90 + 10 * index)}})
        store.close(record.id, description=f"explore {index}")
    for dependencies in (["episode-1", "episode-2"], ["episode-3"], []):
        record = store.open_action("a", dependencies, content={"result": {"text": "a" * 70}})
        store.close(record.id)
    records = snapshot(store)
    budget = store.estimate_tokens() // 2
    store.compact(budget, policy=LEARNED)
    ids = sorted(records)
    best = 0
    for mask in range(1 << len(ids)):
        chosen = {ids[k] for k in range(len(ids)) if mask >> k & 1}
        if any(p not in chosen for i in chosen for p in records[i].depends_on):
            continue
        if sum(_episode_tokens(records[i]) for i in chosen) <= budget:
            best = max(best, len(chosen))
    assert len(live_ids(store)) == best


def test_negative_predictions_are_clipped_reported_and_retain_only_the_mandatory_closure():
    store, _, actions = small_store(fixed_predictor(ids=episode_ids(7), default=-2.0))
    records = snapshot(store)
    result = store.compact(
        store.estimate_tokens() // 3, policy=LEARNED, active_episode_id=actions[0]
    )
    audit = result.dossier["learned_ranking"]
    assert set(audit["clipped_below_zero"]) == set(audit["quantized_utilities"])
    assert set(audit["quantized_utilities"].values()) == {0}
    assert live_ids(store) == closure(records, {actions[0]})


def test_utility_ordering_drives_retention():
    target = "episode-3"
    predictor = fixed_predictor({target: 100.0}, ids=episode_ids(7), default=0.001)
    store, explore, _ = small_store(predictor)
    store.compact(max(_episode_tokens(store.get(i)) for i in explore) * 2, policy=LEARNED)
    assert target in live_ids(store)


def test_infeasible_mandatory_set_is_reported_with_the_learned_strategy():
    store, explore, _ = small_store(fixed_predictor(ids=episode_ids(7)))
    result = store.compact(1, protected_episode_ids=set(explore), policy=LEARNED)
    assert result.status is CompactionStatus.PROTECTED_OVER_BUDGET
    assert result.dossier["solver"]["status"] == "infeasible-mandatory"
    assert result.strategy is CompactionStrategy.LEARNED_PCKP
    assert set(explore) <= live_ids(store)


def test_best_effort_status_is_preserved_when_the_branch_limit_stops_the_search():
    store = InMemoryEpisodeStore(utility_predictor=fixed_predictor(ids=episode_ids(15)))
    explore = []
    for index in range(8):
        record = store.open_exploratory("a", content={"result": {"text": "x" * (80 + 10 * index)}})
        store.close(record.id, description=f"e{index}")
        explore.append(record.id)
    for index in range(7):
        record = store.open_action("a", explore[index : index + 2], content={"result": "y" * 90})
        store.close(record.id)
    policy = PaskCompactionPolicy(
        strategy=CompactionStrategy.LEARNED_PCKP, exact_max_branch_nodes=1
    )
    result = store.compact(store.estimate_tokens() // 2, policy=policy)
    solver = result.dossier["solver"]
    assert solver["status"] == "best-effort"
    assert "not proven optimal" in solver["diagnostics"][0]
    assert result.dossier["learned_ranking"]["solver_status"] == "best-effort"
    retained = live_ids(store)
    assert all(p in retained for r in store.list() if r.id in retained for p in r.depends_on)


NAN_TABLE = {episode_id: float("nan") for episode_id in episode_ids(7)}
INF_TABLE = {episode_id: float("inf") for episode_id in episode_ids(7)}
PARTIAL_TABLE = {episode_id: 1.0 for episode_id in episode_ids(6)}

FAILURES = [
    (None, "UTILITY_PREDICTOR_MISSING", "has no utility_predictor"),
    (mismatched_model_predictor(), "UTILITY_PREDICTOR_FAILED", "raised ValueError"),
    (
        FixedUtilityPredictor(PARTIAL_TABLE, predictor_id="partial", version="1"),
        "UTILITY_PREDICTION_INCOMPLETE",
        "episode-7",
    ),
    (
        FixedUtilityPredictor(NAN_TABLE, predictor_id="nan", version="1"),
        "UTILITY_PREDICTION_NONFINITE",
        "nan",
    ),
    (
        FixedUtilityPredictor(INF_TABLE, predictor_id="inf", version="1"),
        "UTILITY_PREDICTION_NONFINITE",
        "inf",
    ),
]


@pytest.mark.parametrize(("predictor", "code", "message"), FAILURES)
def test_predictor_failures_fall_back_to_static_pckp_with_a_named_error(predictor, code, message):
    store, _, _ = small_store(predictor)
    reference, _, _ = small_store()
    budget = store.estimate_tokens() // 2
    result = store.compact(budget, policy=LEARNED)
    expected = reference.compact(budget, policy=STATIC)
    assert result.strategy is CompactionStrategy.EXACT_PCKP
    assert result.compacted_episode_ids == expected.compacted_episode_ids
    audit = result.dossier["learned_ranking"]
    assert audit["status"] == "fallback-static-pckp" and audit["error_code"] == code
    assert message in audit["error"]


def test_the_compaction_path_never_imports_the_trainer_and_never_mutates_the_model():
    for module in ("episode_store.py", "learned_retention.py"):
        tree = ast.parse((SOURCE / module).read_text("utf-8"))
        imported = {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for name in [node.module or "", *(alias.name for alias in node.names)]
        }
        assert "utility_training" not in imported and "train_ranking_model" not in imported
    model = LinearRankingModel([0.01] * len(FEATURE_NAMES))
    predictor = LearnedUtilityPredictor(
        model, predictor_id="p", version="1", artifact_digest="d" * 64
    )
    store, _, _ = small_store(predictor)
    before = model.parameters
    store.compact(store.estimate_tokens() // 2, relevance_query="explore", policy=LEARNED)
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


def test_the_fixed_table_predictor_validates_its_table_and_reports_a_stable_digest():
    first = FixedUtilityPredictor({"a": 1, "b": 2.5}, predictor_id="t", version="1")
    second = FixedUtilityPredictor({"b": 2.5, "a": 1.0}, predictor_id="t", version="1")
    assert first.identity.artifact_digest == second.identity.artifact_digest
    assert (
        FixedUtilityPredictor({"a": 2.0}, predictor_id="t", version="1").identity != first.identity
    )
    with pytest.raises(ValueError, match="episode \"a\" is 'high'"):
        FixedUtilityPredictor({"a": "high"}, predictor_id="t", version="1")
    with pytest.raises(ValueError, match="is True"):
        FixedUtilityPredictor({"a": True}, predictor_id="t", version="1")
