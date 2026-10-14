import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.verification import VerificationContext
from nailong_agent_sdk.integrations.contracts import InteropFailureMode, InteropOperationStatus
from nailong_agent_sdk.integrations.jev.advisory import (
    JevAdvisoryPolicy,
    JevAdvisoryVerificationGate,
)
from nailong_agent_sdk.integrations.jev.architecture import (
    JevArchitectureRouter,
    JevArchitectureRoutingPolicy,
)
from nailong_agent_sdk.integrations.jev.exploration import (
    ExplorationCandidate,
    JevExplorationAdvisor,
)
from nailong_agent_sdk.integrations.jev.models import JevDecisionResult, JevNoulAnswer
from tests.support.agents import arun, definition, task
from tests.support.jev_cases import jev_request
from tests.support.paths import STUB_ROOT


@pytest.fixture(autouse=True)
def typesafe_stub(monkeypatch):
    monkeypatch.syspath_prepend(str(STUB_ROOT))


class StubEvaluator:
    def __init__(self, result):
        self.result = result
        self.requests = []

    async def evaluate(self, request):
        self.requests.append(request)
        return self.result


def jev_result(answers=None, status=InteropOperationStatus.SUCCEEDED, reason=None):
    return JevDecisionResult(
        status=status,
        answers=answers or {},
        state_digest="a" * 64,
        question_spec_digest="b" * 64,
        unavailable_reason=reason,
    )


def test_advisory_gate_never_accepts_what_the_local_gate_rejected():
    def local_fail(context):
        return (False, "local says no")

    evaluator_ = StubEvaluator(jev_result({"verdict": JevNoulAnswer(noul=0.99)}))
    gate = JevAdvisoryVerificationGate(
        type("G", (), {"verify": staticmethod(local_fail)})(),
        evaluator_,
        lambda c: jev_request(),
        JevAdvisoryPolicy(question_id="verdict", minimum_noul=0.5),
    )
    context = VerificationContext(output={}, definition=definition(), task=task())
    decision = arun(gate.verify(context))
    assert (
        decision.passed is False
        and decision.reason == "local says no"
        and evaluator_.requests == []
    )


@pytest.mark.parametrize(
    "answers, status, mode, expected",
    [
        (
            {"verdict": JevNoulAnswer(noul=0.5)},
            InteropOperationStatus.SUCCEEDED,
            InteropFailureMode.ESCALATE,
            True,
        ),
        (
            {"verdict": JevNoulAnswer(noul=0.4999)},
            InteropOperationStatus.SUCCEEDED,
            InteropFailureMode.ESCALATE,
            False,
        ),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.ESCALATE, False),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.REJECT, False),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.FALLBACK_DETERMINISTIC, True),
        ({}, InteropOperationStatus.SUCCEEDED, InteropFailureMode.REJECT, False),
    ],
)
def test_advisory_gate_policy_matrix(answers, status, mode, expected):
    evaluator_ = StubEvaluator(
        jev_result(
            answers,
            status,
            "provider down" if status is not InteropOperationStatus.SUCCEEDED else None,
        )
    )
    gate = JevAdvisoryVerificationGate(
        type("G", (), {"verify": staticmethod(lambda c: True)})(),
        evaluator_,
        lambda c: jev_request(),
        JevAdvisoryPolicy(question_id="verdict", minimum_noul=0.5, on_unavailable=mode),
    )
    decision = arun(
        gate.verify(VerificationContext(output={}, definition=definition(), task=task()))
    )
    assert decision.passed is expected, decision


def test_architecture_router_is_monotonic_and_honors_failure_modes():
    from nailong_agent_sdk.integrations.jev.models import JevChoiceAnswer

    def answer(choice, confidence):
        return JevChoiceAnswer(
            choice=choice,
            probabilities={"single-agent": 1 - confidence, "multi-agent": confidence},
            confidence=confidence,
        )

    policy = JevArchitectureRoutingPolicy(model="m", minimum_confidence=0.8)
    for evaluator_result, expected in (
        (jev_result({"architecture": answer("multi-agent", 0.8)}), "multi-agent"),
        (jev_result({"architecture": answer("multi-agent", 0.79)}), "single-agent"),
        (jev_result({"architecture": answer("single-agent", 0.99)}), "single-agent"),
        (jev_result({}, InteropOperationStatus.UNAVAILABLE, "down"), "single-agent"),
        (jev_result({"architecture": JevNoulAnswer(noul=0.9)}), "single-agent"),
    ):
        router = JevArchitectureRouter(StubEvaluator(evaluator_result), policy)
        advice = arun(
            router.advise(
                run_id="r", deterministic_architecture="single-agent", state={"summary": "x"}
            )
        )
        assert advice.architecture == expected
    never_called = StubEvaluator(jev_result({}))
    lifted = arun(
        JevArchitectureRouter(never_called, policy).advise(
            run_id="r", deterministic_architecture="multi-agent", state={}
        )
    )
    assert lifted.architecture == "multi-agent" and never_called.requests == []
    rejecting = JevArchitectureRouter(
        StubEvaluator(jev_result({}, InteropOperationStatus.UNAVAILABLE, "down")),
        JevArchitectureRoutingPolicy(model="m", on_unavailable=InteropFailureMode.REJECT),
    )
    with pytest.raises(RuntimeError, match="down"):
        arun(rejecting.advise(run_id="r", deterministic_architecture="single-agent", state={}))
    with pytest.raises(ValidationError, match="on_unavailable") as excinfo:
        JevArchitectureRoutingPolicy(model="m", on_unavailable=InteropFailureMode.ESCALATE)
    assert "has no escalation channel" in str(excinfo.value)


def test_exploration_advisor_bounds_and_selection():
    from nailong_agent_sdk.integrations.jev.models import JevChoiceAnswer

    candidates = [
        ExplorationCandidate(
            candidate_id=f"c{i}", summary=f"s{i}", deterministic_rank=i, provenance_ref="p"
        )
        for i in range(4)
    ]

    def pick(choice):
        return JevChoiceAnswer(
            choice=choice, probabilities={f"c{i}": 0.25 for i in range(4)}, confidence=0.5
        )

    advisor = JevExplorationAdvisor(
        StubEvaluator(jev_result({"priority": pick("c2")})), model="m", max_candidates=4
    )
    assert arun(advisor.prioritize("r", candidates, instructions="i")).selected_candidate_id == "c2"
    invalid = JevExplorationAdvisor(StubEvaluator(jev_result({"priority": pick("zzz")})), model="m")
    fallback = arun(invalid.prioritize("r", candidates, instructions="i"))
    assert fallback.selected_candidate_id == "c0" and fallback.used_deterministic_fallback
    rejecting = JevExplorationAdvisor(
        StubEvaluator(jev_result({}, InteropOperationStatus.UNAVAILABLE, "down")),
        model="m",
        on_unavailable=InteropFailureMode.REJECT,
    )
    assert (
        arun(rejecting.prioritize("r", candidates, instructions="i")).selected_candidate_id is None
    )
    with pytest.raises(ValueError, match="at least two candidates"):
        arun(advisor.prioritize("r", candidates[:1], instructions="i"))
    with pytest.raises(ValueError, match="exceeds the declared Jev bound"):
        arun(
            JevExplorationAdvisor(
                StubEvaluator(jev_result({})), model="m", max_candidates=2
            ).prioritize("r", candidates, instructions="i")
        )
    many = [
        ExplorationCandidate(
            candidate_id=f"c{i}", summary=f"s{i}", deterministic_rank=i, provenance_ref="p"
        )
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="max_candidates 300 must be between 2 and 255") as bound:
        JevExplorationAdvisor(StubEvaluator(jev_result({})), model="m", max_candidates=300)
    assert type(bound.value) is ValueError
    full = JevExplorationAdvisor(
        StubEvaluator(jev_result({"priority": pick("c2")})), model="m", max_candidates=255
    )
    assert arun(full.prioritize("r", many[:255], instructions="i")).selected_candidate_id == "c2"
    twin = [*candidates[:3], candidates[0]]
    with pytest.raises(ValueError, match=r'candidate ids must be unique; repeated: "c0"\.'):
        arun(advisor.prioritize("r", twin, instructions="i"))
