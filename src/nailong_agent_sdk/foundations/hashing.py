# Copyright (c) 2026 David Michael Indraputra

"""Canonical JSON encodings, SHA-256 digests and the token estimate shared by durable records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

_CHARACTERS_PER_TOKEN = 4

"""
Since our SDK is designed to be used in a variety of contexts, we need to ensure that the JSON
serialization (which is used mainly for the agent output) is consistent across different
environments. One way to achieve this is to normalize/canonicalize the JSON serialization by sorting
the keys and using a consistent separator.
SHA-256 is used here as the hashing algorithm itself for a fixed 256-bit (32-byte) output.
"""


def canonical_json(value: Any) -> str:  # Fallback to str for non-serializable objects
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# Much more strict version as it will raise TypeError for non-serializable objects
def strict_canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# Handle pydantic models and other objects that have a model_dump method; see below
def model_canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_model_or_text)


# Main hashing function
def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def estimate_tokens(value: Any, serialize: Callable[[Any], str] = canonical_json) -> int:
    return max(1, len(serialize(value)) // _CHARACTERS_PER_TOKEN)


# Helper function to handle pydantic models and other objects that have a model_dump method
# model_dump here is only accessible through pydantic v2 model objects.
def _model_or_text(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return str(item)  # Fallback to str for non-serializable objects
