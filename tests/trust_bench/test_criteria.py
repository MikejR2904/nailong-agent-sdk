from trust_bench import criteria
from trust_bench.criteria import (
    CRITERIA,
    Verdict,
    card,
    debuggability_verdicts,
    traceability_verdicts,
)
from trust_bench.report import render

from tests.support.trust_rows import clean_row, faulty_row

SHIPPED = "sdk-shipped:results+artifacts"
TRIVIAL = ("first-error", "last-error", "first-write", "last-write", "first-step", "last-step")


def by_id(verdicts):
    return {v.criterion.id: v for v in verdicts}


def clean_rows(count, *, outcomes=("correct",), present=4, arm="sdk", checks=True):
    rows = []
    for index in range(count):
        row = clean_row(f"c{index}", list(outcomes), present=present, arm=arm)
        row["degradation"] = {
            "drop-5%": {
                "strict": {"correct": 1, "wrong": 0, "abstain": 0},
                "lenient": {"correct": 1, "wrong": 0, "abstain": 0},
            }
        }
        if checks:
            row["native_check"] = []
        rows.append(row)
    return rows


def tamper_rows(trials_per_cell, *, misses=0, arm="sdk+anchor"):
    trials = [["audit", "edit", p, p >= misses] for p in range(trials_per_cell)]
    trials += [["journal", "delete", p, True] for p in range(trials_per_cell)]
    return [{"arm": arm, "trials": trials, "false_alarms": [], "pristine_checks": 1}]


def diagnosis_rows(shipped_by_class, trivial=0.0, *, per_class=60, f8=60, f8_guesses=0, arm="sdk"):
    rows = []
    for fault_class, rate in shipped_by_class.items():
        for index in range(per_class):
            hit = index < rate * per_class
            tops = {SHIPPED: hit, **{name: index < trivial * per_class for name in TRIVIAL}}
            rows.append(faulty_row(f"{fault_class}-{index}", fault_class, tops, arm=arm))
    for index in range(f8):
        guessed = index < f8_guesses
        tops = {SHIPPED: False}
        rows.append(
            faulty_row(f"F8-{index}", "F8", tops, arm=arm, has_truth=False, unsupported=guessed)
        )
    return rows


def test_every_criterion_states_its_rule_and_where_its_threshold_comes_from():
    for item in CRITERIA.values():
        assert item.claim and item.rule and item.provenance, item.id
    assert {"P1.1", "P1.2", "P1.3", "P1.4", "P1.5", "P4.0", "P4.1", "P4.2", "P4.3"} <= set(CRITERIA)


def test_full_call_records_pass_the_completeness_criterion_and_missing_ones_fail_it():
    full = by_id(traceability_verdicts(clean_rows(150), []))["P1.1"]
    assert full.passed and full.n == 150 * 8
    short = by_id(traceability_verdicts(clean_rows(150, present=3), []))["P1.1"]
    assert short.passed is False and "exact lower" in short.estimate


def test_no_clean_runs_means_not_run_rather_than_failed():
    verdicts = by_id(traceability_verdicts([], []))
    assert all(v.passed is None for v in verdicts.values()) and len(verdicts) == 5


def test_resolution_needs_a_lower_bound_of_ninety_five_percent():
    passing = by_id(traceability_verdicts(clean_rows(150), []))["P1.2"]
    assert passing.passed and passing.n == 150
    failing = by_id(traceability_verdicts(clean_rows(150, outcomes=("abstain",)), []))["P1.2"]
    assert failing.passed is False


def test_false_resolutions_must_be_zero_and_numerous_enough_to_bound_the_rate():
    assert by_id(traceability_verdicts(clean_rows(150), []))["P1.3"].passed
    assert by_id(traceability_verdicts(clean_rows(60), []))["P1.3"].passed is False
    wrong = clean_rows(150, outcomes=("wrong",))
    assert by_id(traceability_verdicts(wrong, []))["P1.3"].passed is False


def test_a_degraded_condition_with_a_wrong_answer_fails_the_false_resolution_criterion():
    rows = clean_rows(150)
    rows[0]["degradation"]["drop-5%"]["strict"] = {"correct": 0, "wrong": 1, "abstain": 0}
    assert by_id(traceability_verdicts(rows, []))["P1.3"].passed is False


def test_every_tamper_cell_must_be_caught_every_time_and_often_enough_to_bound_it():
    assert by_id(traceability_verdicts(clean_rows(5), tamper_rows(200)))["P1.4"].passed
    thin = by_id(traceability_verdicts(clean_rows(5), tamper_rows(100)))["P1.4"]
    assert thin.passed is False and "audit/edit 100/100" in thin.detail
    missed = by_id(traceability_verdicts(clean_rows(5), tamper_rows(200, misses=1)))["P1.4"]
    assert missed.passed is False and "audit/edit 199/200" in missed.detail


def test_without_a_tamper_battery_the_tamper_criterion_is_not_run():
    assert by_id(traceability_verdicts(clean_rows(5), []))["P1.4"].passed is None


def test_a_false_alarm_on_a_clean_run_fails_the_criterion():
    rows = clean_rows(150)
    assert by_id(traceability_verdicts(rows, []))["P1.5"].passed
    rows[3]["native_check"] = ["audit-chain"]
    assert by_id(traceability_verdicts(rows, []))["P1.5"].passed is False
    unchecked = clean_rows(150, checks=False)
    assert by_id(traceability_verdicts(unchecked, []))["P1.5"].passed is None


def test_a_method_that_finds_every_cause_passes_and_one_that_misses_a_class_fails():
    good = by_id(debuggability_verdicts(diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}), SHIPPED))
    assert good["P4.0"].passed and good["P4.1"].passed
    blind = by_id(debuggability_verdicts(diagnosis_rows({"F1": 1, "F2": 0, "F3": 1}), SHIPPED))
    assert blind["P4.1"].passed is False and "macro top-1 0.667" in blind["P4.1"].estimate


def test_a_family_a_trivial_rule_solves_is_flagged_as_unfit_before_the_system_is_judged():
    rows = diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}, trivial=0.9)
    verdicts = by_id(debuggability_verdicts(rows, SHIPPED))
    assert verdicts["P4.0"].passed is False


def test_guessing_when_no_decision_is_responsible_fails_and_so_does_too_little_evidence():
    rows = diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}, f8=60, f8_guesses=1)
    assert by_id(debuggability_verdicts(rows, SHIPPED))["P4.2"].passed is False
    few = diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}, f8=20)
    assert by_id(debuggability_verdicts(few, SHIPPED))["P4.2"].passed is False
    enough = diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}, f8=60)
    assert by_id(debuggability_verdicts(enough, SHIPPED))["P4.2"].passed


def test_a_framework_without_a_diagnosis_tool_is_not_run_on_the_attribution_criteria():
    rows = diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}, arm="langgraph")
    verdicts = by_id(debuggability_verdicts(rows, None))
    assert (
        verdicts["P4.1"].passed is None and "ships no diagnosis tool" in verdicts["P4.1"].estimate
    )


def test_twins_the_log_cannot_resolve_must_not_receive_a_unique_answer():
    def twin_row(index, named):
        row = faulty_row(f"t{index}", "F2", {SHIPPED: True})
        entry = {"named": named, "names_truth": False, "names_decoy": False, "steps": []}
        row["twins"] = {"ledger-scan+oracle": entry}
        return row

    ok = [twin_row(i, False) for i in range(60)]
    assert by_id(debuggability_verdicts(ok, SHIPPED))["P4.3"].passed
    guessing = [*ok[:59], twin_row(99, True)]
    assert by_id(debuggability_verdicts(guessing, SHIPPED))["P4.3"].passed is False
    assert (
        by_id(debuggability_verdicts([faulty_row("x", "F1", {SHIPPED: True})], SHIPPED))[
            "P4.3"
        ].passed
        is None
    )


def test_the_card_lists_the_endpoints_without_runs_as_not_run_and_never_averages():
    result = card(clean_rows(150) + clean_rows(3, arm="langgraph"), tamper_rows(200))
    assert set(result) == {"sdk", "langgraph"}
    for verdicts in result.values():
        listed = by_id(verdicts)
        assert listed["P2.1"].passed is None and listed["P3.1"].passed is None
        assert all(isinstance(v, Verdict) for v in verdicts)
    assert by_id(result["sdk"])["P1.4"].passed


def test_the_report_prints_the_card_with_each_threshold_and_its_source():
    text = render("T", clean_rows(150) + diagnosis_rows({"F1": 1, "F2": 1, "F3": 1}))
    assert "## Trust Profile card" in text and "| P1.2 |" in text and "| P4.1 |" in text
    assert "published" in text and "PASS" in text and "NOT RUN" in text


def test_the_thresholds_are_the_ones_the_pre_registration_states():
    assert criteria.COMPLETENESS_LOWER == 0.99 and criteria.RESOLUTION_LOWER == 0.95
    assert criteria.FALSE_RESOLUTION_UPPER == criteria.FALSE_ALARM_UPPER == 0.02
    assert criteria.TAMPER_LOWER == 0.98 and criteria.MACRO_TOP1_LOWER == 0.739
    assert criteria.SHORTCUT_CEILING == 0.40 and criteria.UNSUPPORTED_UPPER == 0.05
    assert criteria.ESCAPE_UPPER == criteria.ATTACK_UPPER == criteria.VIOLATION_UPPER == 0.02
    assert criteria.PUBLISHED_ESCAPE == 0.26 and criteria.FALSE_REJECTION_UPPER == 0.05
    assert criteria.CONTROL_LOWER == 0.90 and criteria.COVERAGE_LOWER == 0.98
    assert criteria.OVER_ESCALATION_CEILING == 0.20 and criteria.PAIRED_LOWER == 0.595
    assert criteria.INSTRUMENT_VIOLATION_LOWER == 0.50
