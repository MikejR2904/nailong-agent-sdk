import json
import random
import threading

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import sha256_hex
from nailong_agent_sdk.memory.episode_models import CompactionStrategy, PaskCompactionPolicy
from nailong_agent_sdk.memory.retention_outcomes import RetentionOutcomeLog
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES, extract_features
from tests.support.learned_memory import build_store, episode_ids, fixed_predictor

LEARNED = PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP)
QUERY = "explore alpha customer-ssn-123-45-6789"


def logged_store(tmp_path, predictor=None, group="ops"):
    log = RetentionOutcomeLog(tmp_path / "log.jsonl", task_group=group)
    store, _, _ = build_store(
        random.Random(7), 4, 3, utility_predictor=predictor, retention_observer=log.observe
    )
    return store, log


def test_a_static_compaction_is_recorded_with_its_features_selection_and_utilities(tmp_path):
    store, log = logged_store(tmp_path)
    context = store._retention_context(store.estimate_tokens() // 2, QUERY)
    expected_features = extract_features(context)
    result = store.compact(store.estimate_tokens() // 2, relevance_query=QUERY)
    (decision,) = log.decisions()
    assert decision.strategy == CompactionStrategy.EXACT_PCKP.value and decision.predictor is None
    assert decision.task_digest == sha256_hex(QUERY) and decision.task_group == "ops"
    assert decision.selected_ids == sorted(result.dossier["retained_episode_ids"])
    assert set(decision.features) == set(decision.episode_ids) == set(expected_features)
    assert all(
        decision.features[i] == list(expected_features[i])
        and len(decision.features[i]) == len(FEATURE_NAMES)
        for i in decision.episode_ids
    )
    assert set(decision.utilities) == set(decision.episode_ids)
    assert decision.solver_status == "optimal" and decision.optimality_gap == "0"
    assert "customer-ssn" not in json.dumps(decision.model_dump(mode="json"))


def test_a_learned_decision_records_the_predictor_identity_and_the_utilities_it_used(tmp_path):
    predictor = fixed_predictor({"episode-3": 9.0}, ids=episode_ids(7), default=0.1)
    store, log = logged_store(tmp_path, predictor)
    store.compact(store.estimate_tokens() // 2, relevance_query=QUERY, policy=LEARNED)
    (decision,) = log.decisions()
    assert decision.strategy == CompactionStrategy.LEARNED_PCKP.value
    assert decision.predictor == predictor.identity
    assert decision.utilities["episode-3"] == 9.0 and "episode-3" in decision.selected_ids


def test_no_decision_is_recorded_when_nothing_was_selected(tmp_path):
    store, log = logged_store(tmp_path)
    store.compact(store.estimate_tokens() * 2, relevance_query=QUERY)
    store.compact(1, protected_episode_ids=["episode-1", "episode-2"], relevance_query=QUERY)
    assert log.decisions() == []


def test_identical_decisions_share_an_id_and_different_selections_do_not(tmp_path):
    first, log = logged_store(tmp_path)
    first.compact(first.estimate_tokens() // 2, relevance_query=QUERY)
    second, _ = logged_store(tmp_path)
    second.compact(second.estimate_tokens() // 2, relevance_query=QUERY)
    assert len(log.decisions()) == 1
    third, _ = logged_store(tmp_path)
    third.compact(third.estimate_tokens() // 3, relevance_query=QUERY)
    assert len(RetentionOutcomeLog(tmp_path / "log.jsonl").decisions()) == 2


def test_outcomes_attach_to_a_decision_survive_a_reload_and_reject_conflicts(tmp_path):
    store, log = logged_store(tmp_path)
    store.compact(store.estimate_tokens() // 2, relevance_query=QUERY)
    decision_id = log.decisions()[0].decision_id
    log.record_outcome(decision_id, 0.75, source="measured", trial=0)
    log.record_outcome(decision_id, 0.5, source="measured", trial=1)
    log.record_outcome(decision_id, 0.75, source="measured", trial=0)
    reloaded = RetentionOutcomeLog(tmp_path / "log.jsonl")
    assert [(o.trial, o.score, o.source) for o in reloaded.outcomes(decision_id)] == [
        (0, 0.75, "measured"),
        (1, 0.5, "measured"),
    ]
    with pytest.raises(AgentSdkError, match="already has score 0.75, not 0.25") as raised:
        log.record_outcome(decision_id, 0.25, source="measured", trial=0)
    assert raised.value.code == "RETENTION_LOG_CORRUPT"


def test_unknown_decisions_and_out_of_range_scores_are_rejected_by_name(tmp_path):
    store, log = logged_store(tmp_path)
    store.compact(store.estimate_tokens() // 2, relevance_query=QUERY)
    with pytest.raises(AgentSdkError, match='unknown retention decision "nope"') as raised:
        log.record_outcome("nope", 0.5, source="measured")
    assert raised.value.code == "RETENTION_DECISION_UNKNOWN"
    with pytest.raises(ValueError, match="less than or equal to 1"):
        log.record_outcome(log.decisions()[0].decision_id, 1.5, source="measured")


def test_a_corrupt_log_names_the_file_and_line(tmp_path):
    store, log = logged_store(tmp_path)
    store.compact(store.estimate_tokens() // 2, relevance_query=QUERY)
    path = tmp_path / "log.jsonl"
    path.write_text(path.read_text() + "not json\n")
    with pytest.raises(AgentSdkError, match=r"line 2 is invalid") as raised:
        RetentionOutcomeLog(path)
    assert raised.value.code == "RETENTION_LOG_CORRUPT" and raised.value.details["line"] == 2
    path.write_text(
        '{"type": "outcome", "outcome": {"decision_id": "x", "score": 0.1, "source": "s"}}\n'
    )
    with pytest.raises(AgentSdkError, match='unknown decision "x"'):
        RetentionOutcomeLog(path)


def test_concurrent_writers_leave_a_consistent_log(tmp_path):
    store, log = logged_store(tmp_path)
    store.compact(store.estimate_tokens() // 2, relevance_query=QUERY)
    decision_id = log.decisions()[0].decision_id
    threads = [
        threading.Thread(
            target=log.record_outcome,
            args=(
                decision_id,
                0.1 * n % 1,
            ),
            kwargs={"source": "measured", "trial": n},
        )
        for n in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(RetentionOutcomeLog(tmp_path / "log.jsonl").outcomes(decision_id)) == 20


def test_a_failing_log_surfaces_as_a_named_error_after_the_compaction_was_applied(tmp_path):
    store, log = logged_store(tmp_path)
    path = tmp_path / "log.jsonl"
    path.unlink()
    path.mkdir()
    before = store.estimate_tokens()
    with pytest.raises(AgentSdkError, match="retention observer raised") as raised:
        store.compact(before // 2, relevance_query=QUERY)
    assert raised.value.code == "RETENTION_OBSERVER_FAILED"
    assert "compaction itself was applied" in raised.value.message
    assert store.estimate_tokens() < before
