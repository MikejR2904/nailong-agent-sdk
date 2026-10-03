# Copyright (c) 2026 David Michael Indraputra

"""Private deterministic helpers shared by optional framework integrations."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from typing import Any

_FORBIDDEN_KEYS = {
    "access_key",
    "access_token",
    "approval",
    "approval_id",
    "approval_token",
    "api_key",
    "apikey",
    "auth_token",
    "audit",
    "audit_log",
    "authorization",
    "bearer",
    "capability",
    "capability_token",
    "cookie",
    "client_secret",
    "credential",
    "id_token",
    "jwt",
    "message",
    "message_history",
    "messages",
    "password",
    "private_key",
    "raw_response",
    "refresh_token",
    "secret",
    "secret_key",
    "session_token",
    "token",
    "tool_arguments",
    "tool_result",
    "tool_results",
    "transcript",
}
_FORBIDDEN_COLLAPSED_KEYS = {key.replace("_", "") for key in _FORBIDDEN_KEYS}
_MAX_INTEROP_DEPTH = 16
_MAX_INTEROP_LIST_ITEMS = 256
_MAX_INTEROP_MAPPING_ENTRIES = 128
_MAX_INTEROP_STRING_CHARS = 16_384


def _canonical_key(value: Any) -> str:
    """Normalize snake, kebab, camel, case, and separator variants deterministically."""

    token = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value).strip())
    return re.sub(r"[^A-Za-z0-9]+", "_", token).strip("_").lower()


def assert_sanitized_interop_value(value: Any, *, _depth: int = 0) -> None:
    """Reject authority material and values unsafe for a framework boundary."""

    if _depth > _MAX_INTEROP_DEPTH:
        raise ValueError("Interoperability projection exceeds the maximum nesting depth.")
    if value is None or isinstance(value, (int, float, bool)):
        return
    if isinstance(value, str):
        if len(value) > _MAX_INTEROP_STRING_CHARS:
            raise ValueError("Interoperability projection string exceeds the maximum length.")
        return
    if isinstance(value, list):
        if len(value) > _MAX_INTEROP_LIST_ITEMS:
            raise ValueError("Interoperability projection list exceeds the maximum size.")
        for item in value:
            assert_sanitized_interop_value(item, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_INTEROP_MAPPING_ENTRIES:
            raise ValueError("Interoperability projection object exceeds the maximum size.")
        for key, item in value.items():
            if len(str(key)) > 256:
                raise ValueError("Interoperability projection key exceeds the maximum length.")
            normalized = _canonical_key(key)
            if (
                normalized in _FORBIDDEN_KEYS
                or normalized.replace("_", "") in _FORBIDDEN_COLLAPSED_KEYS
                or "secret" in normalized
            ):
                raise ValueError(
                    f'Unsafe key "{key}" is forbidden in an interoperability projection.'
                )
            assert_sanitized_interop_value(item, _depth=_depth + 1)
        return
    raise TypeError(
        "Interoperability projections must contain JSON-compatible scalar, list, or dict values."
    )


class OptionalDependencyError(RuntimeError):
    pass


def content_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_digest(value: Any) -> str:
    """Return a SHA-256 digest after sanitization and canonical JSON encoding."""

    assert_sanitized_interop_value(value)
    return content_digest(value)


def require_optional_module(module_name: str, extra_name: str) -> Any:
    """Load an optional dependency with a precise installation instruction."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        raise OptionalDependencyError(
            f'Optional dependency "{module_name}" is required; install '
            f"nailong-agent-sdk[{extra_name}]."
        ) from error
