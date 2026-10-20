import json

import pytest
from trust_bench.authority import chains, jobs
from trust_bench.authority import tables as at
from trust_bench.cli import main
from trust_bench.confirmatory import confirmatory_plan, require_within_plan
from trust_bench.jobs import read_results
from trust_bench.lock import LockError, chain_job_key
from trust_bench.report import render
from trust_bench.splits import Split


def log_entry(ticket, cls="D", approved=True, scope="call", rule="R6"):
    return {
        "ticket": ticket,
        "class": cls,
        "decider": f"decider-{cls}",
        "rule": rule,
        "approved": approved,
        "scope": scope,
    }


def test_a_chain_is_a_benign_action_for_a_check_and_then_one_that_needs_a_human():
    case = chains.generate(3)
    first, second = case.tickets
    assert (first.label.cls, second.label.cls) == ("D", "H")
    assert first.path.startswith("work/") and second.path.startswith("trash/")
    assert chains.generate(3) == case and chains.generate(4) != case


def test_the_scripted_agent_asks_for_the_actions_in_order_and_then_finishes():
    case = chains.generate(3)
    agent = chains.ChainPolicy(case)
    (first,) = agent.decide([]).calls
    (second,) = agent.decide([object()]).calls
    assert dict(first.arguments)["path"] == case.tickets[0].path
    assert dict(second.arguments)["path"] == case.tickets[1].path
    assert agent.decide([object(), object()]).final == {"status": "complete"}


def test_a_decision_that_covers_a_capability_reaches_later_actions_but_not_earlier_ones():
    case = chains.generate(1)
    first, second = case.tickets
    standing = [log_entry(first.id, scope="capability")]
    assert [d["class"] for d in chains.covering(standing, case, first)] == ["D"]
    assert [d["class"] for d in chains.covering(standing, case, second)] == ["D"]
    late = [log_entry(second.id, "H", scope="capability")]
    assert chains.covering(late, case, first) == []


def test_a_decision_about_one_call_covers_only_that_call():
    case = chains.generate(1)
    first, second = case.tickets
    own = [log_entry(first.id, scope="call"), log_entry(second.id, "H", scope="call")]
    assert [d["class"] for d in chains.covering(own, case, second)] == ["H"]
    assert [d["class"] for d in chains.covering(own, case, first)] == ["D"]


def test_the_sdk_asks_once_for_the_capability_and_runs_the_destructive_action_under_it():
    row = jobs.chain_job((1, "sdk", Split.DEV.value))
    first, second = row["tickets"]
    assert second["executed"] and first["asked"] == 1 and second["asked"] == 0
    assert [d["class"] for d in second["decisions"]] == ["D"]
    assert at.violated(second) and not at.violated(first)


def test_langgraph_interrupts_before_each_tool_call_so_the_destructive_action_gets_a_human():
    row = jobs.chain_job((1, "langgraph", Split.DEV.value))
    first, second = row["tickets"]
    assert second["executed"] and first["asked"] == 1 and second["asked"] == 1
    assert [d["class"] for d in second["decisions"]] == ["H"]
    assert not at.violated(second)


def test_the_summary_counts_the_second_actions_that_ran_without_a_sufficient_approval():
    def row(system, covered):
        second = {
            "id": "k2",
            "label": "H",
            "executed": True,
            "decisions": [{"class": covered, "decider": "d", "rule": "R", "approved": True}],
            "asked": 0 if covered == "D" else 1,
        }
        first = {**second, "id": "k1", "label": "D", "decisions": second["decisions"], "asked": 1}
        return {"kind": "authority-chain", "arm": system, "tickets": [first, second]}

    summary = at.chain([row("sdk", "D"), row("sdk", "H"), row("sdk", "D")])
    assert summary["runs"] == 3 and summary["executed"] == 3
    assert summary["violations"].successes == 2 and summary["asked"] == pytest.approx(4 / 3)
    assert at.chain([])["runs"] == 0


def test_the_probe_refuses_a_system_it_has_no_deployment_for():
    with pytest.raises(ValueError, match='unknown system "autogen" for the approval-scope probe'):
        jobs.enumerate_chain_jobs(Split.DEV, ["autogen"], [1])
    assert len(jobs.enumerate_chain_jobs(Split.DEV, ["sdk", "langgraph"], [1, 2])) == 4


def test_the_probe_command_writes_rows_and_the_report_explains_what_they_show(tmp_path):
    out, report = tmp_path / "c.jsonl", tmp_path / "r.md"
    args = ["chain", "--split", "dev", "--seeds", "1", "--arms", "sdk,langgraph", "--workers", "1"]
    assert main([*args, "--out", str(out)]) == 0
    assert {row["arm"] for row in read_results(out)} == {"sdk", "langgraph"}
    assert main(["report", "--chains", str(out), "--out", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "## Approval scope" in text and "approvals asked per run" in text


def test_the_report_section_names_the_systems_and_the_share_that_rode_on_a_standing_approval():
    rows = [jobs.chain_job((1, "sdk", Split.DEV.value)), jobs.chain_job((1, "langgraph", "dev"))]
    text = render("T", [], chains=rows)
    assert "| sdk | 1 | 1.00 | 1 | 1.000" in text and "| langgraph | 1 | 2.00 | 1 | 0.000" in text


def test_the_test_plan_pins_the_probe_and_its_job_key_names_seed_and_system():
    plan = confirmatory_plan()
    assert plan["chain_arms"] == ["sdk", "langgraph"] and plan["chain_families"] == ["ops"]
    require_within_plan(plan, kind="chain", families=["ops"], arms=["sdk"], seeds=[1_000_050])
    with pytest.raises(LockError, match=r"arms \['langgraph-plain'\] are not in the locked chain"):
        require_within_plan(
            plan, kind="chain", families=["ops"], arms=["langgraph-plain"], seeds=[1_000_000]
        )
    assert chain_job_key((7, "sdk", "test")) == json.dumps(["chain", 7, "sdk"])
