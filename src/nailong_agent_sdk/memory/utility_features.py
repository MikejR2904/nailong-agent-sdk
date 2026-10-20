# Copyright (c) 2026 David Michael Indraputra

"""Selection-time feature extraction for learned episode-utility prediction."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .episode_models import EpisodeRecord
from .episode_scoring import _episode_terms, _terms

FEATURE_SCHEMA_VERSION = "episode-utility-features-v1"
EMBEDDING_DIMENSIONS = 64

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "bias": ("bias",),
    "task": ("lexical_relevance", "hashed_cosine"),
    "graph": ("dependent_share", "prerequisite_share", "closure_cost_share", "own_cost_share"),
    "provenance": ("substrate_backed", "exploratory", "has_source_handle"),
    "dynamics": ("recency", "frequency"),
    "outcome": ("failed_result",),
}
FEATURE_NAMES: tuple[str, ...] = tuple(name for names in FEATURE_GROUPS.values() for name in names)


@dataclass(frozen=True)
class RetentionContext:
    """Everything a predictor may read when it scores one retention decision."""

    records: tuple[EpisodeRecord, ...]
    token_costs: Mapping[str, int]
    relevance: Mapping[str, float]
    query: str
    token_budget: int


def masked_feature_names(disabled_groups: Sequence[str]) -> frozenset[str]:
    unknown = sorted(set(disabled_groups) - set(FEATURE_GROUPS))
    if unknown:
        raise ValueError(
            f"Unknown feature groups {unknown}; the schema {FEATURE_SCHEMA_VERSION} defines "
            f"{sorted(FEATURE_GROUPS)}."
        )
    return frozenset(name for group in disabled_groups for name in FEATURE_GROUPS[group])


def extract_features(
    context: RetentionContext, disabled_groups: Sequence[str] = ()
) -> dict[str, tuple[float, ...]]:
    """Return one schema-ordered feature vector per live episode."""

    masked = masked_feature_names(disabled_groups)
    records = {record.id: record for record in context.records}
    budget = max(context.token_budget, 1)
    max_dependents = max(
        (_live_count(record.depended_on_by, records) for record in records.values()), default=0
    )
    max_prerequisites = max((len(record.depends_on) for record in records.values()), default=0)
    max_access_count = max((record.access_count for record in records.values()), default=0)
    max_access_sequence = max(
        (record.last_access_sequence for record in records.values()), default=0
    )
    query_vector = _hashed_embedding(_terms(context.query))
    result: dict[str, tuple[float, ...]] = {}
    for episode_id in sorted(records):
        record = records[episode_id]
        closure_cost = sum(context.token_costs[item] for item in _closure(episode_id, records))
        values = {
            "bias": 1.0,
            "lexical_relevance": _unit(context.relevance[episode_id]),
            "hashed_cosine": _cosine(query_vector, _hashed_embedding(_episode_terms(record))),
            "dependent_share": _ratio(_live_count(record.depended_on_by, records), max_dependents),
            "prerequisite_share": _ratio(len(record.depends_on), max_prerequisites),
            "closure_cost_share": min(closure_cost / budget, 1.0),
            "own_cost_share": min(context.token_costs[episode_id] / budget, 1.0),
            "substrate_backed": float(record.substrate_backed),
            "exploratory": float(record.kind.value == "exploratory"),
            "has_source_handle": float(record.source_handle is not None),
            "recency": _ratio(record.last_access_sequence, max_access_sequence),
            "frequency": _ratio(record.access_count, max_access_count),
            "failed_result": float(_result_failed(record)),
        }
        result[episode_id] = tuple(
            0.0 if name in masked else values[name] for name in FEATURE_NAMES
        )
    return result


def _result_failed(record: EpisodeRecord) -> bool:
    result = (record.content or {}).get("result")
    return isinstance(result, dict) and result.get("status", "succeeded") != "succeeded"


def _live_count(identifiers: Sequence[str], records: Mapping[str, EpisodeRecord]) -> int:
    return sum(1 for identifier in identifiers if identifier in records)


def _closure(episode_id: str, records: Mapping[str, EpisodeRecord]) -> set[str]:
    closure = {episode_id}
    pending = [episode_id]
    while pending:
        for prerequisite in records[pending.pop()].depends_on:
            if prerequisite in records and prerequisite not in closure:
                closure.add(prerequisite)
                pending.append(prerequisite)
    return closure


def _ratio(value: float, maximum: float) -> float:
    return value / maximum if maximum else 0.0


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _hashed_embedding(terms: set[str]) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
