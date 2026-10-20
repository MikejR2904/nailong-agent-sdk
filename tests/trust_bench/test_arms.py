import json

import pytest
from trust_bench.evaluate import FAMILIES
from trust_bench.recorder import load_recorded, verify_recorded
from trust_bench.schema import Kind
from trust_bench.systems.langgraph_arm import run_langgraph
from trust_bench.systems.nailong import run_sdk

from nailong_agent_sdk.developer_tools.diagnose import open_ledgers
from nailong_agent_sdk.observability.run_checkpoints import verify_terminal_link
from tests.support.trust_runs import evaluated, fault_ids

FAMILY_NAMES = tuple(FAMILIES)


def truth_events(run):
    return load_recorded(run.run_dir / "truth.jsonl")


def digests(events, kind):
    return {event.key: event.digest for event in events if event.kind is kind}


@pytest.mark.parametrize("family", FAMILY_NAMES)
@pytest.mark.parametrize("seed", (1, 3))
def test_both_arms_make_the_same_decisions_and_write_the_same_bytes(family, seed, tmp_path):
    case = FAMILIES[family](seed)
    sdk = run_sdk(case, None, tmp_path / "sdk")
    lg = run_langgraph(case, None, tmp_path / "lg")
    sdk_events, lg_events = truth_events(sdk), truth_events(lg)
    for kind in (Kind.MODEL_CALL, Kind.TOOL_CALL, Kind.WRITE):
        assert digests(sdk_events, kind) == digests(lg_events, kind), kind
    assert sdk.status == lg.status == "completed"


def test_the_independent_record_of_a_run_is_itself_a_valid_chain(tmp_path):
    run = run_sdk(FAMILIES["data"](1), None, tmp_path)
    assert verify_recorded(run.run_dir / "truth.jsonl")


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_the_sdk_run_leaves_a_linked_ledger_the_sdk_can_read_back(family, tmp_path):
    run = run_sdk(FAMILIES[family](1), None, tmp_path)
    for run_id in run.audit_run_ids:
        with open_ledgers(run.run_root, run_id) as ledgers:
            assert ledgers.audit.chain_break(run_id) is None
            assert ledgers.telemetry.chain_break(run_id) is None
            link = verify_terminal_link(ledgers.telemetry, ledgers.audit, run_id)
            assert link.state.value == "linked"


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_the_sdk_record_of_a_small_run_is_complete_and_recoverable(family):
    result = evaluated(family, 1, "sdk")
    counts = result["trace"]["counts"]
    for kind in ("model-call", "tool-call", "tool-result", "write"):
        assert counts[kind]["present"] == counts[kind]["total"] > 0
        assert counts[kind]["phantom"] == 0
        assert counts[kind]["recoverable"] == counts[kind]["present"]
    assert all(a["outcome"] == "correct" for a in result["trace"]["artifacts"])


def test_the_sdk_audit_bound_cuts_the_large_write_turn_but_the_journal_still_holds_it():
    result = evaluated("doc", 3, "sdk")
    counts = result["trace"]["counts"]
    for kind in ("model-call", "tool-call", "tool-result", "write"):
        assert counts[kind]["recoverable"] == counts[kind]["total"], kind
    assert any("recovered from the tool-result journal" in note for note in result["notes"])


def test_langgraph_keeps_every_payload_in_its_message_history():
    counts = evaluated("doc", 3, "langgraph")["trace"]["counts"]
    assert counts["model-call"]["recoverable"] == counts["model-call"]["total"]


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
def test_a_failed_tool_result_is_visible_to_the_first_error_baseline_on_both_arms(arm):
    result = evaluated("doc", 1, arm, "env_unreadable")
    assert result["diagnoses"]["first-error"]["top1"]
    assert result["diagnoses"]["ledger-scan+oracle"]["top1"]


def test_the_shipped_sdk_diagnosis_finds_a_failed_tool_call_and_a_wrong_write():
    assert evaluated("doc", 1, "sdk", "env_unreadable")["diagnoses"]["sdk-shipped:deliverable"][
        "top1"
    ]
    wrong = evaluated("data", 1, "sdk", "decision_wrong_stage:summary")
    assert wrong["diagnoses"]["sdk-shipped:deliverable"]["top1"]


def test_the_shipped_sdk_diagnosis_does_not_see_a_silently_corrupted_tool_result():
    for seed in (1, 2):
        result = evaluated("doc", seed, "sdk", "env_corrupt:digit_swap")
        assert not result["diagnoses"]["sdk-shipped:all-artifacts"]["top1"]
        assert result["diagnoses"]["ledger-scan+oracle"]["top1"]


def test_the_shipped_diagnosers_are_offered_only_to_the_arm_that_has_them():
    langgraph = evaluated("data", 1, "langgraph", "decision_wrong_stage:clean")["diagnoses"]
    sdk = evaluated("data", 1, "sdk", "decision_wrong_stage:clean")["diagnoses"]
    assert not any(name.startswith("sdk-shipped") for name in langgraph)
    assert {"sdk-shipped:deliverable", "sdk-shipped:all-artifacts"} <= set(sdk)


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
def test_a_limit_fault_fails_the_run_on_both_arms(arm):
    result = evaluated("doc", 1, arm, "limit")
    assert result["status"] == "failed" and result["symptom"] == "run-failed"


def test_langgraph_messages_are_exported_for_later_inspection(tmp_path):
    run = run_langgraph(FAMILIES["data"](1), None, tmp_path)
    messages = json.loads(run.records_path.read_text(encoding="utf-8"))
    assert messages[0]["type"] == "human" and any(m["type"] == "tool" for m in messages)


def test_every_dev_fault_is_run_on_every_arm_without_error():
    for family in FAMILY_NAMES:
        for operator_id in fault_ids(family, 2):
            for arm in ("sdk", "langgraph"):
                assert evaluated(family, 2, arm, operator_id)["fault"]["operator"] in operator_id


def test_a_run_that_hits_its_limit_keeps_every_call_in_the_sdk_record_but_not_in_langgraphs():
    sdk = evaluated("data", 1, "sdk", "limit")
    langgraph = evaluated("data", 1, "langgraph", "limit")
    assert sdk["trace"]["calls_complete"] and not langgraph["trace"]["calls_complete"]
    counts = langgraph["trace"]["counts"]["model-call"]
    assert counts["total"] - counts["present"] == 1


def test_parallel_plan_runs_are_repeatable(tmp_path):
    case = FAMILIES["plan"](4)
    for attempt in range(8):
        run = run_langgraph(case, None, tmp_path / str(attempt))
        assert run.status == "completed"
        assert not case.is_defective(
            case.deliverable, (run.workspace / case.deliverable).read_bytes()
        )


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_a_clean_run_raises_no_alarm_in_the_systems_own_checks(family, arm):
    assert evaluated(family, 1, arm)["native_check"] == []


def test_a_faulty_run_is_not_checked_natively():
    assert evaluated("doc", 1, "sdk", "limit")["native_check"] is None


def test_parallel_sdk_plan_runs_are_repeatable(tmp_path):
    case = FAMILIES["plan"](4)
    for attempt in range(5):
        run = run_sdk(case, None, tmp_path / str(attempt))
        assert run.status == "completed", run.reason
        assert not case.is_defective(
            case.deliverable, (run.workspace / case.deliverable).read_bytes()
        )


def test_every_method_names_only_real_agents_and_turns_on_the_multi_agent_family():
    labels = {node.label for node in FAMILIES["plan"](1).nodes()}
    for operator_id in fault_ids("plan", 1):
        for arm in ("sdk", "langgraph"):
            result = evaluated("plan", 1, arm, operator_id)
            for method, outcome in result["diagnoses"].items():
                for agent, index in outcome["steps"]:
                    assert agent in labels and index >= 0, (arm, operator_id, method, agent, index)
