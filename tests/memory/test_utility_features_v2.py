import random

import pytest

from nailong_agent_sdk.memory.episode_models import CompactionStrategy, PaskCompactionPolicy
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import LearnedUtilityPredictor
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES, extract_features
from nailong_agent_sdk.memory.utility_features_v2 import (
    FEATURE_GROUPS_V2,
    FEATURE_NAMES_V2,
    FEATURE_SCHEMA_VERSION_V2,
    extract_features_v2,
)
from nailong_agent_sdk.memory.utility_models import LinearRankingModel
from tests.support.learned_memory import build_store

QUERY = "alpha ticket-4471 rollback"


def chain_store():
    store = InMemoryEpisodeStore()

    def explore(text, status, description):
        record = store.open_exploratory("a", content={"result": {"status": status, "text": text}})
        store.close(record.id, description=description)
        return record.id

    def act(deps, text, status):
        record = store.open_action("a", deps, content={"result": {"status": status, "text": text}})
        store.close(record.id)
        return record.id

    root = explore("ticket-4471 alpha failed", "failed", "root alpha")
    other = explore("rollback notes", "succeeded", "other")
    side = explore("unrelated noise", "succeeded", "side")
    shared = act([root, other], "rollback done", "succeeded")
    solo = act([root], "retry attempt", "failed")
    return store, root, other, side, shared, solo


def column(features, name):
    return {i: v[FEATURE_NAMES_V2.index(name)] for i, v in features.items()}


def test_v2_extends_v1_without_changing_its_columns():
    store, *_ = chain_store()
    context = store._retention_context(10_000, QUERY)
    v1, v2 = extract_features(context), extract_features_v2(context)
    assert FEATURE_NAMES_V2[: len(FEATURE_NAMES)] == FEATURE_NAMES
    assert all(v2[i][: len(FEATURE_NAMES)] == v1[i] for i in v1)
    assert all(len(v) == len(FEATURE_NAMES_V2) for v in v2.values())
    assert FEATURE_SCHEMA_VERSION_V2.endswith("v2")


def test_graph_features_capture_value_unlocked_exclusivity_and_co_requirements():
    store, root, other, side, shared, solo = chain_store()
    features = extract_features_v2(store._retention_context(10_000, QUERY))
    cost = column(features, "dependent_cost_share")
    assert cost[root] > cost[other] > cost[side] == 0.0
    exclusive = column(features, "exclusive_dependent_share")
    assert exclusive[root] == 0.5 and exclusive[other] == 0.0
    co = column(features, "co_prerequisite_share")
    assert co[root] == co[other] == 1.0 and co[side] == 0.0
    assert column(features, "dependent_failed_share")[root] == 0.5
    assert column(features, "dependent_failed_share")[other] == 0.0


def test_failure_propagation_features():
    store, root, other, side, shared, solo = chain_store()
    features = extract_features_v2(store._retention_context(10_000, QUERY))
    assert column(features, "failed_descendant")[root] == 1.0
    assert column(features, "failed_descendant")[other] == 0.0
    assert column(features, "failed_ancestor")[shared] == 1.0
    assert column(features, "failed_correction")[shared] == 1.0
    assert column(features, "failed_correction")[solo] == 0.0
    assert column(features, "failed_ancestor")[side] == 0.0


def test_task_conditioned_features_reward_requested_identifiers_and_unique_coverage():
    store, root, other, side, shared, solo = chain_store()
    features = extract_features_v2(store._retention_context(10_000, QUERY))
    assert (
        column(features, "identifier_overlap")[root] > column(features, "identifier_overlap")[side]
    )
    assert column(features, "unique_coverage")[root] > 0.0
    assert column(features, "unique_coverage")[side] == 0.0
    assert column(features, "query_coverage")[root] >= column(features, "query_coverage")[other]


def test_redundancy_is_high_for_duplicated_content_and_low_for_distinct_content():
    store = InMemoryEpisodeStore()
    ids = []
    for text in ("same words repeated here", "same words repeated here", "wholly different thing"):
        record = store.open_exploratory(
            "a", content={"result": {"status": "succeeded", "text": text}}
        )
        store.close(record.id, description=text)
        ids.append(record.id)
    redundancy = column(
        extract_features_v2(store._retention_context(10_000, "words")), "redundancy"
    )
    assert redundancy[ids[0]] > redundancy[ids[2]] and redundancy[ids[0]] == redundancy[ids[1]]


def test_masking_zeroes_exactly_the_named_v2_group_and_unknown_groups_are_rejected():
    store, *_ = chain_store()
    context = store._retention_context(10_000, QUERY)
    full = extract_features_v2(context)
    masked = extract_features_v2(context, ["graph_v2", "task"])
    for episode_id, vector in masked.items():
        for name in FEATURE_GROUPS_V2["graph_v2"] + FEATURE_GROUPS_V2["task"]:
            assert vector[FEATURE_NAMES_V2.index(name)] == 0.0
        assert (
            vector[FEATURE_NAMES_V2.index("failed_ancestor")]
            == full[episode_id][FEATURE_NAMES_V2.index("failed_ancestor")]
        )
    with pytest.raises(ValueError, match="Unknown feature groups"):
        extract_features_v2(context, ["nope"])


def test_extraction_is_deterministic():
    store, *_ = chain_store()
    context = store._retention_context(10_000, QUERY)
    assert extract_features_v2(context) == extract_features_v2(context)


def test_a_v2_predictor_drives_a_compaction_and_records_its_schema():
    rng = random.Random(1)
    model = LinearRankingModel([0.1] * len(FEATURE_NAMES_V2))
    predictor = LearnedUtilityPredictor(
        model,
        predictor_id="v2",
        version="1",
        artifact_digest="d" * 64,
        feature_schema=FEATURE_SCHEMA_VERSION_V2,
    )
    store, _, _ = build_store(rng, 5, 3, utility_predictor=predictor)
    result = store.compact(
        store.estimate_tokens() // 2,
        relevance_query="alpha",
        policy=PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP),
    )
    ranking = result.dossier["learned_ranking"]
    assert ranking["status"] == "applied"
    assert ranking["feature_schema_version"] == FEATURE_SCHEMA_VERSION_V2


def test_a_v1_model_on_the_v2_schema_fails_by_name_and_unknown_schemas_are_rejected():
    with pytest.raises(ValueError, match='Unknown feature schema "v9"'):
        LearnedUtilityPredictor(
            LinearRankingModel([0.0]),
            predictor_id="x",
            version="1",
            artifact_digest="a" * 64,
            feature_schema="v9",
        )
    store, *_ = chain_store()
    narrow = LearnedUtilityPredictor(
        LinearRankingModel([0.1] * len(FEATURE_NAMES)),
        predictor_id="narrow",
        version="1",
        artifact_digest="a" * 64,
        feature_schema=FEATURE_SCHEMA_VERSION_V2,
    )
    with pytest.raises(ValueError):
        narrow.predict(store._retention_context(10_000, QUERY))
