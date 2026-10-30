import json

import pytest
from trust_bench.cli import main
from trust_bench.evaluate import ARMS, FAMILIES
from trust_bench.jobs import enumerate_jobs, parse_seeds, read_results, write_results
from trust_bench.splits import (
    DEV_OPERATORS,
    DEV_SEEDS,
    HELD_OUT_FAMILIES,
    TEST_ONLY_OPERATORS,
    TEST_SEEDS,
    Split,
    families_for,
    operators_for,
    seeds_for,
)


def test_the_two_splits_share_no_seed_and_no_operator():
    assert not set(DEV_SEEDS) & set(TEST_SEEDS)
    assert not DEV_OPERATORS & TEST_ONLY_OPERATORS
    assert operators_for(Split.DEV) == DEV_OPERATORS
    assert operators_for(Split.TEST) == DEV_OPERATORS | TEST_ONLY_OPERATORS
    assert seeds_for(Split.DEV) is DEV_SEEDS and seeds_for(Split.TEST) is TEST_SEEDS


def test_a_held_out_family_is_unavailable_in_dev_and_available_in_test():
    available = frozenset({"doc", "data", "plan"})
    assert families_for(Split.DEV, available) == {"doc", "data"}
    assert families_for(Split.TEST, available) == available
    assert HELD_OUT_FAMILIES == {"plan"}


def test_seed_lists_and_ranges_are_parsed_and_checked_against_the_split():
    assert parse_seeds("1-3,10, 12", Split.DEV) == [1, 2, 3, 10, 12]
    with pytest.raises(ValueError, match=r"seeds \[1000000\] are outside the dev range \[1, 999\]"):
        parse_seeds("1,1000000", Split.DEV)
    with pytest.raises(ValueError, match="outside the test range"):
        parse_seeds("5", Split.TEST)


def test_the_dev_job_list_has_one_clean_run_and_one_run_per_dev_fault_per_arm():
    jobs = enumerate_jobs(Split.DEV, ["doc"], ARMS, [1])
    case = FAMILIES["doc"](1)
    per_arm = 1 + len(case.faults(DEV_OPERATORS))
    assert len(jobs) == per_arm * len(ARMS)
    assert {job[3] for job in jobs} - {None} <= DEV_OPERATORS
    assert all(job[4] == "dev" for job in jobs)


def test_the_test_job_list_adds_the_operators_the_dev_split_never_had():
    dev = {job[3] for job in enumerate_jobs(Split.DEV, ["data"], ["sdk"], [1])}
    test = {job[3] for job in enumerate_jobs(Split.TEST, ["data"], ["sdk"], [1_900_000])}
    assert test - dev >= {"env_corrupt:scale10", "decision_wrong_stage:revenue"}


def test_an_unknown_family_or_arm_is_refused_with_the_known_ones_named():
    with pytest.raises(ValueError, match='family "plan" is not available in the dev split'):
        enumerate_jobs(Split.DEV, ["plan"], ["sdk"], [1])
    with pytest.raises(ValueError, match='unknown arm "autogen"; known: sdk, langgraph'):
        enumerate_jobs(Split.DEV, ["doc"], ["autogen"], [1])


def test_results_round_trip_through_json_lines(tmp_path):
    rows = [{"a": 1, "b": [1, 2]}, {"a": 2, "b": []}]
    path = tmp_path / "nested" / "results.jsonl"
    assert write_results(iter(rows), path) == 2
    assert read_results(path) == rows


def test_the_gates_command_succeeds_when_every_gate_passes(capsys):
    assert main(["gates"]) == 0
    assert capsys.readouterr().out.count("PASS") == 2


def test_run_and_report_produce_the_endpoints_for_a_small_dev_slice(tmp_path, capsys):
    results = tmp_path / "results.jsonl"
    report = tmp_path / "report.md"
    args = ["--split", "dev", "--families", "data", "--seeds", "1", "--arms", "langgraph"]
    assert main(["run", *args, "--workers", "1", "--out", str(results)]) == 0
    rows = read_results(results)
    assert len(rows) == 1 + len(FAMILIES["data"](1).faults(DEV_OPERATORS))
    assert main(["report", "--results", str(results), "--out", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "## Reproduction gates" in text and "## Arm: langgraph" in text
    assert "ledger-scan+oracle" in text
    assert "written to" in capsys.readouterr().out


def test_the_run_command_refuses_a_test_seed_in_the_dev_split(tmp_path):
    with pytest.raises(ValueError, match="outside the dev range"):
        main(["run", "--split", "dev", "--seeds", "1000000", "--out", str(tmp_path / "x.jsonl")])


def test_result_rows_are_plain_json(tmp_path):
    results = tmp_path / "r.jsonl"
    main(
        [
            "run",
            "--split",
            "dev",
            "--families",
            "doc",
            "--seeds",
            "1",
            "--arms",
            "langgraph",
            "--workers",
            "1",
            "--out",
            str(results),
        ]
    )
    for line in results.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        assert {"case_id", "family", "arm", "fault", "status", "trace", "diagnoses"} <= set(row)


def test_the_commit_in_a_report_header_says_when_the_tree_is_not_committed(monkeypatch):
    from trust_bench import cli

    answers = {("rev-parse", "--short", "HEAD"): "abc1234", ("status", "--porcelain"): " M file"}
    monkeypatch.setattr(cli, "_git", lambda *args: answers[args])
    assert cli._commit() == "abc1234+uncommitted"
    answers[("status", "--porcelain")] = ""
    assert cli._commit() == "abc1234"
