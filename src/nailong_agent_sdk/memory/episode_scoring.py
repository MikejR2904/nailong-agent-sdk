# Copyright (c) 2026 David Michael Indraputra

"""Deterministic, dependency-free lexical relevance scoring for episode retention."""

from __future__ import annotations

import json
import re

from ..foundations.canonical import estimate_tokens
from ..foundations.contracts import EpisodeKind
from .episode_models import EpisodeRecord


class LexicalEpisodeRelevanceScorer:
    """Dependency-free deterministic relevance baseline based on token coverage."""

    def score(self, query: str, episode: EpisodeRecord) -> float:
        query_terms = _terms(query)
        if not query_terms:
            return 0.0
        overlap = query_terms & _episode_terms(episode)
        return len(overlap) / len(query_terms)


def _episode_tokens(record: EpisodeRecord) -> int:
    payload = record.content if record.content is not None else {"description": record.description}
    return estimate_tokens(payload)


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9_]+", value.lower()) if len(term) > 1}


def _episode_terms(record: EpisodeRecord) -> set[str]:
    content = json.dumps(record.content, sort_keys=True, default=str) if record.content else ""
    return _terms(f"{record.description or ''} {content[:16_384]}")


def _provenance_weight(record: EpisodeRecord) -> float:
    if record.substrate_backed:
        return 1.0
    if record.kind is EpisodeKind.EXPLORATORY:
        return 0.65
    if record.eda_manifest is not None:
        return 0.20
    return 0.40


def _clamp_score(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value
