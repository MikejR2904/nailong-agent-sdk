import json

import pytest
from dfl_bench.evolve.__main__ import main as evolve_main
from dfl_bench.evolve.dataset import load_tasks, split_of
from dfl_bench.evolve.gate import (
    GatePolicy,
    evaluate_against_all,
    model_scoring,
    policy_static_scoring,
    static_scoring,
)
from dfl_bench.evolve.registry import ModelRegistry
from dfl_bench.evolve.retrain import retrain, write_candidate
from dfl_bench.instances import generate_instance, replay
from dfl_bench.oracle import SyntheticOracleExecutor
from dfl_bench.selection import is_usable

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_models import CompactionStrategy, PaskCompactionPolicy
from nailong_agent_sdk.memory.learned_retention import LearnedUtilityPredictor
from nailong_agent_sdk.memory.retention_outcomes import RetentionOutcomeLog
from nailong_agent_sdk.memory.utility_features_v2 import (
    FEATURE_NAMES_V2,
    FEATURE_SCHEMA_VERSION_V2,
)
from tests.dfl_bench.test_evolve import FLOOR, collect

POLICY = GatePolicy(min_tasks=6, min_pairs=10)


@pytest.fixture(scope="module")
def log_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("objectives") / "log.jsonl"
    collect(path, "shifted", 160, 21)
    return path


@pytest.fixture(scope="module")
def tasks(log_path):
    return load_tasks(RetentionOutcomeLog(log_path))[0]


@pytest.mark.parametrize(
    ("objective", "group_dro"),
    [
        ("regret-pairwise", False),
        ("listwise", False),
        ("regression", False),
        ("listwise", True),
    ],
)
def test_every_objective_retrains_records_itself_and_reloads_as_a_predictor(
    tasks, tmp_path, objective, group_dro
):
    candidate = retrain(tasks, objective=objective, group_dro=group_dro, seed=2)
    assert candidate.training["objective"] == objective
    assert candidate.training["group_dro"] is group_dro
    assert candidate.training["feature_schema_version"] == tasks[0].feature_schema
    artifact = tmp_path / "candidate.json"
    digest = write_candidate(candidate, artifact)
    predictor = LearnedUtilityPredictor.from_artifact(artifact, predictor_id="c", version="1")
    assert predictor.identity.artifact_digest == digest
    assert predictor.identity.feature_schema_version == tasks[0].feature_schema


def test_an_unknown_objective_and_an_empty_training_set_fail_by_name(tasks):
    with pytest.raises(ValueError, match="Unknown objective 'bogus'"):
        retrain(tasks, objective="bogus")
    single = [t for t in tasks if len(t.outcomes) < 2][:5]
    if single:
        with pytest.raises(ValueError, match="terms in the"):
            retrain(single, objective="listwise")


def test_tuned_static_scoring_reproduces_the_logged_static_utilities(tasks):
    default = policy_static_scoring(PaskCompactionPolicy())
    logged = static_scoring()
    checked = 0
    for task in tasks[:30]:
        expected = logged.scorer(task)
        if expected is None:
            continue
        rebuilt = default.scorer(task)
        assert rebuilt == pytest.approx(expected, abs=1e-9)
        checked += 1
    assert checked > 10


def test_a_candidate_must_beat_every_incumbent_and_the_reasons_name_the_one_it_lost_to(tasks):
    gate_tasks = [t for t in tasks if split_of(t.task_digest) == "gate"]
    candidate = retrain(tasks, objective="listwise", seed=1)
    scoring = model_scoring(candidate.model, "candidate")
    identical = model_scoring(candidate.model, "identical")
    decision = evaluate_against_all(
        scoring, [static_scoring(), identical], gate_tasks, policy=POLICY, quantization=FLOOR
    )
    assert not decision.promote
    assert any(reason.startswith("vs identical:") for reason in decision.reasons)
    assert set(decision.metrics["against"]) == {"static-pckp", "identical"}
    with pytest.raises(ValueError, match="At least one incumbent"):
        evaluate_against_all(scoring, [], gate_tasks)


def test_disabling_learned_retention_clears_the_serving_version_and_is_recorded(tasks, tmp_path):
    candidate = retrain(tasks, objective="regression", seed=3)
    artifact = tmp_path / "c.json"
    write_candidate(candidate, artifact)
    registry = ModelRegistry(tmp_path / "registry")
    from dfl_bench.evolve.gate import GateDecision

    version = registry.register(artifact, GateDecision(True, [], {}))
    registry.promote(version, approver="ops", reason="gate passed")
    assert registry.current_version() == version
    registry.disable(approver="ops", reason="incident")
    assert registry.current() is None
    assert registry.history()[-1]["event"] == "disable"
    assert registry.history()[-1]["from"] == version
    with pytest.raises(AgentSdkError, match="static default is already in effect"):
        registry.rollback(approver="ops", reason="nothing to undo")
    with pytest.raises(ValueError, match="non-empty approver and reason"):
        registry.disable(approver="", reason="x")
    registry.promote(version, approver="ops", reason="resume")
    assert registry.current_version() == version


def test_the_cli_trains_a_chosen_objective_against_a_tuned_static_baseline(
    log_path, tmp_path, capsys
):
    weights = tmp_path / "tuned.json"
    weights.write_text(json.dumps({"task_relevance_weight": 0.7, "recency_weight": 0.0}))
    registry = tmp_path / "registry"
    code = evolve_main(
        [
            "retrain",
            "--log",
            str(log_path),
            "--registry",
            str(registry),
            "--objective",
            "listwise",
            "--tuned-static",
            str(weights),
            "--min-tasks",
            "6",
            "--min-pairs",
            "10",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code in (0, 2)
    assert set(report["metrics"]["against"]) == {"static-pckp", "tuned-static-pckp"}
    history = ModelRegistry(registry).history()
    assert history[0]["training"]["objective"] == "listwise"
    assert (
        evolve_main(["disable", "--registry", str(registry), "--approver", "a", "--reason", "r"])
        == 0
    )
    assert ModelRegistry(registry).current_version() is None


def test_decisions_can_be_logged_with_v2_features_and_schemas_are_never_mixed(tmp_path):
    path = tmp_path / "v2.jsonl"
    log = RetentionOutcomeLog(path, task_group="g")
    executor = SyntheticOracleExecutor()
    made = 0
    index = 0
    while made < 3:
        instance = generate_instance("V2", "aligned", index, 5)
        index += 1
        if not is_usable(instance):
            continue
        made += 1
        built = replay(
            instance,
            retention_observer=log.observe,
            retention_feature_schema=FEATURE_SCHEMA_VERSION_V2,
        )
        built.store.compact(
            instance.budget,
            relevance_query=instance.query,
            policy=PaskCompactionPolicy(strategy=CompactionStrategy.EXACT_PCKP),
            protected_episode_ids=built.protected,
        )
        decision = log.decisions()[-1]
        log.record_outcome(
            decision.decision_id,
            executor.run(instance, decision.selected_ids, 0),
            source="synthetic",
        )
    decision = log.decisions()[0]
    assert decision.feature_schema_version == FEATURE_SCHEMA_VERSION_V2
    assert all(len(v) == len(FEATURE_NAMES_V2) for v in decision.features.values())
    tasks, _ = load_tasks(RetentionOutcomeLog(path))
    assert {t.feature_schema for t in tasks} == {FEATURE_SCHEMA_VERSION_V2}
    other = collect(tmp_path / "mixed.jsonl", "aligned", 2, 4, explore=False)
    for decision in other.decisions():
        log.observe(decision)
    for decision in other.decisions():
        log.record_outcome(decision.decision_id, 0.5, source="synthetic", trial=3)
    with pytest.raises(ValueError, match="mix feature schemas"):
        load_tasks(RetentionOutcomeLog(path))
    with pytest.raises(ValueError, match="Unknown feature schema"):
        replay(
            generate_instance("V2", "aligned", 99, 5),
            retention_observer=log.observe,
            retention_feature_schema="nope",
        ).store.compact(1, relevance_query="x")
