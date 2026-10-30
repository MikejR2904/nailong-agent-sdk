# Copyright (c) 2026 David Michael Indraputra

"""A hard ceiling on the calls and input tokens a judge run may spend."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

PRICE_PER_MILLION_INPUT_TOKENS = 0.042
CHARS_PER_TOKEN = 3


class BudgetExceeded(RuntimeError):
    """The next call would take the run past its ceiling; nothing was sent."""


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def dollars(input_tokens: int) -> float:
    return input_tokens / 1_000_000 * PRICE_PER_MILLION_INPUT_TOKENS


@dataclass
class Spent:
    calls: int
    input_tokens: int


class Budget:
    def __init__(self, max_calls: int, max_input_tokens: int) -> None:
        if max_calls < 1 or max_input_tokens < 1:
            raise ValueError(
                f"a budget needs at least one call and one token, got {max_calls} calls and "
                f"{max_input_tokens} tokens."
            )
        self.max_calls = max_calls
        self.max_input_tokens = max_input_tokens
        self._calls = 0
        self._tokens = 0
        self._lock = threading.Lock()

    def reserve(self, estimate: int) -> None:
        with self._lock:
            if self._calls + 1 > self.max_calls:
                raise BudgetExceeded(
                    f"the call ceiling of {self.max_calls} is reached after {self._calls} calls."
                )
            if self._tokens + estimate > self.max_input_tokens:
                raise BudgetExceeded(
                    f"a call of about {estimate} input tokens would take the run past its "
                    f"ceiling of {self.max_input_tokens} (spent {self._tokens})."
                )
            self._calls += 1
            self._tokens += estimate

    def settle(self, estimate: int, actual: int | None) -> None:
        if actual is None:
            return
        with self._lock:
            self._tokens += actual - estimate

    @property
    def spent(self) -> Spent:
        with self._lock:
            return Spent(self._calls, self._tokens)
