# Copyright (c) 2026 David Michael Indraputra

"""Concrete optional Jev evaluator using ``typesafe-sdk`` only when explicitly selected.

Primary interfaces: https://docs.typesafe.ai/api and
https://docs.typesafe.ai/concepts/system-one
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from .._utils import OptionalDependencyError, content_digest, require_optional_module
from ..contracts import InteropOperationStatus
from .models import (
    _JEV_ANSWER_ADAPTER,
    JevAnswer,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionReceipt,
    JevDecisionRequest,
    JevDecisionResult,
    JevNoulQuestion,
    JevQuestionKind,
    JevQuestionSpec,
)
from .receipts import JevReceiptSink


class JevResponseError(ValueError):
    pass


class TypeSafeJevDecisionEvaluator:
    """Concrete optional provider using ``typesafe-sdk`` only when explicitly selected.

    This class does not infer credentials or perform an evaluation during construction.
    Its caller controls a pinned model identifier, redacted state, and fixed question spec.
    """

    def __init__(
        self,
        *,
        receipt_sink: JevReceiptSink,
        policy_version: str = "jev-advisory-v1",
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._receipt_sink = receipt_sink
        self._policy_version = policy_version
        self._client_factory = client_factory

    async def evaluate(self, request: JevDecisionRequest) -> JevDecisionResult:
        started = time.perf_counter()
        result: JevDecisionResult
        try:
            result = await self._evaluate_with_bounded_attempts(request)
            result = result.model_copy(update={"duration_ms": _duration_ms(started)})
        except Exception as error:
            result = JevDecisionResult.unavailable(
                request,
                _safe_provider_error(error),
                duration_ms=_duration_ms(started),
            )
        await self._record_receipt(request, result, policy_outcome=result.status.value)
        return result

    async def _evaluate_with_bounded_attempts(
        self,
        request: JevDecisionRequest,
    ) -> JevDecisionResult:
        last_error: Exception | None = None
        for attempt in range(request.max_attempts):
            try:
                raw_response = await asyncio.wait_for(
                    self._request_once(request), timeout=request.deadline_seconds
                )
                return _normalize_jev_response(
                    raw_response,
                    request,
                    duration_ms=0.0,
                    retry_count=attempt,
                )
            except Exception as error:
                last_error = error
                if attempt + 1 >= request.max_attempts:
                    break
        assert last_error is not None
        raise last_error

    async def _request_once(self, request: JevDecisionRequest) -> Any:
        sdk = require_optional_module("typesafe_sdk", "jev")
        client_factory = self._client_factory or getattr(sdk, "AsyncTypeSafeClient")
        client = client_factory()
        questions = _make_typesafe_questions(sdk, request.question_spec)
        if hasattr(client, "__aenter__"):
            async with client as managed_client:
                return await managed_client.system_one(
                    state=request.state,
                    questions=questions,
                    model=request.model,
                )
        return await client.system_one(
            state=request.state,
            questions=questions,
            model=request.model,
        )

    async def _record_receipt(
        self,
        request: JevDecisionRequest,
        result: JevDecisionResult,
        *,
        policy_outcome: str,
    ) -> None:
        receipt = JevDecisionReceipt(
            provider="typesafe-jev",
            operation=f"jev-{request.purpose}",
            status=result.status,
            run_id=request.run_id,
            projection_digest=result.state_digest,
            result_digest=result.response_digest,
            provider_model=result.model,
            provider_request_id=result.provider_request_id,
            duration_ms=result.duration_ms,
            retry_count=result.retry_count,
            detail_code=result.unavailable_reason,
            question_spec_id=request.question_spec.spec_id,
            question_spec_version=request.question_spec.version,
            question_spec_digest=result.question_spec_digest,
            policy_version=self._policy_version,
            policy_outcome=policy_outcome,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        emitted = self._receipt_sink(receipt)
        if inspect.isawaitable(emitted):
            await emitted


def _make_typesafe_questions(sdk: Any, spec: JevQuestionSpec) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for question_id, question in spec.questions.items():
        if isinstance(question, JevNoulQuestion):
            values[question_id] = sdk.Noul(
                instructions=question.instructions,
                criteria=(
                    {str(key).lower(): value for key, value in question.criteria.items()}
                    if question.criteria is not None
                    else None
                ),
            )
        elif isinstance(question, JevChoiceQuestion):
            values[question_id] = sdk.Choice(
                instructions=question.instructions,
                criteria=question.criteria,
            )
        else:
            values[question_id] = sdk.Score(
                instructions=question.instructions,
                criteria=question.criteria,
            )
    return values


def _normalize_jev_response(
    response: Any,
    request: JevDecisionRequest,
    *,
    duration_ms: float,
    retry_count: int,
) -> JevDecisionResult:
    payload = _to_mapping(response)
    answer_payload = payload.get("answers")
    if not isinstance(answer_payload, Mapping):
        answer_payload = _grouped_answers(payload)
    answers = {
        str(question_id): _JEV_ANSWER_ADAPTER.validate_python(answer)
        for question_id, answer in dict(answer_payload).items()
    }
    _validate_answers_match_spec(answers, request.question_spec)
    usage = _to_mapping(payload.get("usage", {}))
    canonical_answers = {
        question_id: answer.model_dump(mode="json") for question_id, answer in answers.items()
    }
    return JevDecisionResult(
        status=InteropOperationStatus.SUCCEEDED,
        model=_string_or_none(payload.get("model")),
        provider_request_id=_string_or_none(payload.get("request_id")),
        answers=answers,
        state_digest=request.state_digest,
        question_spec_digest=request.question_spec.digest,
        response_digest=content_digest(
            {
                "model": payload.get("model"),
                "answers": canonical_answers,
                "usage": usage,
                "request_id": payload.get("request_id"),
            }
        ),
        input_tokens=_nonnegative_int_or_none(usage.get("input_tokens")),
        output_tokens=_nonnegative_int_or_none(usage.get("output_tokens")),
        duration_ms=duration_ms,
        retry_count=retry_count,
    )


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    if hasattr(value, "dict"):
        dumped = value.dict()
        if isinstance(dumped, Mapping):
            return dumped
    if hasattr(value, "__dict__"):
        return vars(value)
    raise JevResponseError(
        f"TypeSafe SDK response of type {type(value).__name__} is not mapping-compatible."
    )


def _grouped_answers(payload: Mapping[str, Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for collection_name, answer_type in (
        ("nouls", JevQuestionKind.NOUL),
        ("choices", JevQuestionKind.CHOICE),
        ("scores", JevQuestionKind.SCORE),
    ):
        collection = payload.get(collection_name, {})
        if not isinstance(collection, Mapping):
            continue
        for question_id, raw_answer in collection.items():
            normalized = dict(_to_mapping(raw_answer))
            normalized.setdefault("type", answer_type.value)
            answers[str(question_id)] = normalized
    return answers


def _validate_answers_match_spec(answers: Mapping[str, JevAnswer], spec: JevQuestionSpec) -> None:
    if set(answers) != set(spec.questions):
        raise JevResponseError(
            f"TypeSafe response answered question IDs {sorted(answers)} but the registered "
            f'question spec "{spec.spec_id}" declares {sorted(spec.questions)}.'
        )
    for question_id, question in spec.questions.items():
        answer = answers[question_id]
        if answer.type != question.type:
            raise JevResponseError(
                f'Jev answered question "{question_id}" with a "{answer.type.value}" answer '
                f'but the registered question type is "{question.type.value}".'
            )
        if isinstance(question, JevChoiceQuestion) and isinstance(answer, JevChoiceAnswer):
            if answer.choice not in question.criteria or set(answer.probabilities) != set(
                question.criteria
            ):
                raise JevResponseError(
                    f'Jev choice answer for question "{question_id}" does not match the '
                    f"registered options {sorted(question.criteria)}: the chosen option or the "
                    "probability keys are not exactly those options."
                )


def _safe_provider_error(error: Exception) -> str:
    name = type(error).__name__.lower()
    if isinstance(error, JevResponseError | OptionalDependencyError):
        return f"provider-{name}: {error}"
    if isinstance(error, TimeoutError):
        return f"provider-{name}: no response within the request deadline"
    if isinstance(error, ValidationError):
        issues = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['type']}"
            for issue in error.errors()[:5]
        )
        return f"provider-{name}: response does not match the typed answer contract ({issues})"
    return f"provider-{name}"


def _duration_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1_000


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
