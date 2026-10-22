import json
import random

import pytest
from dfl_bench.evolve.__main__ import main as evolve_main
from dfl_bench.evolve.dataset import load_tasks, split_of
from dfl_bench.evolve.gate import (
    GatePolicy,
    evaluate_gate,
    model_scoring,
    solve_selection,
    static_scoring,
)
from dfl_bench.evolve.registry import ModelRegistry
from dfl_bench.evolve.retrain import retrain, write_candidate
from dfl_bench.instances import generate_instance, replay
from dfl_bench.oracle import SyntheticOracleExecutor
from dfl_bench.selection import is_usable, perturbed_values, static_utilities

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_models import CompactionStrategy, PaskCompactionPolicy
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import (
    FixedUtilityPredictor,
    LearnedUtilityPredictor,
    QuantizationPolicy,
)
from nailong_agent_sdk.memory.retention_outcomes import RetentionOutcomeLog
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import LinearRankingModel, load_utility_model

LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)
STATIC = PaskCompactionPolicy(strategy=CompactionStrategy.EXACT_PCKP)
POLICY = GatePolicy(min_tasks=6, min_pairs=10)


def collect(path, regime, count, seed, *, explore=True, source="synthetic"):
    log = RetentionOutcomeLog(path, task_group=regime)
    executor = SyntheticOracleExecutor()
    index, made = 0, 0
    rng = random.Random(seed)
    while made < count:
        instance = generate_instance(f"E{seed}x{index % 12}", regime, index, seed)
        index += 1
        if not is_usable(instance):
            continue
        made += 1
        base = static_utilities(instance)
        arms = [(STATIC, None)]
        if explore:
            for sigma in (0.4, 0.8, 1.2, 1.6, 2.0, 2.5, 3.0, 4.0):
                values = perturbed_values(base, rng, sigma)
                arms.append(
                    (LEARNED, FixedUtilityPredictor(values, predictor_id="explore", version="1"))
                )
        for policy, predictor in arms:
            seen = []
            built = replay(
                instance,
                utility_predictor=predictor,
                retention_observer=lambda decision, seen=seen: (
                    log.observe(decision),
                    seen.append(decision),
                ),
            )
            built.store.compact(
                instance.budget,
                relevance_query=instance.query,
                policy=policy,
                protected_episode_ids=built.protected,
            )
            for decision in seen:
                for trial in range(3):
                    score = executor.run(instance, decision.selected_ids, trial)
                    log.record_outcome(decision.decision_id, score, source=source, trial=trial)
    return log


FLOOR = QuantizationPolicy(floor=1)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("world") / "log.jsonl"
    collect(path, "shifted", 200, 11)
    return path


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    path = tmp_path_factory.mktemp("mixed") / "log.jsonl"
    collect(path, "shifted", 120, 11)
    collect(path, "aligned", 120, 12)
    return path


def test_tasks_are_grouped_with_their_candidate_outcomes_and_one_evidence_kind(world):
    tasks, kind = load_tasks(RetentionOutcomeLog(world))
    assert kind == "synthetic" and len(tasks) == 200
    assert {t.task_group for t in tasks} == {"shifted"}
    sample = tasks[0]
    assert len(sample.outcomes) >= 2 and all(len(o.outcomes) % 3 == 0 for o in sample.outcomes)
    assert set(sample.features) == set(sample.episode_ids)
    assert {split_of(t.task_digest) for t in tasks} == {"train", "validation", "gate"}
    assert split_of(sample.task_digest) == split_of(sample.task_digest)


def test_mixed_or_unknown_evidence_kinds_are_refused(tmp_path):
    path = tmp_path / "log.jsonl"
    log = collect(path, "aligned", 2, 3)
    decision = log.decisions()[0]
    log.record_outcome(decision.decision_id, 0.5, source="measured", trial=9)
    with pytest.raises(ValueError, match="Refusing to mix evidence kinds"):
        load_tasks(RetentionOutcomeLog(path))
    other = tmp_path / "other.jsonl"
    collect(other, "aligned", 2, 3, source="guess")
    with pytest.raises(ValueError, match="are not evidence kinds"):
        load_tasks(RetentionOutcomeLog(other))


def test_retraining_without_contrasting_decisions_explains_what_is_missing(tmp_path):
    collect(tmp_path / "log.jsonl", "shifted", 12, 4, explore=False)
    tasks, _ = load_tasks(RetentionOutcomeLog(tmp_path / "log.jsonl"))
    with pytest.raises(ValueError, match="needs decisions that differ within a task"):
        retrain(tasks)


def test_retraining_is_deterministic_and_writes_a_digest_checked_artifact(world, tmp_path):
    tasks, _ = load_tasks(RetentionOutcomeLog(world))
    first, second = retrain(tasks, seed=3), retrain(tasks, seed=3)
    assert first.model.parameters == second.model.parameters
    assert first.training["train_pairs"] > 0 and first.training["evidence_kind"] == "synthetic"
    digest = write_candidate(first, tmp_path / "candidate.json")
    loaded = load_utility_model(tmp_path / "candidate.json")
    assert loaded.digest == digest and loaded.model.parameters == first.model.parameters
    assert loaded.training["dataset_digest"] == first.training["dataset_digest"]


def gate_tasks(world):
    tasks, _ = load_tasks(RetentionOutcomeLog(world))
    return tasks, [t for t in tasks if split_of(t.task_digest) == "gate"]


def test_a_trained_candidate_passes_the_gate_against_static_on_heldout_tasks(world):
    tasks, held_out = gate_tasks(world)
    candidate = retrain(tasks, seed=1)
    decision = evaluate_gate(
        model_scoring(candidate.model, "candidate"),
        static_scoring(),
        held_out,
        policy=POLICY,
        quantization=FLOOR,
    )
    assert decision.reasons == [] and decision.promote
    metrics = decision.metrics
    assert metrics["accuracy_gain"] > 0 and metrics["sign_test_p"] < 0.05
    assert metrics["non_optimal_solves"] == 0 and metrics["budget_use_ratio"] >= 0.9
    assert set(metrics["groups"]) == {"shifted"} and metrics["quantization_floor"] == 1


def test_the_gate_rejects_a_candidate_with_too_little_held_out_data(world):
    tasks, held_out = gate_tasks(world)
    candidate = retrain(tasks, seed=1)
    decision = evaluate_gate(
        model_scoring(candidate.model, "candidate"),
        static_scoring(),
        held_out[:2],
        policy=POLICY,
        quantization=FLOOR,
    )
    assert not decision.promote and "insufficient held-out data" in decision.reasons[0]


def test_the_gate_rejects_a_candidate_that_is_no_better_than_the_incumbent(world):
    _, held_out = gate_tasks(world)
    decision = evaluate_gate(
        static_scoring(), static_scoring(), held_out, policy=POLICY, quantization=FLOOR
    )
    assert not decision.promote
    assert any("no significant ranking gain" in reason for reason in decision.reasons)


def test_the_gate_rejects_a_model_that_ranks_worse_than_the_incumbent(world):
    tasks, held_out = gate_tasks(world)
    trained = retrain(tasks, seed=1).model
    flipped = LinearRankingModel([-w for w in trained.parameters])
    worse = evaluate_gate(
        model_scoring(flipped, "flipped"),
        static_scoring(),
        held_out,
        policy=POLICY,
        quantization=FLOOR,
    )
    assert not worse.promote and any("no significant ranking gain" in r for r in worse.reasons)
    assert worse.metrics["accuracy_gain"] < 0


def test_the_gate_names_a_task_group_the_candidate_really_regresses_on(mixed):
    tasks, held_out = gate_tasks(mixed)
    candidate = retrain(tasks, seed=1)
    decision = evaluate_gate(
        model_scoring(candidate.model, "candidate"),
        static_scoring(),
        held_out,
        policy=POLICY,
        quantization=FLOOR,
    )
    assert not decision.promote
    assert any('regression in task group "aligned"' in r for r in decision.reasons)
    assert decision.metrics["groups"]["aligned"]["candidate"] < (
        decision.metrics["groups"]["aligned"]["incumbent"] - POLICY.group_tolerance
    )


def test_a_utility_floor_is_what_stops_a_zero_scoring_model_from_under_spending(world):
    tasks, held_out = gate_tasks(world)
    silent = LinearRankingModel([0.0] * (len(FEATURE_NAMES) - 1) + [-1.0])
    without = evaluate_gate(
        model_scoring(silent, "silent"), static_scoring(), held_out, policy=POLICY
    )
    assert any("under-uses the token budget" in r for r in without.reasons)
    assert without.metrics["quantization_floor"] == 0 and without.metrics["budget_use_ratio"] < 0.9
    floored = evaluate_gate(
        model_scoring(silent, "silent"),
        static_scoring(),
        held_out,
        policy=POLICY,
        quantization=FLOOR,
    )
    assert floored.metrics["quantization_floor"] == 1 and floored.metrics["budget_use_ratio"] >= 0.9
    assert not any("under-uses" in r for r in floored.reasons)


def test_selection_solves_use_the_logged_graph_budget_and_mandatory_set(world):
    tasks, _ = gate_tasks(world)
    task = tasks[0]
    solution = solve_selection(task, static_scoring().scorer(task))
    chosen = set(solution.selected_item_ids)
    assert solution.status.value == "optimal" and task.mandatory_ids <= chosen
    assert sum(task.token_costs[i] for i in chosen) <= task.token_budget
    assert all(set(task.depends_on[i]) <= chosen for i in chosen)


def test_the_registry_promotes_only_gated_versions_and_rolls_back_to_the_previous(world, tmp_path):
    tasks, held_out = gate_tasks(world)
    registry = ModelRegistry(tmp_path / "registry")
    passing = retrain(tasks, seed=1)
    failing = LinearRankingModel([-w for w in passing.model.parameters])
    for name, model in (("good", passing.model), ("bad", failing)):
        from dfl_bench.evolve.retrain import Candidate

        write_candidate(Candidate(model, {"name": name}), tmp_path / f"{name}.json")
    good = registry.register(
        tmp_path / "good.json",
        evaluate_gate(
            model_scoring(passing.model, "c"),
            static_scoring(),
            held_out,
            policy=POLICY,
            quantization=FLOOR,
        ),
    )
    bad = registry.register(
        tmp_path / "bad.json",
        evaluate_gate(
            model_scoring(failing, "c"),
            static_scoring(),
            held_out,
            policy=POLICY,
            quantization=FLOOR,
        ),
    )
    assert registry.current() is None
    with pytest.raises(AgentSdkError, match="did not pass the promotion gate") as raised:
        registry.promote(bad, approver="ops", reason="try")
    assert raised.value.code == "REGISTRY_GATE_NOT_PASSED"
    with pytest.raises(ValueError, match="non-empty approver and reason"):
        registry.promote(good, approver=" ", reason="x")
    with pytest.raises(AgentSdkError, match="not registered") as unknown:
        registry.promote("0" * 64, approver="ops", reason="x")
    assert unknown.value.code == "REGISTRY_VERSION_UNKNOWN"
    registry.promote(good, approver="ops", reason="passed the offline gate")
    assert registry.current_version() == good and registry.current().digest == good
    assert registry.rollback(approver="ops", reason="canary regressed") is None
    assert registry.current() is None
    with pytest.raises(AgentSdkError, match="nothing|No version"):
        registry.rollback(approver="ops", reason="again")
    reopened = ModelRegistry(tmp_path / "registry")
    assert [e["event"] for e in reopened.history()] == [
        "register",
        "register",
        "promote",
        "rollback",
    ]
    assert [e["seq"] for e in reopened.history()] == [1, 2, 3, 4]


def test_a_tampered_registry_artifact_is_detected_when_it_is_loaded(world, tmp_path):
    tasks, held_out = gate_tasks(world)
    candidate = retrain(tasks, seed=1)
    registry = ModelRegistry(tmp_path / "registry")
    write_candidate(candidate, tmp_path / "c.json")
    version = registry.register(
        tmp_path / "c.json",
        evaluate_gate(
            model_scoring(candidate.model, "c"),
            static_scoring(),
            held_out,
            policy=POLICY,
            quantization=FLOOR,
        ),
    )
    registry.promote(version, approver="ops", reason="gated")
    stored = tmp_path / "registry" / "versions" / f"{version}.json"
    artifact = json.loads(stored.read_text())
    artifact["parameters"][0] += 1.0
    stored.write_text(json.dumps(artifact))
    with pytest.raises(AgentSdkError, match="does not match its content digest"):
        registry.current()


def test_the_loop_closes_a_promoted_model_drives_compaction_and_is_logged_by_digest(
    world, tmp_path
):
    tasks, held_out = gate_tasks(world)
    candidate = retrain(tasks, seed=1)
    registry = ModelRegistry(tmp_path / "registry")
    write_candidate(candidate, tmp_path / "c.json")
    version = registry.register(
        tmp_path / "c.json",
        evaluate_gate(
            model_scoring(candidate.model, "c"),
            static_scoring(),
            held_out,
            policy=POLICY,
            quantization=FLOOR,
        ),
    )
    registry.promote(version, approver="ops", reason="gated")
    current = registry.current()
    predictor = LearnedUtilityPredictor(
        current.model, predictor_id="registry", version=version[:8], artifact_digest=current.digest
    )
    log = RetentionOutcomeLog(tmp_path / "live.jsonl", task_group="aligned")
    instance = next(
        i for i in (generate_instance("L", "aligned", n, 5) for n in range(20)) if is_usable(i)
    )
    store = replay(instance, utility_predictor=predictor, retention_observer=log.observe).store
    store.compact(instance.budget, relevance_query=instance.query, policy=LEARNED)
    (decision,) = log.decisions()
    assert (
        decision.predictor.artifact_digest == version
        and decision.strategy == LEARNED.strategy.value
    )
    assert isinstance(store, InMemoryEpisodeStore)


def test_the_command_line_retrains_gates_registers_promotes_and_reports_status(
    world, tmp_path, capsys
):
    registry = tmp_path / "registry"
    code = evolve_main(
        [
            "retrain",
            "--log",
            str(world),
            "--registry",
            str(registry),
            "--min-tasks",
            "6",
            "--min-pairs",
            "10",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == (0 if report["promote"] else 2) and report["evidence_kind"] == "synthetic"
    assert report["promote"] is True
    evolve_main(
        [
            "promote",
            "--registry",
            str(registry),
            "--version",
            report["version"],
            "--approver",
            "ops",
            "--reason",
            "gated",
        ]
    )
    evolve_main(["status", "--registry", str(registry)])
    status = json.loads(capsys.readouterr().out)
    assert status["current"] == report["version"]
    evolve_main(["rollback", "--registry", str(registry), "--approver", "ops", "--reason", "test"])
    assert "static default" in capsys.readouterr().out
