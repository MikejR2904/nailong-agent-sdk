# Copyright (c) 2026 David Michael Indraputra

"""Schema v2: v1 features plus task-conditioned, transitive-graph and failure-propagation groups.

All features are deterministic functions of the selection-time context; none reads outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from .episode_models import EpisodeRecord
from .episode_scoring import _episode_terms, _terms
from .utility_features import (
    FEATURE_GROUPS,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    RetentionContext,
    _closure,
    _cosine,
    _hashed_embedding,
    _ratio,
    _result_failed,
    extract_features,
)

FEATURE_SCHEMA_VERSION_V2 = "episode-utility-features-v2"

FEATURE_GROUPS_V2: dict[str, tuple[str, ...]] = {
    **FEATURE_GROUPS,
    "task_v2": ("identifier_overlap", "query_coverage", "unique_coverage", "redundancy"),
    "graph_v2": (
        "dependent_cost_share",
        "exclusive_dependent_share",
        "co_prerequisite_share",
        "dependent_failed_share",
    ),
    "failure_v2": ("failed_ancestor", "failed_descendant", "failed_correction"),
}
FEATURE_NAMES_V2: tuple[str, ...] = tuple(
    name for names in FEATURE_GROUPS_V2.values() for name in names
)


def _identifier_terms(terms: set[str]) -> set[str]:
    return {t for t in terms if any(c.isdigit() for c in t) or "-" in t or "_" in t}


def _descendants(episode_id: str, records: Mapping[str, EpisodeRecord]) -> set[str]:
    seen: set[str] = set()
    pending = [episode_id]
    while pending:
        for dependent in records[pending.pop()].depended_on_by:
            if dependent in records and dependent not in seen:
                seen.add(dependent)
                pending.append(dependent)
    return seen


def extract_features_v2(
    context: RetentionContext, disabled_groups: Sequence[str] = ()
) -> dict[str, tuple[float, ...]]:
    unknown = sorted(set(disabled_groups) - set(FEATURE_GROUPS_V2))
    if unknown:
        raise ValueError(
            f"Unknown feature groups {unknown}; the schema {FEATURE_SCHEMA_VERSION_V2} defines "
            f"{sorted(FEATURE_GROUPS_V2)}."
        )
    base = extract_features(context, [g for g in disabled_groups if g in FEATURE_GROUPS])
    masked_v2 = {
        name
        for group in disabled_groups
        if group not in FEATURE_GROUPS
        for name in FEATURE_GROUPS_V2[group]
    }
    records = {record.id: record for record in context.records}
    ids = sorted(records)
    query_terms = _terms(context.query)
    query_identifiers = _identifier_terms(query_terms)
    episode_terms = {i: _episode_terms(records[i]) for i in ids}
    embeddings = {i: _hashed_embedding(episode_terms[i]) for i in ids}
    descendants = {i: _descendants(i, records) for i in ids}
    budget = max(context.token_budget, 1)
    live_depends = {i: [p for p in records[i].depends_on if p in records] for i in ids}
    max_dependents = max((len(descendants[i]) for i in ids), default=0)
    max_co = max(
        (len({p for d in descendants[i] for p in live_depends[d]} - {i}) for i in ids), default=0
    )
    failed = {i for i in ids if _result_failed(records[i])}
    covering: dict[str, int] = {}
    for terms in episode_terms.values():
        for term in query_terms & terms:
            covering[term] = covering.get(term, 0) + 1
    result: dict[str, tuple[float, ...]] = {}
    for episode_id in ids:
        terms = episode_terms[episode_id]
        covered = query_terms & terms
        ancestors = _closure(episode_id, records) - {episode_id}
        dependents = descendants[episode_id]
        co_prerequisites = {p for d in dependents for p in live_depends[d]} - {episode_id}
        values = {
            "identifier_overlap": _ratio(len(query_identifiers & terms), len(query_identifiers)),
            "query_coverage": _ratio(len(covered), len(query_terms)),
            "unique_coverage": _ratio(
                sum(1 for t in covered if covering.get(t, 0) == 1), len(query_terms)
            ),
            "redundancy": max(
                (
                    _cosine(embeddings[episode_id], embeddings[other])
                    for other in ids
                    if other != episode_id
                ),
                default=0.0,
            ),
            "dependent_cost_share": min(
                sum(context.token_costs[d] for d in dependents) / budget, 1.0
            ),
            "exclusive_dependent_share": _ratio(
                sum(1 for d in dependents if live_depends[d] == [episode_id]), max_dependents
            ),
            "co_prerequisite_share": _ratio(len(co_prerequisites), max_co),
            "dependent_failed_share": _ratio(len(dependents & failed), len(dependents)),
            "failed_ancestor": float(bool(ancestors & failed)),
            "failed_descendant": float(bool(descendants[episode_id] & failed)),
            "failed_correction": float(
                episode_id not in failed
                and any(p in failed for p in records[episode_id].depends_on)
            ),
        }
        extra = tuple(
            0.0 if name in masked_v2 else values[name]
            for group in ("task_v2", "graph_v2", "failure_v2")
            for name in FEATURE_GROUPS_V2[group]
        )
        result[episode_id] = base[episode_id] + extra
    return result


FEATURE_SCHEMA_VERSION_V3 = "episode-utility-features-v3"

FEATURE_GROUPS_V3: dict[str, tuple[str, ...]] = {
    **FEATURE_GROUPS_V2,
    "rarity_v3": (
        "term_rarity",
        "max_term_rarity",
        "sole_source_share",
        "identifier_density",
        "distinctiveness",
        "sole_source_failure",
    ),
}
FEATURE_NAMES_V3: tuple[str, ...] = tuple(
    name for names in FEATURE_GROUPS_V3.values() for name in names
)


def extract_features_v3(
    context: RetentionContext, disabled_groups: Sequence[str] = ()
) -> dict[str, tuple[float, ...]]:
    """v2 plus rarity and irreplaceability: how unusual an episode's terms are among the live
    episodes, whether it is the only source of an identifier, and how distinct it is.

    These do not depend on how often an episode was accessed, so an episode that is read rarely
    but holds a fact nothing else holds is not penalised for it.
    """

    unknown = sorted(set(disabled_groups) - set(FEATURE_GROUPS_V3))
    if unknown:
        raise ValueError(
            f"Unknown feature groups {unknown}; the schema {FEATURE_SCHEMA_VERSION_V3} defines "
            f"{sorted(FEATURE_GROUPS_V3)}."
        )
    base = extract_features_v2(context, [g for g in disabled_groups if g in FEATURE_GROUPS_V2])
    masked = "rarity_v3" in disabled_groups
    records = {record.id: record for record in context.records}
    ids = sorted(records)
    terms = {i: _episode_terms(records[i]) for i in ids}
    embeddings = {i: _hashed_embedding(terms[i]) for i in ids}
    document_frequency: dict[str, int] = {}
    for episode_terms in terms.values():
        for term in episode_terms:
            document_frequency[term] = document_frequency.get(term, 0) + 1
    count = len(ids)
    scale = math.log(count + 1) or 1.0
    result: dict[str, tuple[float, ...]] = {}
    for episode_id in ids:
        own = terms[episode_id]
        weights = [math.log((count + 1) / (document_frequency[t] + 0.5)) / scale for t in own]
        identifiers = _identifier_terms(own)
        sole = sum(1 for t in identifiers if document_frequency[t] == 1)
        others = [other for other in ids if other != episode_id]
        similarity = (
            sum(_cosine(embeddings[episode_id], embeddings[other]) for other in others)
            / len(others)
            if others
            else 0.0
        )
        sole_share = _ratio(sole, max(len(identifiers), 1))
        extra = (
            0.0 if masked else _clip01(sum(weights) / len(weights) if weights else 0.0),
            0.0 if masked else _clip01(max(weights, default=0.0)),
            0.0 if masked else sole_share,
            0.0 if masked else _ratio(len(identifiers), max(len(own), 1)),
            0.0 if masked else _clip01(1.0 - similarity),
            0.0 if masked else float(_result_failed(records[episode_id]) and sole > 0),
        )
        result[episode_id] = base[episode_id] + extra
    return result


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


FEATURE_SCHEMAS: dict[
    str,
    tuple[
        tuple[str, ...], Callable[[RetentionContext, Sequence[str]], dict[str, tuple[float, ...]]]
    ],
] = {
    FEATURE_SCHEMA_VERSION: (FEATURE_NAMES, extract_features),
    FEATURE_SCHEMA_VERSION_V2: (FEATURE_NAMES_V2, extract_features_v2),
    FEATURE_SCHEMA_VERSION_V3: (FEATURE_NAMES_V3, extract_features_v3),
}
