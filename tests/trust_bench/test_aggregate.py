import pytest
from trust_bench import aggregate as agg
from trust_bench.report import render
from trust_bench.tamper_jobs import tamper_table

from tests.support.trust_rows import clean_row, faulty_row


def test_traceability_pools_event_counts_across_clean_runs_only():
    rows = [clean_row("a", ["correct", "correct"]), clean_row("b", ["wrong", "abstain"])]
    rows.append({**clean_row("c", ["correct"]), "fault": {"class": "F1"}})
    out = agg.traceability(rows)
    assert out["runs"] == 2
    assert out["model-call"]["completeness"].trials == 8
    resolution = out["resolution"]
    assert resolution["artifacts"] == 4 and resolution["darr"].successes == 2
    assert resolution["false_resolution"].successes == 1 and resolution["abstained"].successes == 1
    assert resolution["source_recall"] == 0.5


def test_a_run_is_call_complete_only_if_every_clean_run_says_so():
    rows = [clean_row("a", ["correct"]), clean_row("b", ["correct"], complete=False)]
    assert agg.traceability(rows)["calls_complete"].successes == 1


def test_traceability_of_no_clean_runs_is_empty_rather_than_an_error():
    assert agg.traceability([]) == {"runs": 0}


def test_the_cluster_interval_treats_each_case_as_one_observation():
    rows = [clean_row(f"c{i}", ["correct", "correct"]) for i in range(6)]
    estimate, low, high = agg.traceability(rows)["resolution"]["darr_cluster"]
    assert estimate == 1.0 and low <= estimate <= high


def test_degradation_counts_are_summed_over_runs_and_resolvers():
    rows = [clean_row("a", ["correct"]), clean_row("b", ["correct"])]
    table = agg.degradation_table(rows)["drop-5%"]
    assert table["strict"]["abstain"].successes == 2 and table["strict"]["abstain"].trials == 4
    assert table["lenient"]["correct"].estimate == 1.0


def test_diagnosis_reports_top_one_by_class_and_the_macro_average():
    rows = [
        faulty_row("a", "F1", {"m": True}),
        faulty_row("b", "F2", {"m": False}),
        faulty_row("c", "F2", {"m": False}),
        faulty_row("d", "F3", {"m": True}),
        faulty_row("e", "F8", {"m": False}, has_truth=False),
    ]
    out = agg.diagnosis(rows)
    method = out["methods"]["m"]
    assert out["cases"] == 5
    assert method["top1"].successes == 2 and method["top1"].trials == 4
    assert method["by_class"]["F2"].successes == 0 and method["by_class"]["F1"].successes == 1
    assert method["macro_top1"][0] == pytest.approx((1 + 0 + 1) / 3)
    assert method["unsupported"].successes == 1 and method["unsupported"].trials == 1


def test_paired_comparison_counts_discordant_pairs_for_the_exact_test():
    rows = [
        faulty_row("a", "F1", {"p": True, "q": True}),
        faulty_row("b", "F1", {"p": True, "q": False}),
        faulty_row("c", "F1", {"p": True, "q": False}),
        faulty_row("d", "F1", {"p": False, "q": True}),
        faulty_row("e", "F1", {"p": False, "q": False}),
    ]
    out = agg.paired_top1(rows, "p", "q")
    assert (out["both"], out["only_first"], out["only_second"], out["neither"]) == (1, 2, 1, 1)
    assert 0 < out["p_exact"] <= 1


def test_split_by_groups_in_first_seen_order():
    rows = [clean_row("a", []), clean_row("b", [], arm="langgraph"), clean_row("c", [])]
    groups = agg.split_by(rows, "arm")
    assert list(groups) == ["sdk", "langgraph"] and len(groups["sdk"]) == 2


def test_the_tamper_table_pools_trials_and_false_alarms():
    results = [
        {
            "arm": "sdk",
            "trials": [["audit", "edit", 0, True], ["audit", "edit", 5, False]],
            "false_alarms": [],
            "pristine_checks": 1,
        },
        {
            "arm": "sdk",
            "trials": [["audit", "edit", 0, True]],
            "false_alarms": ["x"],
            "pristine_checks": 1,
        },
    ]
    table = tamper_table(results)
    assert table["cells"]["audit/edit"].successes == 2 and table["cells"]["audit/edit"].trials == 3
    assert table["false_alarms"].successes == 1 and table["false_alarms"].trials == 2


def test_the_report_has_a_section_for_each_arm_family_and_endpoint():
    rows = [
        clean_row("a", ["correct"]),
        faulty_row("b", "F1", {"first-error": True}),
        clean_row("c", ["correct"], arm="langgraph"),
    ]
    tamper = [
        {
            "arm": "sdk",
            "trials": [["audit", "edit", 0, True]],
            "false_alarms": [],
            "pristine_checks": 1,
        }
    ]
    text = render("T", rows, tamper=tamper, header=["header line"])
    for expected in (
        "# T",
        "header line",
        "## Arm: sdk",
        "## Arm: langgraph",
        "### doc: traceability on 1 clean runs",
        "diagnosis on 1 faulty runs",
        "## Tamper detection",
        "audit/edit",
    ):
        assert expected in text, expected


def test_records_of_runs_that_ended_by_a_limit_are_pooled_separately_from_clean_runs():
    failed = {**clean_row("a", [], present=3, complete=False), "fault": {"class": "F8"}}
    failed["status"] = "failed"
    other = {**clean_row("b", [], present=4), "fault": {"class": "F8"}, "status": "failed"}
    rows = [clean_row("c", ["correct"]), failed, other]
    out = agg.failure_completeness(rows)
    assert out["runs"] == 2 and out["calls_complete"].successes == 1
    assert out["model-call"]["completeness"].successes == 7
    assert out["model-call"]["completeness"].trials == 8
    assert agg.failure_completeness([clean_row("c", ["correct"])]) == {"runs": 0}


def test_the_report_names_the_runs_that_exhausted_a_limit():
    failed = {**clean_row("a", [], present=3, complete=False), "fault": {"class": "F8"}}
    failed["status"] = "failed"
    text = render("T", [clean_row("c", ["correct"]), failed])
    assert "records of 1 runs that ended by exhausting a limit" in text


def operator_row(case_id, operator, hit, *, family="doc", arm="sdk", fault_class="F2"):
    row = faulty_row(case_id, fault_class, {"m": hit}, arm=arm)
    row["family"] = family
    row["fault"] = {**row["fault"], "operator": operator}
    return row


def test_results_are_split_into_development_like_test_only_and_held_out_strata():
    rows = (
        [operator_row(f"a{i}", "env_corrupt:digit_swap", i < 9) for i in range(10)]
        + [operator_row(f"b{i}", "env_corrupt:scale10", i < 5) for i in range(10)]
        + [operator_row(f"c{i}", "env_unreadable", i < 2, family="plan") for i in range(10)]
    )
    table = agg.stratum_table(rows, "m")
    assert table["strata"]["development-like"].successes == 9
    assert table["strata"]["test-only operators"].successes == 5
    assert table["strata"]["held-out family"].successes == 2
    assert table["differences"]["held-out family"][0] == pytest.approx(0.2 - 0.9)
    low, high = table["differences"]["test-only operators"][1:]
    assert low < 0.5 - 0.9 < high


def test_a_clean_run_of_the_held_out_family_counts_in_its_own_resolution_stratum():
    rows = [clean_row("a", ["correct"]), {**clean_row("b", ["wrong"]), "family": "plan"}]
    strata = agg.resolution_strata(rows)
    assert strata["development-like"].successes == 1 and strata["held-out family"].successes == 0


def paired(case_id, operator, sdk_ok, lg_ok):
    first = operator_row(case_id, operator, True)
    second = operator_row(case_id, operator, True, arm="langgraph")
    for row, ok in ((first, sdk_ok), (second, lg_ok)):
        row["diagnoses"] = {"ledger-scan+oracle": {**row["diagnoses"]["m"], "top1": ok}}
    return first, second


def test_arms_are_compared_on_the_same_cases_with_exact_tests_and_a_holm_correction():
    rows = []
    for index in range(12):
        sdk, langgraph = paired(f"c{index}", "env_unreadable", True, index >= 8)
        rows += [sdk, langgraph]
    sdk_clean = clean_row("k", ["correct"])
    lg_clean = clean_row("k", ["correct"], arm="langgraph", complete=False)
    comparison = agg.compare_arms([*rows, sdk_clean, lg_clean])
    assert comparison["pairs"] == 13
    ledger = comparison["tests"][
        "generic ledger scan names the decisive step (single-cause faults)"
    ]
    assert (ledger["both"], ledger["only_first"], ledger["only_second"]) == (4, 8, 0)
    assert ledger["p_exact"] == pytest.approx(2 * 0.5**8) and ledger["p_holm"] >= ledger["p_exact"]
    calls = comparison["tests"]["every model call and tool call recorded (clean runs)"]
    assert (calls["only_first"], calls["only_second"]) == (1, 0)


def test_the_report_adds_strata_and_the_paired_comparison():
    rows = [
        *(operator_row(f"a{i}", "env_corrupt:scale10", True) for i in range(4)),
        *(operator_row(f"b{i}", "env_corrupt:digit_swap", True) for i in range(4)),
        clean_row("k", ["correct"]),
        clean_row("k", ["correct"], arm="langgraph"),
    ]
    text = render("T", rows)
    assert "held-out strata" in text and "Paired comparison, sdk against langgraph" in text
