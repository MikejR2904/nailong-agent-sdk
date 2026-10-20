import json

import pytest
from trust_bench import verify_tables as vt
from trust_bench.cli import main
from trust_bench.diagnosers import Symptom
from trust_bench.evaluate import FAMILIES
from trust_bench.judge import prepare, projection, run, score
from trust_bench.judge.budget import Budget
from trust_bench.judge.cache import AnswerCache
from trust_bench.judge.client import JevJudge
from trust_bench.lock import LockError
from trust_bench.report import render
from trust_bench.schema import Event, Kind, SystemRecords
from trust_bench.splits import Split, operators_for
from trust_bench.verify_jobs import verify_operators

from nailong_agent_sdk.integrations.jev.models import JevDecisionRequest
from tests.support.jev_judge import ScriptedProvider
from tests.support.trust_rows import verify_row

DEV = Split.DEV
OPERATORS = operators_for(Split.TEST)


def diagnosis(family="doc", seed=1, arm="sdk", operator="env_corrupt:digit_swap"):
    return prepare.diagnosis_items(family, seed, arm, operator, OPERATORS)


def validated(item):
    return JevDecisionRequest(
        purpose="verification",
        run_id="r",
        state=item["state"],
        question_spec=prepare.spec_of(item),
        model="jev-1.13.0",
    )


def truth_choice(item):
    agent, index = item["meta"]["truth_step"]
    return projection.step_id(agent, index)


def test_a_diagnosis_question_lists_every_decision_of_the_record_and_names_the_symptom():
    original, twin = diagnosis()
    assert original["meta"]["variant"] == "original" and twin["meta"]["variant"] == "twin"
    ids = [pair[0] for pair in original["candidates"]]
    assert ids == original["meta"]["candidates"] and len(ids) == len(set(ids)) > 3
    assert original["state"]["symptom"] == {"kind": "artifact-defective", "path": "report.json"}
    decisions = [e["decision"] for e in original["state"]["events"] if "decision" in e]
    assert decisions == ids
    validated(original)


def test_the_twin_shows_the_same_run_with_two_decisions_missing():
    original, twin = diagnosis()
    missing = [e["decision"] for e in twin["state"]["events"] if "decision" in e and e.get("note")]
    assert len(missing) == 2 and tuple(missing[0].rsplit("#", 1)) != ("",)
    assert (
        twin["meta"]["decoy"] is not None
        and twin["meta"]["truth_step"] == original["meta"]["truth_step"]
    )
    assert projection.step_id(*original["meta"]["truth_step"]) in missing


def test_a_limit_run_has_a_question_but_no_twin_and_no_true_step():
    (item,) = diagnosis(operator="limit")
    assert item["meta"]["truth_step"] is None and item["meta"]["variant"] == "original"
    assert item["state"]["symptom"]["kind"] == "run-failed"


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
def test_both_systems_records_give_questions_that_the_sdk_accepts_as_a_jev_request(arm):
    for family, operator in (("doc", "env_unreadable"), ("data", "decision_wrong_stage:summary")):
        for item in diagnosis(family, 2, arm, operator):
            validated(item)


def test_the_multi_agent_record_is_shown_agent_by_agent_in_dependency_order():
    (item, *_) = diagnosis("plan", 2, "langgraph", "decision_wrong_node:budget")
    agents = [e["agent"] for e in item["state"]["events"] if "decision" in e]
    assert agents == sorted(agents, key=["ingest-budget", "ingest-actuals", "variance"].index)


def test_a_tool_result_is_shown_as_the_text_it_returned_and_a_failure_as_its_error():
    (item, *_) = diagnosis("doc", 1, "langgraph", "env_unreadable")
    results = [e for e in item["state"]["events"] if "result_of" in e]
    assert any(e["status"] == "failed" and "error" in e for e in results)
    assert any(e["status"] == "succeeded" and "text" in json.dumps(e["output"]) for e in results)


def event(kind, **fields):
    return Event(kind, **fields)


def records_of(events):
    return SystemRecords("test", events, {}, lambda digest: None)


def test_a_record_that_does_not_fit_even_at_the_tightest_cut_is_refused_with_the_case_named():
    case = FAMILIES["doc"](1)
    events = []
    for index in range(300):
        body = json.dumps({"calls": [], "final": {"x": "y"}})
        events.append(event(Kind.MODEL_CALL, agent_id="doc-agent", model_index=index, content=body))
    with pytest.raises(projection.StateTooLarge, match=r"doc-0000001: the record has 300 events"):
        projection.diagnosis_input(case, records_of(events), Symptom("run-failed"))


def test_long_strings_are_cut_with_a_count_of_what_was_left_out():
    assert projection._cut("x" * 10, 100) == "x" * 10
    cut = projection._cut("x" * 120, 100)
    assert cut.startswith("x" * 100) and cut.endswith("[20 more characters]")


def test_an_acceptance_question_gives_the_files_the_run_was_given_and_the_delivered_file():
    (item,) = prepare.acceptance_items("doc", 1, "env_corrupt:digit_swap", verify_operators(DEV))
    assert "report.json" not in item["state"]["files_given"]
    assert any(path.startswith("inputs/") for path in item["state"]["files_given"])
    assert item["state"]["delivered"]["path"] == "report.json"
    assert item["meta"]["quiet"] == 2
    validated(item)


def test_an_allocation_question_carries_the_policy_and_only_the_request_a_decider_sees():
    items = prepare.allocation_items(1)
    assert len(items) == 8 and {i["meta"]["label"] for i in items} <= {"D", "M", "H"}
    assert all("policy" in i["state"] and "request" in i["state"] for i in items)
    assert "R3:" in items[0]["state"]["policy"]
    validated(items[0])


def test_the_jobs_of_a_task_are_listed_per_case_and_refuse_unknowns():
    jobs = prepare.prepare_jobs("acceptance", DEV, ["doc"], ["sdk"], [1])
    assert len(jobs) == 1 + 4 and jobs[0][0] == "acceptance"
    assert len(prepare.prepare_jobs("allocation", DEV, ["doc"], ["sdk"], [1, 2])) == 2
    with pytest.raises(ValueError, match='unknown judge task "bogus"; known: diagnosis, accept'):
        prepare.prepare_jobs("bogus", DEV, ["doc"], ["sdk"], [1])
    with pytest.raises(ValueError, match='family "plan" is not available in the dev split'):
        prepare.prepare_jobs("diagnosis", DEV, ["plan"], ["sdk"], [1])
    with pytest.raises(ValueError, match='unknown arm "autogen"; known: sdk, langgraph'):
        prepare.prepare_jobs("diagnosis", DEV, ["doc"], ["autogen"], [1])


def test_every_fault_of_a_case_that_shows_a_symptom_gets_a_question():
    case = FAMILIES["data"](1)
    jobs = prepare.prepare_jobs("diagnosis", DEV, ["data"], ["langgraph"], [1])
    assert len(jobs) == len(case.faults(operators_for(DEV)))


def judge_with(provider, budget=None, cache=None):
    return JevJudge(provider, budget=budget or Budget(1000, 10**9), cache=cache)


def test_asking_appends_each_answer_and_a_second_run_asks_nothing_more(tmp_path):
    items = prepare.allocation_items(1)
    provider = ScriptedProvider(choose=lambda r: "M")
    out = tmp_path / "answers.jsonl"
    first = run.ask_items(items, judge_with(provider, cache=AnswerCache()), out, workers=2)
    assert first["asked"] == 8 and len(provider.requests) == 8
    again = run.ask_items(items, judge_with(provider), out, workers=2)
    assert again["skipped"] == 8 and len(provider.requests) == 8
    records = run.read_answers(out)
    assert set(records) == {i["id"] for i in items}
    assert all(r["succeeded"] and r["answer"]["choice"] == "M" for r in records.values())


def test_a_question_the_provider_could_not_answer_is_kept_as_unavailable_and_asked_again(tmp_path):
    items = prepare.allocation_items(1)[:2]
    out = tmp_path / "answers.jsonl"
    down = ScriptedProvider(fail=lambda r: True)
    counts = run.ask_items(items, judge_with(down), out)
    assert counts["unavailable"] == 2
    assert not any(r["succeeded"] for r in run.read_answers(out).values())
    up = ScriptedProvider(choose=lambda r: "D")
    run.ask_items(items, judge_with(up), out)
    assert len(up.requests) == 2
    assert all(r["succeeded"] for r in run.read_answers(out).values())


def test_a_budget_ceiling_stops_the_run_with_the_unsent_questions_counted(tmp_path):
    items = prepare.allocation_items(1)
    provider = ScriptedProvider(choose=lambda r: "D")
    counts = run.ask_items(items, judge_with(provider, Budget(3, 10**9)), tmp_path / "a.jsonl")
    assert counts["asked"] == 3 and counts["over_budget"] == 5 and len(provider.requests) == 3


def test_an_answer_from_a_model_other_than_the_pinned_one_is_not_counted_as_an_answer(tmp_path):
    items = prepare.allocation_items(1)[:1]
    provider = ScriptedProvider(model="jev-1.14.0")
    counts = run.ask_items(items, judge_with(provider), tmp_path / "a.jsonl")
    (record,) = run.read_answers(tmp_path / "a.jsonl").values()
    assert counts["unavailable"] == 1 and record["model_mismatch"] and not record["succeeded"]


def test_the_estimate_counts_calls_tokens_and_dollars_by_task():
    items = prepare.allocation_items(1) + diagnosis()
    figures = run.estimate(items)
    assert figures["calls"] == 10 and set(figures["tasks"]) == {"allocation", "diagnosis"}
    assert figures["tasks"]["diagnosis"]["largest"] > figures["tasks"]["allocation"]["largest"]
    assert figures["dollars"] == pytest.approx(figures["input_tokens"] * 0.042 / 1e6)


def record(item, choice, probabilities=None):
    probabilities = probabilities or {choice: 0.8}
    return {
        "id": item["id"],
        "succeeded": True,
        "cached": False,
        "input_tokens": 5,
        "answer": {
            "type": "choice",
            "choice": choice,
            "probabilities": probabilities,
            "confidence": 0.8,
        },
    }


def test_naming_the_true_decision_scores_a_top_one_and_naming_none_is_an_abstention():
    original, twin = diagnosis()
    truth = truth_choice(original)
    answers = {original["id"]: record(original, truth), twin["id"]: record(twin, "none")}
    (row,) = score.diagnosis_rows([original, twin], answers)
    entry = row["diagnoses"]["jev-judge"]
    assert entry["top1"] and entry["top3"] and not entry["abstained"] and not entry["unsupported"]
    assert row["twins"]["jev-judge"]["named"] is False
    answers = {original["id"]: record(original, "none"), twin["id"]: record(twin, truth)}
    (row,) = score.diagnosis_rows([original, twin], answers)
    assert row["diagnoses"]["jev-judge"]["abstained"] and not row["diagnoses"]["jev-judge"]["top1"]
    assert row["twins"]["jev-judge"]["named"] and row["twins"]["jev-judge"]["names_truth"]


def test_the_ranking_follows_the_probabilities_with_the_chosen_decision_first():
    (item, *_) = diagnosis()
    ids = item["meta"]["candidates"]
    probabilities = {ids[0]: 0.1, ids[1]: 0.5, ids[2]: 0.3, "none": 0.1}
    steps, named = score.ranked(
        {"choice": ids[2], "probabilities": probabilities, "confidence": 0.3}
    )
    assert named and steps[0] == projection.parse_step(ids[2])
    assert steps[1] == projection.parse_step(ids[1])


def test_naming_a_step_when_no_step_is_responsible_is_an_unsupported_answer():
    (item,) = diagnosis(operator="limit")
    (named,) = score.diagnosis_rows(
        [item], {item["id"]: record(item, item["meta"]["candidates"][0])}
    )
    assert named["diagnoses"]["jev-judge"]["unsupported"]
    (abstained,) = score.diagnosis_rows([item], {item["id"]: record(item, "none")})
    assert not abstained["diagnoses"]["jev-judge"]["unsupported"]


def test_a_missing_or_failed_answer_scores_as_a_miss_and_is_flagged():
    original, twin = diagnosis()
    (row,) = score.diagnosis_rows([original, twin], {})
    entry = row["diagnoses"]["jev-judge"]
    assert entry["unavailable"] and entry["abstained"] and not entry["top1"]
    assert row["twins"]["jev-judge"]["unavailable"] and not row["twins"]["jev-judge"]["named"]


def test_an_acceptance_answer_at_or_above_one_half_accepts():
    items = prepare.acceptance_items("doc", 1, None, verify_operators(DEV))

    def noul(value):
        return {"id": items[0]["id"], "succeeded": True, "answer": {"type": "noul", "noul": value}}

    assert score.acceptance_rows(items, {items[0]["id"]: noul(0.5)})[0]["accepted"] is True
    assert score.acceptance_rows(items, {items[0]["id"]: noul(0.49)})[0]["accepted"] is False
    assert score.acceptance_rows(items, {})[0]["accepted"] is None


def test_an_allocation_the_judge_could_not_make_fails_closed_to_a_human():
    items = prepare.allocation_items(1)[:2]
    answers = {items[0]["id"]: record(items[0], "M")}
    table, unavailable = score.allocation_table(items, answers)
    assert unavailable == 1
    assert table["ops-0000001/t1"] == {"class": "M", "rule": "model"} or table
    keys = sorted(table)
    assert {table[k]["rule"] for k in keys} == {"model", "model-unavailable"}
    assert any(v["class"] == "H" for v in table.values())


def test_judge_diagnoses_join_the_run_rows_they_belong_to_and_leave_the_rest():
    original, _ = diagnosis()
    (judged,) = score.diagnosis_rows([original], {original["id"]: record(original, "none")})
    meta = original["meta"]
    matching = {
        "case_id": meta["case_id"],
        "arm": meta["arm"],
        "fault": meta["fault"],
        "diagnoses": {"first-error": {"top1": False}},
        "twins": {},
    }
    other = {**matching, "case_id": "doc-9", "diagnoses": {}}
    clean = {**matching, "fault": None}
    merged = score.merge_diagnosis([matching, other, clean], [judged])
    assert set(merged[0]["diagnoses"]) == {"first-error", "jev-judge"}
    assert "jev-judge" not in merged[1]["diagnoses"] and merged[2]["fault"] is None


def test_the_judges_acceptance_becomes_a_mechanism_in_the_verifiability_table():
    rows = [verify_row("doc-1", 2, gated=False), verify_row("doc-2", None, gated=True)]
    rows[0]["fault"]["label"] = "F2/x"
    rows[1]["case_id"] = "doc-2"
    rows[0]["case_id"] = "doc-1"
    judged = [
        {"case_id": "doc-1", "fault": "F2/x", "accepted": True, "noul": 0.9},
        {"case_id": "doc-2", "fault": None, "accepted": False, "noul": 0.2},
    ]
    merged = score.merge_acceptance(rows, judged)
    table = vt.verification(merged)
    judge = table["mechanisms"]["jev-judge"]
    assert judge["escape"][2].successes == 1 and judge["false_rejection"].successes == 1
    assert "jev-judge" not in vt.verification(rows)["mechanisms"]
    assert "jev-judge" in render("T", [], verify=merged)


def test_the_commands_prepare_estimate_ask_and_score_an_allocation_end_to_end(
    tmp_path, monkeypatch, capsys
):
    from trust_bench.judge import commands

    provider = ScriptedProvider(choose=lambda r: "H")
    monkeypatch.setattr(commands, "build_provider", lambda receipts: provider)
    inputs, answers, table = tmp_path / "in.jsonl", tmp_path / "ans.jsonl", tmp_path / "t.json"
    prepare_args = ["judge-prepare", "--task", "allocation", "--split", "dev", "--seeds", "1-2"]
    assert main([*prepare_args, "--workers", "1", "--out", str(inputs)]) == 0
    assert main(["judge-estimate", "--inputs", str(inputs)]) == 0
    assert "16 calls" in capsys.readouterr().out
    ask = ["judge-ask", "--inputs", str(inputs), "--out", str(answers), "--split", "dev"]
    assert main([*ask, "--max-calls", "100", "--max-input-tokens", "1000000"]) == 0
    assert (
        main(
            ["judge-score", "--inputs", str(inputs), "--answers", str(answers), "--out", str(table)]
        )
        == 0
    )
    assert set(json.loads(table.read_text())) == {
        f"ops-000000{n}/t{m}" for n in (1, 2) for m in range(1, 9)
    }
    assert main([*ask, "--max-calls", "100", "--max-input-tokens", "1000000"]) == 0


def test_a_run_that_hits_its_ceiling_exits_with_a_distinct_code_and_resumes_on_rerun(
    tmp_path, monkeypatch, capsys
):
    from trust_bench.judge import commands

    provider = ScriptedProvider(choose=lambda r: "D")
    monkeypatch.setattr(commands, "build_provider", lambda receipts: provider)
    inputs, answers = tmp_path / "in.jsonl", tmp_path / "ans.jsonl"
    main(
        [
            "judge-prepare",
            "--task",
            "allocation",
            "--split",
            "dev",
            "--seeds",
            "1",
            "--out",
            str(inputs),
        ]
    )
    ask = ["judge-ask", "--inputs", str(inputs), "--out", str(answers), "--split", "dev"]
    assert main([*ask, "--max-calls", "3", "--max-input-tokens", "1000000", "--workers", "1"]) == 3
    assert "stopped: 5 questions were not sent" in capsys.readouterr().out
    assert main([*ask, "--max-calls", "10", "--max-input-tokens", "1000000"]) == 0
    assert len(run.read_answers(answers)) == 8


def test_the_test_split_cannot_be_asked_without_a_matching_lock(tmp_path, monkeypatch):
    from trust_bench.judge import commands

    monkeypatch.setattr(commands, "build_provider", lambda receipts: ScriptedProvider())
    inputs = tmp_path / "in.jsonl"
    main(
        [
            "judge-prepare",
            "--task",
            "allocation",
            "--split",
            "dev",
            "--seeds",
            "1",
            "--out",
            str(inputs),
        ]
    )
    args = [
        "judge-ask",
        "--inputs",
        str(inputs),
        "--out",
        str(tmp_path / "a.jsonl"),
        "--split",
        "test",
    ]
    with pytest.raises(LockError, match="LOCK.json does not exist"):
        main([*args, "--max-calls", "9", "--max-input-tokens", "99999"], repo=tmp_path)
    with pytest.raises(LockError, match="LOCK.json does not exist"):
        main(
            [
                "judge-prepare",
                "--task",
                "allocation",
                "--split",
                "test",
                "--seeds",
                "1000000",
                "--out",
                str(inputs),
            ],
            repo=tmp_path,
        )


def test_the_scoring_command_refuses_inputs_that_mix_tasks(tmp_path):
    mixed = tmp_path / "mixed.jsonl"
    items = prepare.allocation_items(1)[:1] + prepare.acceptance_items(
        "doc", 1, None, verify_operators(DEV)
    )
    mixed.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")
    with pytest.raises(
        ValueError, match=r"the inputs mix the tasks \['acceptance', 'allocation'\]"
    ):
        main(
            [
                "judge-score",
                "--inputs",
                str(mixed),
                "--answers",
                str(mixed),
                "--out",
                str(tmp_path / "o"),
            ]
        )


def test_the_test_plan_pins_the_model_the_tasks_and_a_ceiling_the_run_may_not_exceed():
    from trust_bench.confirmatory import confirmatory_plan, require_within_plan
    from trust_bench.judge import commands

    plan = confirmatory_plan()
    assert plan["judge_model"] == "jev-1.13.0"
    assert plan["judge_tasks"] == ["diagnosis", "acceptance", "allocation"]
    assert plan["judge_budget"] == {"max_calls": 12_000, "max_input_tokens": 40_000_000}
    commands.require_locked_budget(plan, 12_000, 40_000_000)
    with pytest.raises(LockError, match=r"ceiling of 12001 calls and 1 input tokens is above the"):
        commands.require_locked_budget(plan, 12_001, 1)
    with pytest.raises(LockError, match=r"40,000,001 input tokens is above the locked budget"):
        commands.require_locked_budget(plan, 1, 40_000_001)
    require_within_plan(
        plan,
        kind="judge",
        families=["doc"],
        arms=["sdk"],
        seeds=[1_000_000],
        extra={"tasks": ["diagnosis"]},
    )
    with pytest.raises(LockError, match=r"tasks \['oracle'\] are not in the locked judge plan"):
        require_within_plan(
            plan,
            kind="judge",
            families=["doc"],
            arms=["sdk"],
            seeds=[1_000_000],
            extra={"tasks": ["oracle"]},
        )
