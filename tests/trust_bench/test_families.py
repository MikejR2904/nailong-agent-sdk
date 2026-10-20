import json

import pytest
from trust_bench.cases import FaultSpec
from trust_bench.evaluate import FAMILIES
from trust_bench.families import data as data_family
from trust_bench.families import doc as doc_family
from trust_bench.recorder import WorkspaceWatcher
from trust_bench.splits import DEV_OPERATORS, TEST_ONLY_OPERATORS

from tests.support.trust_runs import ALL_OPERATORS, evaluated, fault_ids

SEEDS = (1, 2, 3, 4)
FAMILY_NAMES = tuple(FAMILIES)


def materialized(case, tmp_path, fault=None):
    workspace = tmp_path / "ws"
    case.materialize(workspace, fault)
    return WorkspaceWatcher(workspace).snapshot()


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_a_seed_always_builds_the_same_case(family, tmp_path):
    first, second = FAMILIES[family](7), FAMILIES[family](7)
    assert first.case_id == second.case_id and first.task_text == second.task_text
    assert materialized(first, tmp_path / "a") == materialized(second, tmp_path / "b")


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_different_seeds_build_different_inputs(family, tmp_path):
    assert materialized(FAMILIES[family](1), tmp_path / "a") != materialized(
        FAMILIES[family](2), tmp_path / "b"
    )


def test_one_seed_in_three_builds_the_large_doc_case():
    assert doc_family.generate(3).scale == "large" and doc_family.generate(1).scale == "small"
    large = doc_family.generate(3)
    assert len(large.records) == doc_family.LARGE_RECORDS
    assert all(len(item.memo) == doc_family.MEMO_CHARS for item in large.records)


def test_no_generated_amount_is_a_palindrome_so_a_digit_swap_always_changes_it():
    for seed in range(1, 40):
        for item in doc_family.generate(seed).records:
            assert str(item.amount) != str(item.amount)[::-1]
        for row in data_family.generate(seed).rows:
            assert str(row.units) != str(row.units)[::-1]


@pytest.mark.parametrize("family", FAMILY_NAMES)
@pytest.mark.parametrize("seed", SEEDS)
def test_operator_ids_are_unique_within_a_case(family, seed):
    ids = fault_ids(family, seed)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_dev_cases_never_offer_the_test_only_operators(family):
    offered = {f.operator_id for f in FAMILIES[family](1).faults(DEV_OPERATORS)}
    assert offered <= DEV_OPERATORS and not offered & TEST_ONLY_OPERATORS
    full = {f.operator_id for f in FAMILIES[family](1).faults(ALL_OPERATORS)}
    assert full & TEST_ONLY_OPERATORS


def test_fault_labels_name_class_operator_and_target():
    fault = FaultSpec("F2", "env_corrupt", "rec-004", "digit_swap")
    assert fault.operator_id == "env_corrupt:digit_swap"
    assert fault.label == "F2/env_corrupt:digit_swap@rec-004"
    assert FaultSpec("F8", "limit").label == "F8/limit"


def test_unknown_corruption_variants_are_refused_with_the_known_ones_named():
    with pytest.raises(ValueError, match='unknown env_corrupt variant "bogus"'):
        doc_family.corrupted_amount(10, "bogus")
    with pytest.raises(ValueError, match='unknown env_corrupt variant "bogus"'):
        data_family.corrupted_units(10, "bogus")


def test_the_doc_oracle_accepts_the_expected_report_and_rejects_damage():
    case = doc_family.generate(2)
    good = json.dumps(case.expected()).encode()
    assert not case.is_defective(doc_family.REPORT, good)
    damaged = case.expected()
    vendor = case.requested[0]
    damaged["vendors"][vendor]["total"] += 1
    assert case.is_defective(doc_family.REPORT, json.dumps(damaged).encode())
    assert case.is_defective(doc_family.REPORT, b"\xff\xfe")
    assert not case.is_defective("other.json", b"anything")


def test_the_data_oracle_accepts_the_expected_artifacts_and_rejects_damage():
    case = data_family.generate(2)
    for path, text in case.expected().items():
        assert not case.is_defective(path, text.encode())
        assert case.is_defective(path, (text + " ").encode()) == (path == data_family.CLEAN)
    assert case.is_defective(data_family.SUMMARY, b"{}")
    assert case.is_defective(data_family.REVENUE, b"\xff")
    assert not case.is_defective("unrelated.txt", b"x")


def test_the_result_oracle_flags_a_read_that_differs_from_the_true_record():
    case = doc_family.generate(2)
    item = case.records[0]
    path = f"inputs/{item.id}.json"

    def envelope(text):
        return {"status": "succeeded", "output": {"lines": [{"line": 1, "text": text}]}}

    assert not case.is_defective_result("read_file", {"path": path}, envelope(item.text()))
    assert case.is_defective_result("read_file", {"path": path}, envelope(item.text(999)))
    assert case.is_defective_result("read_file", {"path": path}, {"status": "failed"})
    assert not case.is_defective_result("glob", {"pattern": "*"}, {"status": "succeeded"})


@pytest.mark.parametrize("family", FAMILY_NAMES)
@pytest.mark.parametrize("seed", SEEDS)
def test_a_clean_run_delivers_a_correct_artifact_and_a_full_trace(family, seed):
    result = evaluated(family, seed, "langgraph")
    assert result["status"] == "completed" and result["symptom"] == "none"
    assert result["trace"]["calls_complete"]
    assert all(a["outcome"] == "correct" for a in result["trace"]["artifacts"])


@pytest.mark.parametrize("family", FAMILY_NAMES)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_fault_is_caught_by_the_oracle_and_the_declared_step_is_the_first_defect(
    family, seed
):
    for operator_id in fault_ids(family, seed):
        result = evaluated(family, seed, "langgraph", operator_id)
        fault_class = result["fault"]["class"]
        if fault_class == "F8":
            assert result["status"] == "failed" and result["truth_step"] is None
            continue
        assert result["status"] == "completed", operator_id
        assert result["symptom"] == "artifact-defective", operator_id
        scan = result["diagnoses"]["ledger-scan+oracle"]
        assert scan["top1"], f"{operator_id}: declared {result['truth_step']}, scan {scan['steps']}"


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_a_limit_fault_ends_the_run_with_no_single_decision_to_blame(family):
    result = evaluated(family, 1, "langgraph", "limit")
    scan = result["diagnoses"]["ledger-scan+oracle"]
    assert result["status"] != "completed" and not scan["has_truth"]
