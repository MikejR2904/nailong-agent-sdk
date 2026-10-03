# Copyright (c) 2026 David Michael Indraputra

"""The one canonical JSON encoding, digest, and token estimate used across the SDK.

Every integrity chain, content address, and context budget in the SDK hashes or
measures the same canonical form, so it lives in exactly one place. The encoding
is byte-for-byte the one each module previously used, which keeps every hash
already persisted on disk verifiable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _model_aware_default(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return str(item)


def canonical_json(value: Any, *, models: bool = False) -> str:
    """Sorted-key, compact, ASCII JSON.

    Values that are not JSON-native fall back to ``str(value)``. With ``models``
    set, pydantic models are encoded through ``model_dump(mode="json")`` instead,
    which is what typed project state uses.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_model_aware_default if models else str,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: Any, *, models: bool = False) -> str:
    """SHA-256 hex digest of ``canonical_json(value)``."""

    return sha256_text(canonical_json(value, models=models))


def estimate_tokens(value: Any, *, models: bool = False) -> int:
    """Conservative token estimate: one token per four canonical characters, minimum one."""

    return max(1, len(canonical_json(value, models=models)) // 4)
