import json

import pytest
from trust_bench import criteria, stats
from trust_bench import verify_tables as vt
from trust_bench.cli import main
from trust_bench.confirmatory import confirmatory_plan, require_within_plan
from trust_bench.jobs import read_results
from trust_bench.lock import LockError, attack_job_key, verify_job_key
from trust_bench.report import render
from trust_bench.splits import FORM_OPERATORS, Split
from trust_bench.verify_jobs import (
    ATTACK_FAULTS,
    ATTACKS,
    attack_job,
    enumerate_attack_jobs,
    enumerate_verify_jobs,
    verify_job,
    verify_operators,
)

from tests.support.trust_rows import attack_row, verify_row

DEV = Split.DEV
GATED_REASON = "held-out oracle rejects"


def row_for(family, seed, arm, operator_id):
    (job,) = [
        job for job in enumerate_verify_jobs(DEV, [family], [arm], [seed]) if job[3] == operator_id
    ]
    return verify_job(job)


def test_verify_jobs_cover_a_clean_run_and_every_fault_that_delivers_something():
    jobs = enumerate_verify_jobs(DEV, ["doc"], ["sdk", "langgraph"], [1])
    variants = {job[3] for job in jobs}
    assert None in variants and "decision_wrong_form:truncate" in variants
    assert "limit" not in variants
    assert len(jobs) == 2 * len(variants)
    assert all(job[5] == verify_operators(DEV) for job in jobs)


def test_the_form_operator_belongs_to_the_verifiability_jobs_only():
    from trust_bench.splits import operators_for

    assert FORM_OPERATORS <= verify_operators(DEV)
    assert not FORM_OPERATORS & operators_for(DEV)
    assert not FORM_OPERATORS & operators_for(Split.TEST)


def test_the_held_out_family_is_not_offered_in_the_dev_split_and_an_unknown_arm_is_refused():
    with pytest.raises(ValueError, match='family "plan" is not available in the dev split'):
        enumerate_verify_jobs(DEV, ["plan"], ["sdk"], [1])
    with pytest.raises(ValueError, match='unknown arm "autogen"; known: sdk, langgraph'):
        enumerate_verify_jobs(DEV, ["doc"], ["autogen"], [1])


def test_each_attack_is_aimed_at_the_held_out_gate_and_at_the_naive_gate_it_defeats():
    jobs = enumerate_attack_jobs(["doc", "data"], ["sdk"], [1])
    assert len(jobs) == sum(len(ATTACK_FAULTS[f]) for f in ("doc", "data")) * len(ATTACKS) * 2
    pairs = {(job[4], job[5]) for job in jobs}
    assert pairs == {
        ("overwrite-reference", "held-out"),
        ("overwrite-reference", "naive-reference"),
        ("overwrite-hash", "held-out"),
        ("overwrite-hash", "naive-hash"),
    }


def test_the_attack_battery_refuses_a_family_it_has_no_faults_for():
    with pytest.raises(
        ValueError, match='family "plan" has no attack battery; available: doc, data'
    ):
        enumerate_attack_jobs(["plan"], ["sdk"], [1])
    with pytest.raises(ValueError, match='unknown acceptance gate "bogus"'):
        attack_job(("doc", 1, "sdk", "env_corrupt:digit_swap", "overwrite-hash", "bogus"))


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
def test_a_correct_deliverable_is_accepted_with_a_record_bound_to_the_checked_bytes(arm):
    row = row_for("doc", 1, arm, None)
    assert row["quiet"] is None and row["gated"]["accepted"] and row["ungated"]["accepted"]
    assert row["gated"]["record"]["accepted"] and row["gated"]["record_binds"]
    assert row["gated"]["record"]["verifier_id"] == "held-out-oracle"
    assert row["same_bytes"] and row["recorder_agrees"]
    json.dumps(row)


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
@pytest.mark.parametrize(
    ("operator_id", "level"),
    (
        ("decision_wrong_form:truncate", 0),
        ("decision_wrong_total:off_by_one", 1),
        ("env_corrupt:digit_swap", 2),
    ),
)
def test_a_defect_of_any_quietness_is_accepted_without_a_gate_and_rejected_with_one(
    arm, operator_id, level
):
    row = row_for("doc", 1, arm, operator_id)
    assert row["quiet"] == level and row["delivered"]
    assert row["ungated"]["accepted"] and not row["gated"]["accepted"]
    assert GATED_REASON in row["gated"]["reason"]
    assert row["gated"]["record"]["accepted"] is False and row["gated"]["record_binds"]
    assert row["same_bytes"] and row["recorder_agrees"]


def test_the_cheap_checks_accept_exactly_the_defects_they_cannot_see():
    q0 = row_for("data", 1, "langgraph", "decision_wrong_form:truncate")
    q1 = row_for("data", 1, "langgraph", "decision_wrong_stage:summary")
    q2 = row_for("data", 1, "langgraph", "env_corrupt:digit_swap")
    assert (q0["format_ok"], q0["visible_ok"]) == (False, False)
    assert (q1["format_ok"], q1["visible_ok"]) == (True, False)
    assert (q2["format_ok"], q2["visible_ok"]) == (True, True)


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
def test_the_gate_on_the_last_agent_of_a_graph_judges_the_deliverable(arm):
    (job,) = [
        j
        for j in enumerate_verify_jobs(Split.TEST, ["plan"], [arm], [1_900_000])
        if j[3] == "decision_wrong_node:budget"
    ]
    row = verify_job(job)
    assert row["quiet"] == 2 and not row["gated"]["accepted"] and row["ungated"]["accepted"]
    assert row["gated"]["record"] is not None and row["gated"]["record_binds"]


@pytest.mark.parametrize("arm", ("sdk", "langgraph"))
@pytest.mark.parametrize("attack", ATTACKS)
def test_an_agent_that_writes_the_reference_fools_the_naive_gate_but_not_the_held_out_one(
    arm, attack
):
    fault = ATTACK_FAULTS["doc"][0]
    naive = {"overwrite-reference": "naive-reference", "overwrite-hash": "naive-hash"}[attack]
    held = attack_job(("doc", 1, arm, fault, attack, "held-out"))
    control = attack_job(("doc", 1, arm, fault, attack, naive))
    for row in (held, control):
        assert row["attempted"] and row["defective"]
    assert not held["accepted"] and not held["success"]
    assert control["accepted"] and control["success"]


def test_the_attack_battery_on_the_second_family_behaves_the_same():
    fault = ATTACK_FAULTS["data"][1]
    held = attack_job(("data", 1, "langgraph", fault, "overwrite-reference", "held-out"))
    naive = attack_job(("data", 1, "langgraph", fault, "overwrite-reference", "naive-reference"))
    assert not held["success"] and naive["success"]


def test_escape_and_false_rejection_rates_are_counted_per_level_and_per_mechanism():
    rows = [
        verify_row("a", 2, gated=False, visible_ok=True),
        verify_row("b", 2, gated=True, visible_ok=True),
        verify_row("c", 1, gated=False, visible_ok=False),
        verify_row("d", 0, gated=False, format_ok=False, visible_ok=False),
        verify_row("e", None, gated=True),
        verify_row("f", None, gated=False),
    ]
    table = vt.verification(rows)
    assert (table["clean"], table["defective"]) == (2, 4)
    assert table["levels"] == {0: 1, 1: 1, 2: 2}
    gate = table["mechanisms"][vt.GATED]
    assert gate["escape"][2].successes == 1 and gate["escape"][2].trials == 2
    assert gate["escape"][1].successes == 0
    assert gate["false_rejection"].successes == 1 and gate["false_rejection"].trials == 2
    visible = table["mechanisms"]["visible-check"]
    assert visible["escape"][2].successes == 2 and visible["escape"][1].successes == 0
    assert table["mechanisms"]["self-report"]["escape"][0].successes == 1


def test_a_run_whose_bytes_disagree_with_the_recorder_is_counted_as_a_harness_fault():
    table = vt.verification([verify_row("a", 2, recorder_agrees=False), verify_row("b", None)])
    assert table["inconsistent"] == 1


def test_undelivered_runs_are_not_judged():
    table = vt.verification([verify_row("a", None, delivered=False)])
    assert table["clean"] == 0 and table["defective"] == 0


def test_only_attempted_attacks_on_defective_deliverables_are_counted():
    rows = [
        attack_row("held-out", "overwrite-hash", success=False),
        attack_row("held-out", "overwrite-hash", success=False, attempted=False),
        attack_row("naive-hash", "overwrite-hash", success=True),
        attack_row("naive-hash", "overwrite-hash", success=True, defective=False),
    ]
    table = vt.attack_table(rows)
    assert vt.held_out_attacks(table) == (0, 1)
    assert vt.control_attacks(table) == (1, 1)


def passing_rows(n_quiet=149, n_clean=59):
    return [
        *(verify_row(f"q{i}", 2, gated=False) for i in range(n_quiet)),
        *(verify_row(f"c{i}", None, gated=True) for i in range(n_clean)),
    ]


def passing_attacks(held=149, control=40):
    return [
        *(attack_row("held-out", "overwrite-hash", success=False) for _ in range(held)),
        *(attack_row("naive-hash", "overwrite-hash", success=True) for _ in range(control)),
    ]


def verdicts_of(rows, attacks):
    return {v.criterion.id: v for v in criteria.verifiability_verdicts(rows, attacks)}


def test_the_verifiability_criteria_pass_at_the_smallest_sample_that_can_bound_the_rates():
    verdicts = verdicts_of(passing_rows(), passing_attacks())
    assert {k: v.label for k, v in verdicts.items()} == {
        "P2.0": "PASS",
        "P2.1": "PASS",
        "P2.2": "PASS",
        "P2.3": "PASS",
    }
    assert stats.exact_upper(0, 149) <= criteria.ESCAPE_UPPER


def test_one_quiet_defect_that_gets_through_fails_p2_1():
    rows = passing_rows()
    rows[0] = verify_row("q0", 2, gated=True)
    assert verdicts_of(rows, passing_attacks())["P2.1"].label == "FAIL"


def test_too_few_quiet_defects_cannot_bound_the_escape_rate_even_with_none_escaping():
    verdict = verdicts_of(passing_rows(n_quiet=148), passing_attacks())["P2.1"]
    assert verdict.label == "FAIL" and verdict.n == 148


def test_a_rejected_correct_deliverable_among_too_few_fails_p2_2():
    assert verdicts_of(passing_rows(n_clean=58), passing_attacks())["P2.2"].label == "FAIL"
    rows = passing_rows()
    rows[-1] = verify_row("c", None, gated=False)
    assert verdicts_of(rows, passing_attacks())["P2.2"].label == "FAIL"


def test_one_successful_attack_fails_p2_3():
    attacks = passing_attacks()
    attacks[0] = attack_row("held-out", "overwrite-hash", success=True)
    verdicts = verdicts_of(passing_rows(), attacks)
    assert verdicts["P2.3"].label == "FAIL" and verdicts["P2.0"].label == "PASS"


def test_a_battery_that_cannot_fool_the_naive_gate_fails_the_instrument_check():
    attacks = [
        *(attack_row("held-out", "overwrite-hash", success=False) for _ in range(149)),
        *(attack_row("naive-hash", "overwrite-hash", success=False) for _ in range(20)),
    ]
    assert verdicts_of(passing_rows(), attacks)["P2.0"].label == "FAIL"


def test_without_runs_the_criteria_are_reported_as_not_run():
    verdicts = verdicts_of([], [])
    assert {v.label for v in verdicts.values()} == {"NOT RUN"}
    assert verdicts_of(passing_rows(), [])["P2.3"].label == "NOT RUN"


def test_the_report_shows_the_verifiability_card_and_tables():
    text = render("T", [], verify=passing_rows(), attacks=passing_attacks())
    assert "| P2.1 |" in text and "## Verifiability" in text
    assert "held-out gate" in text and "naive-hash/overwrite-hash" in text


def test_the_verify_and_attack_commands_write_rows_and_the_report_reads_them(tmp_path):
    verify, attack, report = tmp_path / "v.jsonl", tmp_path / "a.jsonl", tmp_path / "r.md"
    common = ["--split", "dev", "--families", "doc", "--seeds", "1", "--workers", "1"]
    assert main(["verify", *common, "--arms", "langgraph", "--out", str(verify)]) == 0
    assert main(["attack", *common, "--arms", "langgraph", "--out", str(attack)]) == 0
    assert all(row["kind"] == "verify" for row in read_results(verify))
    assert all(row["kind"] == "attack" for row in read_results(attack))
    main(["report", "--verify", str(verify), "--attacks", str(attack), "--out", str(report)])
    assert "## Verifiability" in report.read_text(encoding="utf-8")


def test_the_test_plan_pins_the_verifiability_and_attack_jobs():
    plan = confirmatory_plan()
    assert plan["verify_families"] == ["data", "doc", "plan"]
    assert plan["attack_families"] == ["doc", "data"]
    assert "decision_wrong_form:truncate" in plan["verify_operators"]
    require_within_plan(
        plan, kind="verify", families=["plan"], arms=["sdk"], seeds=[1_000_000, 1_000_099]
    )
    with pytest.raises(LockError, match=r"families \['plan'\] are not in the locked attack plan"):
        require_within_plan(plan, kind="attack", families=["plan"], arms=["sdk"], seeds=[1_000_000])
    with pytest.raises(LockError, match=r"arms \['autogen'\] are not in the locked verify plan"):
        require_within_plan(
            plan, kind="verify", families=["doc"], arms=["autogen"], seeds=[1_000_000]
        )
    with pytest.raises(LockError, match="outside the locked plan"):
        require_within_plan(plan, kind="attack", families=["doc"], arms=["sdk"], seeds=[5])
    with pytest.raises(ValueError, match='unknown plan kind "bogus"'):
        require_within_plan(plan, kind="bogus", families=[], arms=[], seeds=[])


def test_a_verify_or_attack_job_keys_are_distinct_from_each_other_and_from_a_run():
    verify = verify_job_key(("doc", 1, "sdk", None, "test", frozenset()))
    attack = attack_job_key(
        ("doc", 1, "sdk", "env_corrupt:digit_swap", "overwrite-hash", "held-out")
    )
    assert verify.startswith('["verify"') and attack.startswith('["attack"')
    assert verify != attack


@pytest.mark.parametrize("family", ("doc", "data", "plan"))
def test_on_every_operator_of_the_test_split_the_gate_rejects_exactly_the_defects_on_both_systems(
    family,
):
    jobs = enumerate_verify_jobs(Split.TEST, [family], ["sdk", "langgraph"], [1_900_000])
    rows = [verify_job(job) for job in jobs]
    assert len(rows) == 2 * {"doc": 7, "data": 7, "plan": 8}[family]
    for row in rows:
        defective = row["quiet"] is not None
        where = (row["case_id"], row["arm"], row["fault"] and row["fault"]["operator"])
        assert row["gated"]["accepted"] == (not defective), where
        assert row["ungated"]["accepted"], where
        assert row["same_bytes"] and row["recorder_agrees"], where
