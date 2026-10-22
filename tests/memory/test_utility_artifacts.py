import json
import random

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.learned_retention import LearnedUtilityPredictor
from nailong_agent_sdk.memory.utility_features import (
    FEATURE_GROUPS,
    FEATURE_NAMES,
    RetentionContext,
    extract_features,
    masked_feature_names,
)
from nailong_agent_sdk.memory.utility_models import (
    LinearRankingModel,
    MlpRankingModel,
    load_utility_model,
    model_from_artifact,
    model_to_artifact,
    save_utility_model,
)
from tests.support.learned_memory import build_store

N = len(FEATURE_NAMES)


def context_for(store, query="explore alpha", budget=2000):
    live = [r for r in store.list() if r.state.value != "compacted"]
    return RetentionContext(
        records=tuple(live),
        token_costs={r.id: _episode_tokens(r) for r in live},
        relevance={r.id: store.relevance_scorer.score(query, r) for r in live},
        query=query,
        token_budget=budget,
    )


def test_linear_model_round_trips_exactly(tmp_path):
    model = LinearRankingModel([0.1 * i - 0.5 for i in range(N)])
    digest = save_utility_model(tmp_path / "m.json", model, {"seed": 3})
    loaded = load_utility_model(tmp_path / "m.json")
    assert loaded.model.parameters == model.parameters
    assert loaded.digest == digest and loaded.training == {"seed": 3}


def test_mlp_round_trip_preserves_predictions(tmp_path):
    model = MlpRankingModel.initialize(N, 6, seed=4)
    save_utility_model(tmp_path / "m.json", model, {})
    loaded = load_utility_model(tmp_path / "m.json").model
    probe = tuple(0.1 * i for i in range(N))
    assert loaded.score(probe) == model.score(probe)
    assert loaded.architecture() == {"feature_dim": N, "hidden_units": 6}


def test_digest_is_a_function_of_content_only():
    model = LinearRankingModel([0.5] * N)
    assert (
        model_to_artifact(model, {"a": 1})["digest"] == model_to_artifact(model, {"a": 1})["digest"]
    )
    assert (
        model_to_artifact(model, {"a": 1})["digest"] != model_to_artifact(model, {"a": 2})["digest"]
    )


def test_unreadable_artifacts_raise_distinct_coded_errors(tmp_path):
    with pytest.raises(AgentSdkError) as raised:
        load_utility_model(tmp_path / "absent.json")
    assert raised.value.code == "UTILITY_MODEL_ARTIFACT_MISSING"
    assert "absent.json" in raised.value.message
    path = tmp_path / "m.json"
    save_utility_model(path, LinearRankingModel([0.0] * N), {})
    path.write_text(path.read_text()[:40])
    with pytest.raises(AgentSdkError) as raised:
        load_utility_model(path)
    assert raised.value.code == "UTILITY_MODEL_ARTIFACT_CORRUPT"
    assert "not valid JSON" in raised.value.message
    path.write_text("[1, 2]")
    with pytest.raises(AgentSdkError, match="JSON list"):
        load_utility_model(path)


def test_tampered_parameters_fail_the_digest_check(tmp_path):
    path = tmp_path / "m.json"
    save_utility_model(path, LinearRankingModel([0.0] * N), {})
    artifact = json.loads(path.read_text())
    artifact["parameters"][0] = 9.0
    path.write_text(json.dumps(artifact))
    with pytest.raises(AgentSdkError, match="does not match its content digest"):
        load_utility_model(path)


def test_feature_schema_mismatch_is_rejected_with_both_versions():
    artifact = model_to_artifact(LinearRankingModel([0.0] * N), {})
    body = {k: v for k, v in artifact.items() if k != "digest"}
    body["feature_schema_version"] = "episode-utility-features-v0"
    body["feature_names"] = body["feature_names"][:-1]
    from nailong_agent_sdk.foundations.hashing import sha256_hex, strict_canonical_json

    signed = {**body, "digest": sha256_hex(strict_canonical_json(body))}
    with pytest.raises(AgentSdkError) as raised:
        model_from_artifact(signed, source="test")
    assert raised.value.code == "UTILITY_MODEL_FEATURE_SCHEMA_MISMATCH"
    assert "episode-utility-features-v0" in raised.value.message
    assert f"{N - 1} features" in raised.value.message


def test_wrong_input_width_is_a_schema_mismatch():
    artifact = model_to_artifact(LinearRankingModel([0.0] * (N - 1)), {})
    with pytest.raises(AgentSdkError) as raised:
        model_from_artifact(artifact)
    assert raised.value.code == "UTILITY_MODEL_FEATURE_SCHEMA_MISMATCH"


def test_non_finite_parameters_are_refused_on_save_and_names_the_index(tmp_path):
    weights = [0.0] * N
    weights[3] = float("nan")
    with pytest.raises(AgentSdkError) as raised:
        save_utility_model(tmp_path / "m.json", LinearRankingModel(weights), {})
    assert raised.value.code == "UTILITY_MODEL_NONFINITE" and "index 3" in raised.value.message
    assert not (tmp_path / "m.json").exists()


def test_features_are_finite_schema_ordered_and_deterministic():
    store, _, _ = build_store(random.Random(1), 4, 4)
    context = context_for(store)
    first = extract_features(context)
    assert first == extract_features(context)
    assert set(first) == {r.id for r in context.records}
    for vector in first.values():
        assert len(vector) == N and all(abs(x) < 1e9 for x in vector)
        assert vector[FEATURE_NAMES.index("bias")] == 1.0


def test_failed_results_and_exploratory_kind_are_visible_features():
    store, explore, actions = build_store(random.Random(2), 2, 2)
    features = extract_features(context_for(store))
    failed = FEATURE_NAMES.index("failed_result")
    kind = FEATURE_NAMES.index("exploratory")
    assert {features[a][failed] for a in actions} == {1.0}
    assert {features[e][failed] for e in explore} == {0.0}
    assert features[explore[0]][kind] == 1.0 and features[actions[0]][kind] == 0.0


def test_features_of_an_episode_do_not_change_when_unrelated_future_episodes_arrive():
    rng = random.Random(3)
    store, explore, _ = build_store(rng, 3, 0)
    before = extract_features(context_for(store))
    later = store.open_exploratory(
        "a", content={"result": {"status": "succeeded", "text": "z" * 50}}
    )
    store.close(later.id, description="later")
    after = extract_features(context_for(store))
    name = FEATURE_NAMES.index("lexical_relevance")
    assert [after[e][name] for e in explore] == [before[e][name] for e in explore]
    assert set(after) - set(before) == {later.id}


def test_disabled_groups_zero_exactly_their_columns():
    store, _, _ = build_store(random.Random(4), 3, 3)
    context = context_for(store)
    full = extract_features(context)
    ablated = extract_features(context, ["task", "graph"])
    masked = masked_feature_names(["task", "graph"])
    for episode_id, vector in full.items():
        for name, value, new in zip(FEATURE_NAMES, vector, ablated[episode_id]):
            assert new == (0.0 if name in masked else value)
    assert masked == set(FEATURE_GROUPS["task"]) | set(FEATURE_GROUPS["graph"])
    with pytest.raises(ValueError, match="Unknown feature groups"):
        masked_feature_names(["nonsense"])


def test_predictor_loads_from_artifact_and_reports_identity(tmp_path):
    store, _, _ = build_store(random.Random(5), 3, 2)
    model = LinearRankingModel([0.0] * N)
    digest = save_utility_model(
        tmp_path / "m.json", model, {"disabled_feature_groups": ["dynamics"]}
    )
    predictor = LearnedUtilityPredictor.from_artifact(
        tmp_path / "m.json", predictor_id="p", version="2"
    )
    assert predictor.identity.artifact_digest == digest
    assert predictor.identity.feature_schema_version == "episode-utility-features-v1"
    scores = predictor.predict(context_for(store))
    assert set(scores.values()) == {0.0}
