import random
import time

import pytest

from nailong_agent_sdk.state.planning import DependencyRule, Plan, PlanTask, PlanValidator
from tests.support.plans import D, K, P, X, proof, task


def test_plan_validator_rules_and_messages():
    validator = PlanValidator()
    producer = task("T1", ["clk"], [("clk", P)])
    consumer = task("T2", ["clk"], [("clk", K)], deps=["T1"])
    good = Plan(
        plan_id="p", tasks=[producer, consumer], dependency_proofs=[proof("T1", "T2", ["clk"])]
    )
    report = validator.validate(good)
    assert report.valid and report.recomputed_dependencies == {"T1": [], "T2": ["T1"]}
    no_proof = validator.validate(Plan(plan_id="p", tasks=[producer, consumer]))
    assert [e.code for e in no_proof.errors] == ["DEPENDENCY_PROOF_MISMATCH"]
    missing_dep = Plan(
        plan_id="p",
        tasks=[producer, task("T2", ["clk"], [("clk", K)])],
        dependency_proofs=[proof("T1", "T2", ["clk"])],
    )
    assert "DEPENDENCY_SET_MISMATCH" in {e.code for e in validator.validate(missing_dep).errors}
    two_producers = Plan(plan_id="p", tasks=[producer, task("T2", ["clk"], [("clk", D)])])
    assert "SIGNAL_OWNERSHIP_AMBIGUITY" in {
        e.code for e in validator.validate(two_producers).errors
    }
    unknown = Plan(plan_id="p", tasks=[task("T1", deps=["ghost"])])
    assert {e.code for e in validator.validate(unknown).errors} >= {"UNKNOWN_TASK_DEPENDENCY"}
    selfdep = Plan(plan_id="p", tasks=[task("T1", deps=["T1"])])
    assert {e.code for e in validator.validate(selfdep).errors} >= {"SELF_DEPENDENCY"}
    undeclared = Plan(plan_id="p", tasks=[task("T1", ["a"], [("zzz", P)])])
    assert {e.code for e in validator.validate(undeclared).errors} >= {
        "SIGNAL_NOT_IN_LOCKED_INTERFACE"
    }
    uncited = Plan(plan_id="p", tasks=[task("T1", ["s"]), task("T2", ["s"], [("s", K)])])
    assert "MISSING_SIGNAL_CITATION" in {e.code for e in validator.validate(uncited).errors}
    orphan_producer = Plan(
        plan_id="p", tasks=[task("T1", ["s"], [("s", P)]), task("T2", ["s"], [("s", X)])]
    )
    assert "MISSING_SIGNAL_CONSUMER" in {e.code for e in validator.validate(orphan_producer).errors}
    fallback = Plan(
        plan_id="p",
        tasks=[
            task("T1", ["s"], [("s", X)], rank=1),
            task("T2", ["s"], [("s", X)], rank=2, deps=["T1"]),
        ],
        dependency_proofs=[proof("T1", "T2", ["s"], DependencyRule.GENERALITY_FALLBACK)],
    )
    assert validator.validate(fallback).valid
    cyc_a = task("A", ["s", "t"], [("s", P), ("t", K)], deps=["B"])
    cyc_b = task("B", ["s", "t"], [("s", K), ("t", P)], deps=["A"])
    cycle = validator.validate(Plan(plan_id="p", tasks=[cyc_a, cyc_b]))
    assert "DEPENDENCY_CYCLE" in {e.code for e in cycle.errors}


def test_assert_valid_message_names_the_offending_tasks_and_signals():
    validator = PlanValidator()
    two_producers = Plan(
        plan_id="p",
        tasks=[task("T1", ["clk"], [("clk", P)]), task("T2", ["clk"], [("clk", D)])],
    )
    with pytest.raises(ValueError) as excinfo:
        validator.assert_valid(two_producers)
    message = str(excinfo.value)
    assert "T1" in message and "T2" in message and "clk" in message, message


def test_plan_report_is_independent_of_task_order():
    rng = random.Random(1)
    base = [
        task("T1", ["a", "b"], [("a", P), ("b", P)]),
        task("T2", ["a"], [("a", K)], deps=["T1"]),
        task("T3", ["b"], [("b", K)], deps=["T1"]),
    ]
    proofs = [proof("T1", "T2", ["a"]), proof("T1", "T3", ["b"])]
    reports = set()
    for _ in range(30):
        tasks = base[:]
        rng.shuffle(tasks)
        reports.add(
            PlanValidator()
            .validate(Plan(plan_id="p", tasks=tasks, dependency_proofs=proofs))
            .model_dump_json()
        )
    assert len(reports) == 1


def test_plan_validator_scales():
    count = 300
    producer = task(
        "T0000", [f"s{i}" for i in range(1, count)], [(f"s{i}", P) for i in range(1, count)]
    )
    tasks = [producer]
    for i in range(1, count):
        tasks.append(task(f"T{i:04d}", [f"s{i}"], [(f"s{i}", K)], deps=["T0000"]))
    proofs = [proof("T0000", f"T{i:04d}", [f"s{i}"]) for i in range(1, count)]
    started = time.monotonic()
    report = PlanValidator().validate(Plan(plan_id="p", tasks=tasks, dependency_proofs=proofs))
    seconds = time.monotonic() - started
    assert report.valid and seconds < 10


def test_signal_sets_are_computed_once_per_task(monkeypatch):
    calls = []
    original = PlanTask.signal_ids

    def counting(self):
        calls.append(self.task_id)
        return original(self)

    monkeypatch.setattr(PlanTask, "signal_ids", counting)
    tasks = [task(f"T{i:04d}", [f"s{i}"]) for i in range(400)]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=tasks))
    assert report.valid and len(calls) == 400


def shared_clock_tasks(count, dependencies_of):
    return [task(f"T{i:03d}", ["clk"], [("clk", K)], deps=dependencies_of(i)) for i in range(count)]


def fallback_proof(parent, child):
    return proof(parent, child, ["clk"], DependencyRule.GENERALITY_FALLBACK)


@pytest.mark.parametrize("count", [10, 20, 40])
def test_a_dependency_chain_orders_tasks_that_share_a_signal(count):
    chain = shared_clock_tasks(count, lambda i: [f"T{i - 1:03d}"] if i else [])
    proofs = [fallback_proof(f"T{i - 1:03d}", f"T{i:03d}") for i in range(1, count)]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=chain, dependency_proofs=proofs))
    assert report.valid, report.errors


@pytest.mark.parametrize("count", [10, 20, 40])
def test_declaring_every_pairwise_ordering_remains_valid(count):
    tasks = shared_clock_tasks(count, lambda i: [f"T{j:03d}" for j in range(i)])
    proofs = [fallback_proof(f"T{j:03d}", f"T{i:03d}") for i in range(count) for j in range(i)]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=tasks, dependency_proofs=proofs))
    assert report.valid, report.errors


def test_a_broken_chain_names_the_task_that_is_not_ordered_after_its_peers():
    tasks = shared_clock_tasks(6, lambda i: [f"T{i - 1:03d}"] if i not in (0, 3) else [])
    proofs = [fallback_proof(f"T{i - 1:03d}", f"T{i:03d}") for i in range(1, 6) if i != 3]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=tasks, dependency_proofs=proofs))
    mismatches = [e for e in report.errors if e.code == "DEPENDENCY_SET_MISMATCH"]
    assert [e.task_ids[0] for e in mismatches] == ["T003", "T004", "T005"], report.errors
    assert "T003" in mismatches[0].message and "T002" in mismatches[0].message


def test_a_dependency_no_shared_signal_derives_is_rejected_and_named():
    tasks = [
        task("T1", ["a"], [("a", K)]),
        task("T2", ["b"], [("b", K)], deps=["T1"]),
    ]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=tasks))
    mismatch = next(e for e in report.errors if e.code == "DEPENDENCY_SET_MISMATCH")
    assert mismatch.task_ids == ["T2", "T1"] and "T1" in mismatch.message


def test_a_proof_is_required_for_every_declared_chain_edge():
    chain = shared_clock_tasks(4, lambda i: [f"T{i - 1:03d}"] if i else [])
    proofs = [fallback_proof("T000", "T001"), fallback_proof("T002", "T003")]
    report = PlanValidator().validate(Plan(plan_id="p", tasks=chain, dependency_proofs=proofs))
    assert [e.code for e in report.errors] == ["DEPENDENCY_PROOF_MISMATCH"]
    assert report.errors[0].task_ids == ["T001", "T002"]
