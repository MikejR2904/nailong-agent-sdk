# Copyright (c) 2026 David Michael Indraputra

"""Use Jev only to prioritize an already bounded, host-approved candidate set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import Field

from ...foundations.contracts import StrictModel
from ...foundations.identifiers import require_unique
from ..contracts import InteropFailureMode, InteropOperationStatus
from .models import (
    _JEV_ANSWER_ADAPTER,
    MAX_CHOICE_CRITERIA,
    JevAnswer,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionProvider,
    JevDecisionRequest,
    JevDecisionResult,
    JevQuestionSpec,
)


class ExplorationCandidate(StrictModel):
    """A redacted, already-authorized candidate for optional exploration prioritization."""

    candidate_id: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=2_000)
    deterministic_rank: int = Field(ge=0)
    provenance_ref: str = Field(min_length=1)


class JevExplorationAdvice(StrictModel):
    """A non-binding triage result; it cannot create graph nodes or mutate state."""

    selected_candidate_id: str | None = None
    used_deterministic_fallback: bool
    result: JevDecisionResult


class JevExplorationAdvisor:
    """Use Jev only to prioritize an already bounded, host-approved candidate set."""

    def __init__(
        self,
        evaluator: JevDecisionProvider,
        *,
        model: str,
        max_candidates: int = 64,
        on_unavailable: InteropFailureMode = InteropFailureMode.FALLBACK_DETERMINISTIC,
    ) -> None:
        if not 2 <= max_candidates <= MAX_CHOICE_CRITERIA:
            raise ValueError(
                f"max_candidates {max_candidates} must be between 2 and {MAX_CHOICE_CRITERIA}, "
                "the number of choices a Jev question can carry."
            )
        self._evaluator = evaluator
        self._model = model
        self._max_candidates = max_candidates
        self._on_unavailable = on_unavailable

    async def prioritize(
        self,
        run_id: str,
        candidates: Sequence[ExplorationCandidate],
        *,
        instructions: str,
        deadline_seconds: float = 20,
    ) -> JevExplorationAdvice:
        if len(candidates) < 2:
            raise ValueError(
                f"Jev exploration prioritisation needs at least two candidates, got "
                f"{len(candidates)}; with fewer there is nothing to prioritise, so select the "
                "only candidate directly."
            )
        if len(candidates) > self._max_candidates:
            raise ValueError(
                f"Exploration candidate count {len(candidates)} exceeds the declared Jev bound "
                f"of {self._max_candidates}."
            )
        identifiers = [candidate.candidate_id for candidate in candidates]
        require_unique(identifiers, "Exploration candidate ids")
        ordered = sorted(
            candidates,
            key=lambda candidate: (candidate.deterministic_rank, candidate.candidate_id),
        )
        spec = JevQuestionSpec(
            spec_id="agent-sdk-exploration-priority",
            version="v1",
            questions={
                "priority": JevChoiceQuestion(
                    instructions=instructions,
                    criteria={candidate.candidate_id: candidate.summary for candidate in ordered},
                )
            },
        )
        state = {
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "summary": candidate.summary,
                    "provenance_ref": candidate.provenance_ref,
                }
                for candidate in ordered
            ]
        }
        request = JevDecisionRequest(
            purpose="exploration",
            run_id=run_id,
            state=state,
            question_spec=spec,
            model=self._model,
            deadline_seconds=deadline_seconds,
        )
        result = await self._evaluator.evaluate(request)
        answer = (
            result.answers.get("priority")
            if result.status is InteropOperationStatus.SUCCEEDED
            else None
        )
        if isinstance(answer, JevChoiceAnswer):
            parsed_choice: JevAnswer | None = answer
        elif isinstance(answer, Mapping):
            parsed_choice = _JEV_ANSWER_ADAPTER.validate_python(answer)
        else:
            parsed_choice = None
        if parsed_choice is not None:
            if isinstance(parsed_choice, JevChoiceAnswer) and parsed_choice.choice in {
                item.candidate_id for item in ordered
            }:
                return JevExplorationAdvice(
                    selected_candidate_id=parsed_choice.choice,
                    used_deterministic_fallback=False,
                    result=result,
                )
        if self._on_unavailable is InteropFailureMode.REJECT:
            return JevExplorationAdvice(
                selected_candidate_id=None,
                used_deterministic_fallback=False,
                result=result,
            )
        return JevExplorationAdvice(
            selected_candidate_id=ordered[0].candidate_id,
            used_deterministic_fallback=True,
            result=result,
        )
