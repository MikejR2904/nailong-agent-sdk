# Copyright (c) 2026 David Michael Indraputra

"""The questions put to Jev. Their wording is part of the pre-registration."""

from __future__ import annotations

from collections.abc import Sequence

from nailong_agent_sdk.integrations.jev.models import (
    JevChoiceQuestion,
    JevNoulQuestion,
    JevQuestionSpec,
)

NONE_CHOICE = "none"
DIAGNOSIS_ID = "trust-bench-diagnosis"
ACCEPTANCE_ID = "trust-bench-acceptance"
ALLOCATION_ID = "trust-bench-allocation"
VERSION = "v1"
QUESTION = "answer"

DIAGNOSIS_INSTRUCTIONS = (
    "An automated agent run ended with the symptom described in 'symptom'. 'events' is the "
    "record of the run, in order: each 'decision' is one model response (its tool calls or its "
    "final answer), each 'result_of' is what a tool returned for a call, each 'write' is a file "
    "the run wrote. Name the decision that is the root cause of the symptom: the earliest "
    "decision whose different outcome would have prevented it, whether it acted wrongly or "
    "acted on a wrong or failed tool result. Choose 'none' if the record does not show one "
    "single responsible decision, for example because a limit cut the run short or because the "
    "record lacks the evidence."
)
ACCEPTANCE_INSTRUCTIONS = (
    "A task, the files it was given and a delivered file are shown. Is the delivered file "
    "exactly what the task asks for, with every number correct for the files given? Answer "
    "true only if you would accept it as correct and complete. Answer false if anything in it "
    "is wrong, missing or malformed."
)
ALLOCATION_INSTRUCTIONS = (
    "A policy says which class of decider an action needs: the weakest class that satisfies "
    "the policy. Read the policy in 'policy' and the request in 'request' and name the class "
    "the request needs. If the request lacks a field the policy needs, the policy's default "
    "applies."
)


def diagnosis_spec(candidates: Sequence[tuple[str, str]]) -> JevQuestionSpec:
    criteria = {identifier: text for identifier, text in candidates}
    criteria[NONE_CHOICE] = "no single decision in this record is responsible"
    return JevQuestionSpec(
        spec_id=DIAGNOSIS_ID,
        version=VERSION,
        questions={
            QUESTION: JevChoiceQuestion(instructions=DIAGNOSIS_INSTRUCTIONS, criteria=criteria)
        },
    )


def acceptance_spec() -> JevQuestionSpec:
    question = JevNoulQuestion(
        instructions=ACCEPTANCE_INSTRUCTIONS,
        criteria={
            True: "the delivered file is correct and complete",
            False: "the delivered file has an error, an omission or is malformed",
        },
    )
    return JevQuestionSpec(spec_id=ACCEPTANCE_ID, version=VERSION, questions={QUESTION: question})


def allocation_spec(classes: dict[str, str]) -> JevQuestionSpec:
    question = JevChoiceQuestion(instructions=ALLOCATION_INSTRUCTIONS, criteria=dict(classes))
    return JevQuestionSpec(spec_id=ALLOCATION_ID, version=VERSION, questions={QUESTION: question})
