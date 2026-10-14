from nailong_agent_sdk.integrations.jev.models import (
    JevDecisionRequest,
    JevNoulQuestion,
    JevQuestionSpec,
)


def noul_spec(question_id="verdict"):
    return JevQuestionSpec(
        spec_id="spec",
        version="v1",
        questions={question_id: JevNoulQuestion(instructions="Is it ok?")},
    )


def jev_request(spec=None, **overrides):
    values = dict(
        purpose="verification",
        run_id="run-1",
        state={"summary": "ok"},
        question_spec=spec or noul_spec(),
        model="jev-1",
        deadline_seconds=2,
    )
    values.update(overrides)
    return JevDecisionRequest(**values)
