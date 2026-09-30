# Copyright (c) 2026 David Michael Indraputra

"""Stable error categories and safe failure-detail handling for the Python runtime."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_SECRET_KEY = re.compile(
    r"(authorization|api[_-]?key|password|secret"
    r"|token(?!_?budget|_?cost|s\b)|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_HIDDEN_REASONING_KEYS = {"chain_of_thought", "hidden_reasoning", "reasoning_trace", "scratchpad"}
_MAX_FAILURE_DETAIL_CHARS = 2_048

# Free-text patterns for the credentials a key-name check cannot see — e.g. a
# subprocess that prints its own environment, or an error message that quotes
# a connection string. This is a best-effort net for well-known credential
# shapes, not a general secret scanner: it will not catch a bespoke or
# high-entropy token that matches none of these shapes. Each pattern is
# anchored on a distinctive literal prefix before a bounded/simple quantified
# class, so none of them can backtrack more than linearly — the
# ``KEYWORD=value`` case that needs an unanchored identifier boundary is
# handled separately by ``_redact_assignments`` instead of a regex here; see
# that function's docstring for why.
_SECRET_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{10,}=*"),
)
_ASSIGNMENT_TAIL = re.compile(r"\s*[:=]\s*['\"]?[^\s'\";,}]{4,}")
_IDENTIFIER_CHAR = re.compile(r"[A-Za-z0-9_]")
# Lowercase literal substrings every pattern above (and _SECRET_KEY, used by
# _redact_assignments) requires; used as the fast pre-filter in _redact_content.
_CONTENT_HINT_SUBSTRINGS = (
    "-----begin",
    "sk-",
    "akia",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "xox",
    "bearer",
    "authorization",
    "key",
    "password",
    "secret",
    "token",
    "cookie",
    "credential",
)


@dataclass(eq=False)
class AgentSdkError(Exception):
    """A contract or runtime failure that can be returned as typed agent state."""

    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(eq=False)
class TransientProviderError(AgentSdkError):
    """A provider failure that is likely to succeed on retry (429, 5xx, or a connection drop).

    Distinguishing this from a plain ``AgentSdkError`` lets a caller such as
    ``FailoverAgentModel`` retry the same adapter with backoff before treating
    it as exhausted, instead of immediately burning a fallback slot on a
    transient blip that a fresh attempt would likely have survived.
    """

    retry_after_seconds: float | None = None


def sanitize_failure_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Redact, bound, and remove private reasoning before durable result exposure."""

    redacted = _redact_failure_value(details or {})
    encoded = json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded) <= _MAX_FAILURE_DETAIL_CHARS:
        return redacted if isinstance(redacted, dict) else {"value": redacted}
    return {
        "truncated": True,
        "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "original_chars": len(encoded),
        "preview": encoded[:_MAX_FAILURE_DETAIL_CHARS],
    }


def _redact_failure_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _SECRET_KEY.search(str(key)) or str(key).lower() in _HIDDEN_REASONING_KEYS
                else _redact_failure_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_failure_value(item) for item in value]
    if isinstance(value, str):
        return _redact_content(value)
    return value


def redact_secrets(value: Any) -> Any:
    """Recursively redact credential-shaped dict keys and free-text content.

    Shared by every durable record (audit log, telemetry) so a fix to a
    detection pattern — e.g. excluding ``token_budget``/``input_tokens`` from
    the ``token`` match — applies everywhere at once instead of drifting
    across independent copies. A dict key match redacts the whole value; a
    plain string is scanned for the free-text patterns in
    ``_SECRET_CONTENT_PATTERNS`` and only the matched span is masked, so
    surrounding context (e.g. captured tool output) survives.
    """

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _redact_content(value)
    return value


def _redact_content(text: str) -> str:
    # Every pattern below requires at least one of these literal substrings,
    # so a miss here means none of them can possibly match. Plain substring
    # search is far cheaper than even a "safe" regex engine's per-character,
    # per-alternative cost, and on a large string with nothing to redact —
    # the common case for captured tool output — this skips the regex work
    # entirely instead of paying to prove there is nothing to find.
    lowered = text.lower()
    if not any(hint in lowered for hint in _CONTENT_HINT_SUBSTRINGS):
        return text
    for pattern in _SECRET_CONTENT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return _redact_assignments(text)


def _redact_assignments(text: str) -> str:
    """Redact a ``SOME_SECRET_NAME=value`` / ``"api_key": "value"`` style assignment.

    This used to be one regex: an unanchored ``[A-Za-z0-9_]*`` run wrapped
    around the keyword alternation, so it could match a keyword embedded
    anywhere inside a longer identifier (``AWS_SECRET_ACCESS_KEY``). Measured
    on a realistic 60KB captured-output string, that pattern cost ~15ms per
    call — regex engines backtrack that leading run character-by-character at
    every position when nothing matches, which is most of a long string of
    ordinary text. This reaches the same result in linear time instead: find
    the cheap keyword match first (the same fast ``_SECRET_KEY`` alternation
    used for dict keys), extend it left/right through identifier characters
    by hand, then check whether an assignment immediately follows.
    """

    pieces: list[str] = []
    cursor = 0
    for match in _SECRET_KEY.finditer(text):
        if match.start() < cursor:
            continue  # inside a span this loop already consumed.
        start, end = match.start(), match.end()
        while start > 0 and _IDENTIFIER_CHAR.match(text[start - 1]):
            start -= 1
        while end < len(text) and _IDENTIFIER_CHAR.match(text[end]):
            end += 1
        tail = _ASSIGNMENT_TAIL.match(text, end)
        if tail is None:
            continue
        pieces.append(text[cursor:start])
        pieces.append("[REDACTED]")
        cursor = tail.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def assert_no_hidden_reasoning(value: Any) -> None:
    """Raise if ``value`` contains a key reserved for private model reasoning.

    Telemetry and the audit log are permanent, review-facing records; unlike
    :func:`sanitize_failure_details` (which redacts and continues so a failure
    can still be reported), these refuse to persist the entry at all.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _HIDDEN_REASONING_KEYS:
                raise ValueError("Durable records must not persist hidden model reasoning.")
            assert_no_hidden_reasoning(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_hidden_reasoning(item)
