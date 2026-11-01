# Copyright (c) 2026 David Michael Indraputra

"""Bounds on what one ledger record may carry, keeping the fields that say who and what."""

from __future__ import annotations

from typing import Any

from ..foundations.hashing import canonical_json, sha256_hex

KEPT_FIELD_CHARS = 256


def bound_payload(redacted: Any, max_chars: int) -> dict[str, Any]:
    encoded = canonical_json(redacted)
    if len(encoded) <= max_chars:
        if isinstance(redacted, dict):
            return redacted
        return {"value": redacted}
    bounded: dict[str, Any] = {
        "truncated": True,
        "content_hash": sha256_hex(encoded),
        "original_chars": len(encoded),
        "preview": encoded[:max_chars],
    }
    if isinstance(redacted, dict):
        bounded.update(identity_fields(redacted, set(bounded)))
    return bounded


def identity_fields(payload: dict[str, Any], reserved: set[str]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    dropped: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if key in reserved:
            continue
        encoded = canonical_json(value)
        small = isinstance(value, str | int | float | bool) or value is None
        if small and len(encoded) <= KEPT_FIELD_CHARS:
            kept[key] = value
        else:
            dropped[key] = {"chars": len(encoded), "sha256": sha256_hex(encoded)}
    if dropped:
        kept["omitted_fields"] = dropped
    return kept
