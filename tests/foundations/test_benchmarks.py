import json

import pytest

from nailong_agent_sdk.foundations.benchmarks import (
    load_pckp_cases,
    run_pckp_benchmark,
    write_pckp_benchmark_report,
)

TRAP = {
    "case_id": "shared-prerequisite-density-trap",
    "description": "A shared-prerequisite joint action beats a higher-density alternative.",
    "problem": {
        "token_budget": 9,
        "items": [
            {"item_id": "root", "token_cost": 2, "utility": 0},
            {"item_id": "left", "token_cost": 3, "utility": 1, "prerequisites": ["root"]},
            {"item_id": "right", "token_cost": 3, "utility": 1, "prerequisites": ["root"]},
            {
                "item_id": "joint",
                "token_cost": 1,
                "utility": 10,
                "prerequisites": ["left", "right"],
            },
            {"item_id": "alternative", "token_cost": 6, "utility": 11},
        ],
    },
}
OVERSIZED = {
    "case_id": "a-mandatory-item-over-budget",
    "description": "The mandatory closure alone exceeds the budget.",
    "problem": {
        "token_budget": 3,
        "items": [{"item_id": "must", "token_cost": 5, "utility": 3, "mandatory": True}],
    },
}


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def test_cases_load_from_a_json_list_and_anything_else_is_refused(tmp_path):
    cases = load_pckp_cases(write_cases(tmp_path, [TRAP, OVERSIZED]))
    assert [case.case_id for case in cases] == [TRAP["case_id"], OVERSIZED["case_id"]]
    with pytest.raises(ValueError, match="case file must be a JSON list"):
        load_pckp_cases(write_cases(tmp_path, {"case_id": "not-a-list"}))


def test_both_solvers_see_the_same_ordered_cases_and_the_gap_is_exact_minus_greedy(tmp_path):
    report = run_pckp_benchmark(load_pckp_cases(write_cases(tmp_path, [TRAP, OVERSIZED])))
    assert [result.case_id for result in report.results] == [
        OVERSIZED["case_id"],
        TRAP["case_id"],
    ]
    oversized, trap = report.results
    assert oversized.exact.status.value == "infeasible-mandatory"
    assert oversized.retained_utility_gap == 0
    assert trap.exact.status.value == "optimal" and trap.exact.utility == 12
    assert trap.greedy.utility == 11 and trap.retained_utility_gap == 1
    assert trap.problem_hash == trap.exact.problem_hash == trap.greedy.problem_hash
    assert trap.exact_elapsed_ns >= 0 and trap.greedy_elapsed_ns >= 0


def test_the_report_is_written_as_canonical_json_and_can_be_read_back(tmp_path):
    report = run_pckp_benchmark(load_pckp_cases(write_cases(tmp_path, [TRAP])))
    target = tmp_path / "results" / "report.json"
    write_pckp_benchmark_report(report, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["schema_version"] == "pckp-benchmark-v1"
    assert written["results"][0]["case_id"] == TRAP["case_id"]
    assert list(written) == sorted(written)
    assert not [path for path in target.parent.iterdir() if path.name.endswith(".tmp")]
