import asyncio
import sys
import time

import pytest

from nailong_agent_sdk.integrations._utils import OptionalDependencyError, require_optional_module
from nailong_agent_sdk.integrations.contracts import InteropOperationStatus
from nailong_agent_sdk.integrations.jev.decision import TypeSafeJevDecisionEvaluator
from nailong_agent_sdk.integrations.jev.models import JevChoiceQuestion, JevQuestionSpec
from nailong_agent_sdk.integrations.jev.receipts import InMemoryJevReceiptStore
from tests.support.agents import arun
from tests.support.jev_cases import jev_request
from tests.support.paths import STUB_ROOT


@pytest.fixture(autouse=True)
def typesafe_stub(monkeypatch):
    monkeypatch.syspath_prepend(str(STUB_ROOT))


class FakeClient:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    async def system_one(self, *, state, questions, model):
        self.calls += 1
        result = self.behavior(self.calls, state, questions, model)
        if asyncio.iscoroutine(result):
            result = await result
        return result


def choice_spec():
    return JevQuestionSpec(
        spec_id="route",
        version="v1",
        questions={
            "architecture": JevChoiceQuestion(
                instructions="pick", criteria={"single-agent": "one", "multi-agent": "many"}
            )
        },
    )


def evaluator(behavior, sink=None, **kwargs):
    client = FakeClient(behavior)
    return TypeSafeJevDecisionEvaluator(
        receipt_sink=sink or InMemoryJevReceiptStore(), client_factory=lambda: client, **kwargs
    ), client


def test_jev_evaluator_normalizes_answers_and_records_digest_only_receipts():
    sink = InMemoryJevReceiptStore()
    good, _ = evaluator(
        lambda n, s, q, m: {
            "model": m,
            "request_id": "req-1",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "answers": {"verdict": {"type": "noul", "noul": 0.9}},
        },
        sink,
    )
    request = jev_request()
    result = arun(good.evaluate(request))
    assert (
        result.status is InteropOperationStatus.SUCCEEDED and result.answers["verdict"].noul == 0.9
    )
    assert (
        result.state_digest == request.state_digest
        and result.response_digest
        and result.input_tokens == 5
    )
    receipt = sink.receipts[0]
    blob = receipt.model_dump_json()
    assert (
        "Is it ok?" not in blob
        and "summary" not in blob
        and receipt.projection_digest == request.state_digest
    )
    grouped, _ = evaluator(
        lambda n, s, q, m: {"nouls": {"verdict": {"noul": 0.4}}, "model": m}, sink
    )
    assert arun(grouped.evaluate(request)).answers["verdict"].noul == 0.4


def test_jev_evaluator_failure_modes_become_unavailable_with_reasons():
    sink = InMemoryJevReceiptStore()
    cases = {
        "wrong ids": (
            lambda n, s, q, m: {"answers": {"other": {"type": "noul", "noul": 0.5}}},
            "answered question IDs",
        ),
        "wrong type": (
            lambda n, s, q, m: {
                "answers": {
                    "verdict": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 1.0},
                        "confidence": 0.5,
                    }
                }
            },
            "registered question type",
        ),
        "out of range": (
            lambda n, s, q, m: {"answers": {"verdict": {"type": "noul", "noul": 7}}},
            "typed answer contract",
        ),
        "not mapping": (lambda n, s, q, m: 42, "not mapping-compatible"),
        "raises": (
            lambda n, s, q, m: (_ for _ in ()).throw(RuntimeError("secret connection string")),
            "provider-runtimeerror",
        ),
    }
    for name, (behavior, fragment) in cases.items():
        subject, _ = evaluator(behavior, sink)
        result = arun(subject.evaluate(jev_request()))
        assert result.status is InteropOperationStatus.UNAVAILABLE, name
        assert fragment in result.unavailable_reason, (name, result.unavailable_reason)
        assert "secret connection string" not in result.unavailable_reason

    async def slow(n, s, q, m):
        await asyncio.sleep(5)

    subject, _ = evaluator(slow, sink)
    started = time.monotonic()
    result = arun(subject.evaluate(jev_request(deadline_seconds=0.5)))
    assert (
        result.status is InteropOperationStatus.UNAVAILABLE
        and "no response within the request deadline" in result.unavailable_reason
    )
    assert time.monotonic() - started < 3
    flaky, client = evaluator(
        lambda n, s, q, m: (
            (_ for _ in ()).throw(RuntimeError("x"))
            if n < 3
            else {"answers": {"verdict": {"type": "noul", "noul": 0.7}}}
        )
    )
    recovered = arun(flaky.evaluate(jev_request(max_attempts=3)))
    assert (
        recovered.status is InteropOperationStatus.SUCCEEDED
        and recovered.retry_count == 2
        and client.calls == 3
    )
    choice = evaluator(
        lambda n, s, q, m: {
            "answers": {
                "architecture": {
                    "type": "choice",
                    "choice": "bogus",
                    "probabilities": {"single-agent": 0.5, "multi-agent": 0.5},
                    "confidence": 0.9,
                }
            }
        },
        sink,
    )[0]
    assert (
        "does not match the registered options"
        in arun(choice.evaluate(jev_request(choice_spec()))).unavailable_reason
    )
    assert len(sink.receipts) == 7 and all(len(r.projection_digest) == 64 for r in sink.receipts)


def test_jev_optional_dependency_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    with pytest.raises(OptionalDependencyError, match=r"nailong-agent-sdk\[jev\]"):
        require_optional_module("typesafe_sdk", "jev")
    subject = TypeSafeJevDecisionEvaluator(receipt_sink=InMemoryJevReceiptStore())
    result = arun(subject.evaluate(jev_request()))
    assert (
        result.status is InteropOperationStatus.UNAVAILABLE
        and "nailong-agent-sdk[jev]" in result.unavailable_reason
    )


def test_claim_8_option_label_named_approval_does_not_make_evaluate_raise():
    spec = JevQuestionSpec(
        spec_id="labels",
        version="v1",
        questions={
            "route": JevChoiceQuestion(
                instructions="pick",
                criteria={"approval": "needs a human", "token": "plain", "message": "text"},
            )
        },
    )
    good, _ = evaluator(
        lambda n, s, q, m: {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "approval",
                    "probabilities": {"approval": 0.6, "token": 0.2, "message": 0.2},
                    "confidence": 0.7,
                }
            }
        }
    )
    result = arun(good.evaluate(jev_request(spec)))
    assert (
        result.status is InteropOperationStatus.SUCCEEDED
        and result.answers["route"].choice == "approval"
    )
