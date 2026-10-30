# Copyright (c) 2026 David Michael Indraputra

"""Ask Jev through the SDK's own evaluator, inside a budget, with a cache and a receipt per call."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nailong_agent_sdk.integrations.contracts import InteropOperationStatus
from nailong_agent_sdk.integrations.jev.models import (
    JevDecisionProvider,
    JevDecisionReceipt,
    JevDecisionRequest,
    JevQuestionSpec,
)

from .budget import Budget, estimate_tokens
from .cache import AnswerCache, cache_key

MODEL = "jev-1.13.0"
POLICY_VERSION = "trust-bench-judge-v1"
KEY_NAME = "TYPESAFE_API_KEY"
REPO_ROOT = Path(__file__).resolve().parents[4]
DEADLINE_SECONDS = 60.0
MAX_ATTEMPTS = 2


class JudgeUnavailable(RuntimeError):
    """Jev cannot be asked, and the message says what is missing."""


def ensure_api_key(env: MutableMapping[str, str] | None = None, dotenv: Path | None = None) -> None:
    """Make the key visible to the client, reading no other name from the dotenv file."""
    target = os.environ if env is None else env
    if target.get(KEY_NAME):
        return
    path = REPO_ROOT / ".env" if dotenv is None else dotenv
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == KEY_NAME and value.strip():
                target[KEY_NAME] = value.strip().strip("'\"")
                return
    raise JudgeUnavailable(
        f"{KEY_NAME} is set neither in the environment nor in {path}; Jev cannot be asked "
        "without it."
    )


class JsonlReceiptSink:
    """The SDK's digest-only receipt of every call, appended to a file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def __call__(self, receipt: JevDecisionReceipt) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(receipt.model_dump_json() + "\n")


def build_provider(receipts: Path) -> JevDecisionProvider:
    ensure_api_key()
    from nailong_agent_sdk.integrations.jev.decision import TypeSafeJevDecisionEvaluator

    try:
        import typesafe_sdk  # noqa: F401
    except ModuleNotFoundError as error:
        raise JudgeUnavailable(
            "the package typesafe_sdk is not installed; install the jev extra of this "
            'repository, for example pip install "typesafe-sdk>=0.7,<1".'
        ) from error
    return TypeSafeJevDecisionEvaluator(
        receipt_sink=JsonlReceiptSink(receipts), policy_version=POLICY_VERSION
    )


@dataclass(frozen=True)
class JudgeAnswer:
    succeeded: bool
    cached: bool
    answers: dict[str, Any]
    input_tokens: int | None
    model: str | None
    reason: str | None


class JevJudge:
    def __init__(
        self,
        provider: JevDecisionProvider,
        *,
        budget: Budget,
        cache: AnswerCache | None = None,
        model: str = MODEL,
        run_id: str = "trust-bench-judge",
    ) -> None:
        self._provider = provider
        self._budget = budget
        self._cache = cache
        self.model = model
        self._run_id = run_id

    def ask(
        self, spec: JevQuestionSpec, state: dict[str, Any], purpose: str = "verification"
    ) -> JudgeAnswer:
        request = JevDecisionRequest(
            purpose=purpose,  # type: ignore[arg-type]
            run_id=self._run_id,
            state=state,
            question_spec=spec,
            model=self.model,
            deadline_seconds=DEADLINE_SECONDS,
            max_attempts=MAX_ATTEMPTS,
        )
        key = cache_key(request)
        stored = self._cache.get(key) if self._cache is not None else None
        if stored is not None:
            return self._answer(stored, cached=True)
        estimate = estimate_tokens(request.model_dump_json())
        self._budget.reserve(estimate)
        result = asyncio.run(self._provider.evaluate(request))
        self._budget.settle(estimate, result.input_tokens)
        if result.status is InteropOperationStatus.SUCCEEDED and self._cache is not None:
            self._cache.put(key, result)
        return self._answer(result, cached=False)

    @staticmethod
    def _answer(result: Any, *, cached: bool) -> JudgeAnswer:
        return JudgeAnswer(
            succeeded=result.status is InteropOperationStatus.SUCCEEDED,
            cached=cached,
            answers=dict(result.answers),
            input_tokens=result.input_tokens,
            model=result.model,
            reason=result.unavailable_reason,
        )
