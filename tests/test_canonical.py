"""The shared canonical encoding is byte-identical to every copy it replaced.

Persisted integrity hashes (audit logs, telemetry, project state, artifacts) were
computed with the old per-module helpers, so equivalence is what keeps them
verifiable after the consolidation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from nailong_agent_sdk.foundations.canonical import (
    canonical_json,
    estimate_tokens,
    sha256_json,
    sha256_text,
)


class _Model(BaseModel):
    name: str
    when: datetime


def _old_plain(value):  # audit_log, profiler, context_projection
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _old_ascii(value):  # telemetry_store, integrations content_digest
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _old_models(value):  # project_state_store / _models / _engine
    def default(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        return str(item)

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=default)


SAMPLES = [
    {},
    [],
    {"b": 1, "a": [3, 2, {"z": None, "y": True}]},
    {"unicode": "café — 新加坡 ✓", "emoji": "🚀"},
    {"path": Path("a/b"), "decimal": Decimal("1.50"), "when": datetime(2026, 10, 3, tzinfo=UTC)},
    {"nested": {"model": _Model(name="x", when=datetime(2026, 1, 1, tzinfo=UTC))}},
    [1.5, -0.0, 10**20, "x" * 1000],
]


@pytest.mark.parametrize("value", SAMPLES)
def test_canonical_json_matches_previous_helpers(value):
    assert canonical_json(value) == _old_plain(value) == _old_ascii(value)
    assert canonical_json(value, models=True) == _old_models(value)


@pytest.mark.parametrize("value", SAMPLES)
def test_digests_and_estimates_match(value):
    assert sha256_json(value) == hashlib.sha256(_old_plain(value).encode()).hexdigest()
    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()
    assert estimate_tokens(value) == max(1, len(_old_plain(value)) // 4)
    assert estimate_tokens(value, models=True) == max(1, len(_old_models(value)) // 4)
