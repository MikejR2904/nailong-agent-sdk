# Copyright (c) 2026 David Michael Indraputra

"""Stable error categories and safe failure-detail handling for the Python runtime."""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .text import scrub_surrogates

_SECRET_KEY = re.compile(
    r"(authorization|api[_-]?key|passw(?:or)?d|passphrase|secret|access[_-]?key|signing[_-]?key"
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
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_RUN_START = r"(?<![A-Za-z0-9_-])"
_SECRET_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_RUN_START}sk-[A-Za-z0-9_-]{{20,}}+"),
    re.compile(rf"{_RUN_START}sk_(?:live|test)_[A-Za-z0-9]{{16,}}+"),
    re.compile(rf"{_RUN_START}(?:AKIA|ASIA)[0-9A-Z]{{16}}\b"),
    re.compile(rf"{_RUN_START}gh[pousr]_[A-Za-z0-9]{{30,}}+"),
    re.compile(rf"{_RUN_START}github_pat_[A-Za-z0-9_]{{20,}}+"),
    re.compile(rf"{_RUN_START}glpat-[A-Za-z0-9_-]{{20,}}+"),
    re.compile(rf"{_RUN_START}hf_[A-Za-z0-9]{{30,}}+"),
    re.compile(rf"{_RUN_START}AIza[0-9A-Za-z_-]{{35}}"),
    re.compile(rf"{_RUN_START}npm_[A-Za-z0-9]{{36}}"),
    re.compile(rf"{_RUN_START}fw_[A-Za-z0-9]{{20,}}+"),
    re.compile(rf"{_RUN_START}xox[baprs]-[A-Za-z0-9-]{{10,}}+"),
    re.compile(rf"{_RUN_START}eyJ[A-Za-z0-9_-]{{8,}}+\.[A-Za-z0-9_-]{{8,}}+\.[A-Za-z0-9_-]{{8,}}+"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{10,}=*"),
)
_URL_CREDENTIAL = re.compile(r"(://[^/\s:@]+:)[^/\s@]+(@)")
_ASSIGNMENT_TAIL = re.compile(
    r"['\"]?\s*[:=]\s*(?:\"[^\"\r\n]{4,}\"|'[^'\r\n]{4,}'|(?P<value>[^\s'\";,}]{4,}))"
)
_AUTHORIZATION_SCHEMES = frozenset(
    {
        "basic",
        "bearer",
        "digest",
        "negotiate",
        "ntlm",
        "token",
        "apikey",
        "hmac",
        "aws4-hmac-sha256",
    }
)
_AUTHORIZATION_CREDENTIAL = re.compile(r"[ \t]+[^\s'\";,}]{4,}")
_IDENTIFIER_RUN = re.compile(r"[A-Za-z0-9_-]+")
# Lowercase literal substrings every pattern above (and _SECRET_KEY, used by
# _redact_assignments) requires; used as the fast pre-filter in _redact_content.
_CONTENT_HINT_SUBSTRINGS = (
    "-----begin",
    "sk-",
    "sk_",
    "akia",
    "asia",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "hf_",
    "aiza",
    "npm_",
    "fw_",
    "xox",
    "eyj",
    "://",
    "bearer",
    "authorization",
    "key",
    "passw",
    "passphrase",
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

    redacted = _redact_value(details or {}, hide_reasoning_keys=True)
    encoded = json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded) <= _MAX_FAILURE_DETAIL_CHARS:
        return redacted if isinstance(redacted, dict) else {"value": redacted}
    return {
        "truncated": True,
        "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "original_chars": len(encoded),
        "preview": encoded[:_MAX_FAILURE_DETAIL_CHARS],
    }


def redact_secrets(value: Any) -> Any:
    """Recursively redact credential-shaped dict keys and free-text content.

    Shared by every durable record (audit log, telemetry) so a fix to a
    detection pattern — e.g. excluding ``token_budget``/``input_tokens`` from
    the ``token`` match — applies everywhere at once instead of drifting
    across independent copies. A dict key match redacts the whole value; a
    plain string is scanned for the free-text patterns in
    ``_SECRET_CONTENT_PATTERNS`` and only the matched span is masked, so
    surrounding context (e.g. captured tool output) survives. Lists, tuples,
    sets and bytes are walked too.
    """

    return _redact_value(value, hide_reasoning_keys=False)


def _redact_value(value: Any, *, hide_reasoning_keys: bool) -> Any:
    if isinstance(value, dict):
        return {
            scrub_surrogates(str(key)): "[REDACTED]"
            if _SECRET_KEY.search(str(key))
            or (hide_reasoning_keys and str(key).lower() in _HIDDEN_REASONING_KEYS)
            else _redact_value(item, hide_reasoning_keys=hide_reasoning_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, hide_reasoning_keys=hide_reasoning_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, hide_reasoning_keys=hide_reasoning_keys) for item in value)
    if isinstance(value, set | frozenset):
        return type(value)(
            _redact_value(item, hide_reasoning_keys=hide_reasoning_keys) for item in value
        )
    if isinstance(value, bytes | bytearray):
        return _redact_content(bytes(value).decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return _redact_content(value)
    return value


def contains_secret_text(text: str) -> bool:
    return _redact_content(text) != scrub_surrogates(text)


def _redact_content(text: str) -> str:
    text = scrub_surrogates(text)
    lowered = text.lower()
    if not any(hint in lowered for hint in _CONTENT_HINT_SUBSTRINGS):
        return text
    text = _redact_pem_blocks(text)
    for pattern in _SECRET_CONTENT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]\2", text)
    return _redact_assignments(text)


def _redact_pem_blocks(text: str) -> str:
    if "-----BEGIN" not in text:
        return text
    ends = list(_PEM_END.finditer(text))
    if not ends:
        return text
    end_starts = [match.start() for match in ends]
    pieces: list[str] = []
    cursor = 0
    for begin in _PEM_BEGIN.finditer(text):
        if begin.start() < cursor:
            continue
        index = bisect.bisect_left(end_starts, begin.end())
        if index == len(ends):
            break
        pieces.append(text[cursor : begin.start()])
        pieces.append("[REDACTED]")
        cursor = ends[index].end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _redact_assignments(text: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for run in _IDENTIFIER_RUN.finditer(text):
        if run.start() < cursor or _SECRET_KEY.search(run.group()) is None:
            continue
        tail = _ASSIGNMENT_TAIL.match(text, run.end())
        if tail is None:
            continue
        end = tail.end()
        value = tail.group("value")
        if (
            value is not None
            and value.lower() in _AUTHORIZATION_SCHEMES
            and "authorization" in run.group().lower()
        ):
            credential = _AUTHORIZATION_CREDENTIAL.match(text, end)
            if credential is not None:
                end = credential.end()
        pieces.append(text[cursor : run.start()])
        pieces.append("[REDACTED]")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def redact_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _HIDDEN_REASONING_KEYS:
                redacted[f"{key}_redacted"] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_hidden_reasoning(item)
        return redacted
    if isinstance(value, list | tuple):
        return [redact_hidden_reasoning(item) for item in value]
    if isinstance(value, set | frozenset):
        return [redact_hidden_reasoning(item) for item in sorted(value, key=repr)]
    return value


def assert_no_hidden_reasoning(value: Any, path: str = "$") -> None:
    """Raise if ``value`` contains a key reserved for private model reasoning.

    Telemetry and the audit log are permanent, review-facing records; unlike
    :func:`sanitize_failure_details` (which redacts and continues so a failure
    can still be reported), these refuse to persist the entry at all.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _HIDDEN_REASONING_KEYS:
                raise ValueError(
                    f'Durable records must not persist hidden model reasoning: key "{key}" at '
                    f"{path}."
                )
            assert_no_hidden_reasoning(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            assert_no_hidden_reasoning(item, f"{path}[{index}]")
    elif isinstance(value, set | frozenset):
        for index, item in enumerate(sorted(value, key=repr)):
            assert_no_hidden_reasoning(item, f"{path}[{index}]")
