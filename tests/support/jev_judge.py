from collections.abc import Callable
from typing import Any

from nailong_agent_sdk.integrations.contracts import InteropOperationStatus
from nailong_agent_sdk.integrations.jev.models import (
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionRequest,
    JevDecisionResult,
    JevNoulAnswer,
)

PINNED = "jev-1.13.0"


def choice_answer(question: JevChoiceQuestion, picked: str, weight: float = 0.8) -> JevChoiceAnswer:
    others = [name for name in question.criteria if name != picked]
    rest = (1 - weight) / len(others) if others else 0.0
    probabilities = {picked: weight, **{name: rest for name in others}}
    return JevChoiceAnswer(choice=picked, probabilities=probabilities, confidence=weight)


class ScriptedProvider:
    """A stand-in for the Jev evaluator: it answers from a function of the request."""

    def __init__(
        self,
        choose: Callable[[JevDecisionRequest], str] | None = None,
        noul: float = 0.9,
        *,
        model: str = PINNED,
        tokens: int = 100,
        fail: Callable[[JevDecisionRequest], bool] | None = None,
    ) -> None:
        self.choose = choose
        self.noul = noul
        self.model = model
        self.tokens = tokens
        self.fail = fail
        self.requests: list[JevDecisionRequest] = []

    async def evaluate(self, request: JevDecisionRequest) -> JevDecisionResult:
        self.requests.append(request)
        if self.fail is not None and self.fail(request):
            return JevDecisionResult.unavailable(request, "provider-down", duration_ms=1.0)
        answers: dict[str, Any] = {}
        for identifier, question in request.question_spec.questions.items():
            if isinstance(question, JevChoiceQuestion):
                picked = self.choose(request) if self.choose else next(iter(question.criteria))
                answers[identifier] = choice_answer(question, picked)
            else:
                answers[identifier] = JevNoulAnswer(noul=self.noul)
        return JevDecisionResult(
            status=InteropOperationStatus.SUCCEEDED,
            model=self.model,
            answers=answers,
            state_digest=request.state_digest,
            question_spec_digest=request.question_spec.digest,
            input_tokens=self.tokens,
            output_tokens=0,
        )
