"""Measured downstream executor: one real model call per (task, retention set, trial)."""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nailong_agent_sdk.agent.openai_compatible.chat import OpenAICompatibleAgentModel
from nailong_agent_sdk.agent.openai_compatible.transport import (
    HttpxAsyncJsonTransport,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.foundations.contracts import (
    BlockedTurn,
    FinalTurn,
    ModelBinding,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episode_models import SemanticSummaryView

from ..instances import Instance
from ..ledger import OutcomeLedger
from ..oracle import MEASURED
from .context import build_scene, model_context
from .scenarios import LiveTask, score_answer

FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
MODEL_OUTPUT_FAILURES = frozenset(
    {
        "OPENAI_COMPATIBLE_RESPONSE_INVALID",
        "OPENAI_COMPATIBLE_RESPONSE_TRUNCATED",
        "OPENAI_COMPATIBLE_RESPONSE_FILTERED",
    }
)


def api_key_from_environment(name: str, env_file: Path | None = None) -> str:
    value = os.environ.get(name)
    if not value and env_file is not None and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip() == name:
                value = raw.strip().strip("\"'")
    if not value:
        raise AgentSdkError(
            "LIVE_API_KEY_MISSING",
            f'Environment variable "{name}" is not set and no value was found in '
            f'"{env_file}"; the live executor cannot call the model without it.',
        )
    return value


SUMMARY_SUFFIX = "~s"


@dataclass(frozen=True)
class LiveSettings:
    model: str
    provider: str = "fireworks"
    base_url: str = FIREWORKS_BASE_URL
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {"temperature": 0.6, "max_tokens": 1024}
    )
    timeout_seconds: float = 120.0
    concurrency: int = 8
    max_attempts: int = 12
    input_token_cap: int | None = None
    enforce_budget: bool = True


@dataclass
class UsageTotals:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    output_failures: int = 0
    retries: int = 0


class Throttle:
    """Adaptive concurrency shared by every worker: halve on a rejection, regrow on success."""

    def __init__(
        self,
        max_concurrency: int = 8,
        base_seconds: float = 1.0,
        ceiling_seconds: float = 120.0,
        spread_seconds: float = 1.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("Throttle max_concurrency must be at least 1.")
        self._maximum = float(max_concurrency)
        self._limit = float(max_concurrency)
        self._base = base_seconds
        self._ceiling = ceiling_seconds
        self._spread = spread_seconds
        self._until = 0.0
        self._in_flight = 0
        self._streak = 0
        self._consecutive_rejections = 0

    @property
    def limit(self) -> int:
        return max(1, int(self._limit))

    async def acquire(self) -> None:
        while True:
            remaining = self._until - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining + random.random() * self._spread)
            elif self._in_flight < self.limit:
                self._in_flight += 1
                return
            else:
                await asyncio.sleep(0.05)

    def release(self) -> None:
        self._in_flight -= 1

    def succeeded(self) -> None:
        self._consecutive_rejections = 0
        self._streak += 1
        if self._streak >= self.limit:
            self._limit = min(self._maximum, self._limit + 1.0)
            self._streak = 0

    def rejected(self, retry_after_seconds: float | None) -> float:
        self._consecutive_rejections += 1
        self._limit = max(1.0, self._limit / 2.0)
        self._streak = 0
        delay = min(
            self._ceiling,
            max(retry_after_seconds or self._base, self._base)
            * 2 ** min(self._consecutive_rejections - 1, 4),
        )
        delay *= 1.0 + random.random() * 0.25
        self._until = max(self._until, time.monotonic() + delay)
        return delay


class LiveExecutor:
    evidence_kind = MEASURED

    def __init__(
        self,
        tasks: Mapping[str, LiveTask],
        settings: LiveSettings,
        ledger: OutcomeLedger,
        api_key: str,
        usage: UsageTotals | None = None,
        throttle: Throttle | None = None,
    ) -> None:
        self._tasks = dict(tasks)
        self._settings = settings
        self._ledger = ledger
        self._model = OpenAICompatibleAgentModel(
            OpenAICompatibleEndpoint(
                base_url=settings.base_url,
                api_key=api_key,
                timeout_seconds=settings.timeout_seconds,
            ),
            provider=settings.provider,
            model=settings.model,
            transport=HttpxAsyncJsonTransport(),
        )
        self.usage = usage if usage is not None else UsageTotals()
        self._throttle = throttle if throttle is not None else Throttle(settings.concurrency)
        self._scenes: dict[str, object] = {}
        self.summaries: dict[tuple[str, str], tuple[str, int]] = {}

    def _binding(self, trial: int) -> ModelBinding:
        return ModelBinding(
            provider=self._settings.provider,
            model=self._settings.model,
            parameters={**self._settings.parameters, "seed": trial},
        )

    async def _call(self, task: LiveTask, selection: Collection[str], trial: int):
        scene = build_scene(
            task.instance,
            None
            if self._settings.enforce_budget
            else ContextProjectionPolicy(episode_token_budget=12_000),
        )
        full = {i for i in selection if not i.endswith(SUMMARY_SUFFIX)}
        summarized = sorted(
            i[: -len(SUMMARY_SUFFIX)] for i in selection if i.endswith(SUMMARY_SUFFIX)
        )
        views = []
        summary_tokens = 0
        for episode_id in summarized:
            text, tokens = self.summaries[(task.instance.instance_id, episode_id)]
            summary_tokens += tokens
            views.append(
                SemanticSummaryView(
                    summary_id=f"tier-{episode_id}",
                    level=1,
                    summary_text=text,
                    source_episode_ids=[episode_id],
                    source_episode_count=1,
                )
            )
        context = model_context(
            scene,
            full,
            task.output_schema,
            self._binding(trial),
            enforce_budget=self._settings.enforce_budget,
            summaries=views,
            summary_tokens=summary_tokens,
        )
        for attempt in range(1, self._settings.max_attempts + 1):
            await self._throttle.acquire()
            try:
                response = await self._model.next_turn(context)
            except TransientProviderError as error:
                if attempt == self._settings.max_attempts:
                    raise
                self.usage.retries += 1
                self._throttle.rejected(error.retry_after_seconds)
            else:
                self._throttle.succeeded()
                return response
            finally:
                self._throttle.release()
        raise AssertionError("unreachable: the retry loop returns or raises")

    async def _evaluate_one(
        self, instance: Instance, selection: Collection[str], trial: int
    ) -> float:
        cached = self._ledger.lookup(instance.instance_id, selection, trial)
        if cached is not None:
            self.usage.cached += 1
            return cached
        cap = self._settings.input_token_cap
        if cap is not None and self.usage.input_tokens >= cap:
            raise AgentSdkError(
                "LIVE_TOKEN_CAP_REACHED",
                f"The input-token cap of {cap} was reached after {self.usage.calls} calls; "
                "raise input_token_cap to continue. Recorded outcomes are kept in the ledger.",
            )
        task = self._tasks[instance.instance_id]
        detail: dict[str, object] = {"model": self._settings.model}
        try:
            response = await self._call(task, selection, trial)
        except AgentSdkError as error:
            if error.code not in MODEL_OUTPUT_FAILURES:
                raise
            self.usage.calls += 1
            self.usage.output_failures += 1
            detail.update({"failure": error.code, "message": error.message[:200]})
            self._ledger.record(instance.instance_id, selection, trial, 0.0, detail)
            return 0.0
        self.usage.calls += 1
        if response.usage is not None:
            self.usage.input_tokens += response.usage.input_tokens or 0
            self.usage.output_tokens += response.usage.output_tokens or 0
            detail.update(
                {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "request_id": response.usage.request_id,
                }
            )
        turn = response.turn
        if isinstance(turn, FinalTurn) and isinstance(turn.output, Mapping):
            score = score_answer(turn.output, task.expected)
            detail["answer"] = {k: turn.output.get(k) for k in task.answer_keys}
        else:
            score = 0.0
            detail["failure"] = (
                f"turn:{turn.type}" if isinstance(turn, BlockedTurn) else "non-object-final"
            )
            self.usage.output_failures += 1
        self._ledger.record(instance.instance_id, selection, trial, score, detail)
        return score

    async def evaluate(
        self, requests: Sequence[tuple[Instance, Collection[str], int]]
    ) -> list[float]:
        semaphore = asyncio.Semaphore(self._settings.concurrency)
        in_flight: dict[tuple[str, tuple[str, ...], int], asyncio.Task[float]] = {}

        async def bounded(instance, selection, trial) -> float:
            async with semaphore:
                return await self._evaluate_one(instance, selection, trial)

        results: list[asyncio.Task[float]] = []
        for instance, selection, trial in requests:
            key = (instance.instance_id, tuple(sorted(selection)), trial)
            if key not in in_flight:
                in_flight[key] = asyncio.ensure_future(bounded(instance, selection, trial))
            results.append(in_flight[key])
        try:
            return list(await asyncio.gather(*results))
        except BaseException:
            for pending in in_flight.values():
                pending.cancel()
            raise

    def run_batch(self, requests: Sequence[tuple[Instance, Collection[str], int]]) -> list[float]:
        return asyncio.run(self.evaluate(requests))

    def run(self, instance: Instance, selection: Collection[str], seed: int) -> float:
        return self.run_batch([(instance, selection, seed)])[0]
