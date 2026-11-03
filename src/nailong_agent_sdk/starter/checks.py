# Copyright (c) 2026 David Michael Indraputra

"""Output checks as plain functions: accept an answer only when something else agrees."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent.verification import VerificationContext, VerificationReturn

OutputCheck = Callable[[Any], bool | str | tuple[bool, str | None] | Awaitable[Any]]
CHECK_GATE_ID = "starter-output-check"


def gate_callback(check: OutputCheck) -> Callable[..., Any]:
    name = getattr(check, "__name__", type(check).__name__)

    async def callback(context: VerificationContext) -> VerificationReturn:
        verdict = check(context.output)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if isinstance(verdict, bool):
            return (verdict, None if verdict else f'Check "{name}" rejected the output.')
        if isinstance(verdict, str) and verdict.strip():
            return (False, verdict)
        if (
            isinstance(verdict, tuple)
            and len(verdict) == 2
            and isinstance(verdict[0], bool)
            and (verdict[1] is None or isinstance(verdict[1], str))
        ):
            return verdict
        raise TypeError(
            f'Check "{name}" must return True to accept the output, False or a non-empty reason '
            f"string to reject it, or (accepted, reason); it returned {type(verdict).__name__}."
        )

    return callback
