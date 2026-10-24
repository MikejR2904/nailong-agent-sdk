# Copyright (c) 2026 David Michael Indraputra

"""Three-level retention: keep an episode in full, keep a grounded summary, or drop it.

The choice is made jointly under the token budget by the exact PCKP solver, not after it. An
episode with a useful summary becomes two items: a *base* item (the summary: cost ``s``, utility
``f*u``) and an *upgrade* item (the rest of the original: cost ``c - s``, utility ``(1-f)*u``) that
requires the base. Selecting both costs ``c`` and is worth ``u``, so any keep-or-drop selection
stays feasible and the tiered optimum is never below the binary one (up to quantisation rounding).
``f`` is the measured fidelity of the summary. The upgrade is worth at least one unit, so slack
budget always buys the fuller text.

Summaries are extractive and deterministic: they quote the source, keep every identifier-like term
verbatim, and add the highest-information fragments that fit. Nothing is generated, so a summary
cannot introduce a fact that is not in the episode.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.hashing import estimate_tokens, sha256_hex, strict_canonical_json
from ..foundations.optimization import ExactPckpSolver, PckpItem, PckpProblem
from .episode_models import EpisodeRecord
from .episode_scoring import _terms
from .learned_retention import QuantizationPolicy, quantize_utilities
from .semantic_compaction import record_corpus
from .utility_features import RetentionContext

Tier = Literal["full", "summary", "drop"]
_SEGMENT = re.compile(r"[^.;\n{}\[\],]+")


class TierPolicy(StrictModel):
    summary_ratio: float = Field(default=0.3, gt=0, lt=1)
    min_saving_ratio: float = Field(default=0.3, gt=0, lt=1)
    min_fidelity: float = Field(default=0.2, ge=0, le=1)
    min_identifier_recall: float = Field(default=0.6, ge=0, le=1)
    short_value_chars: int = Field(default=48, ge=8)
    full_requires_prerequisite_tier: Literal["summary", "full"] = "full"


@dataclass(frozen=True)
class EpisodeSummary:
    episode_id: str
    text: str
    tokens: int
    kept_terms: frozenset[str]
    salient_terms: frozenset[str]
    fidelity: float
    identifier_recall: float = 1.0
    field_recall: float = 1.0


@dataclass(frozen=True)
class TieredPlan:
    full_ids: tuple[str, ...]
    summary_ids: tuple[str, ...]
    dropped_ids: tuple[str, ...]
    summaries: Mapping[str, EpisodeSummary]
    token_cost: int
    solver_status: str
    optimality_gap: str
    objective: int
    expected_fidelity: float
    audit: dict[str, object] = field(default_factory=dict)

    def tier_of(self, episode_id: str) -> Tier:
        if episode_id in self.full_ids:
            return "full"
        return "summary" if episode_id in self.summary_ids else "drop"


def is_identifier(term: str) -> bool:
    return any(c.isdigit() for c in term) or "_" in term


def document_frequency(records: list[EpisodeRecord]) -> dict[str, int]:
    frequency: dict[str, int] = {}
    for record in records:
        for term in _terms(record_corpus(record)):
            frequency[term] = frequency.get(term, 0) + 1
    return frequency


def term_information(term: str, frequency: Mapping[str, int], count: int) -> float:
    return math.log((count + 1) / (frequency.get(term, 0) + 0.5)) / (math.log(count + 1) or 1.0)


_BOOKKEEPING = frozenset({"id", "consumed_episode_ids", "depends_on_call_ids", "failure"})


def _leaves(value: object, key: str = "") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        found: list[tuple[str, str]] = []
        for name, child in value.items():
            if name not in _BOOKKEEPING:
                found.extend(_leaves(child, str(name)))
        return found
    if isinstance(value, list | tuple):
        return [leaf for child in value for leaf in _leaves(child, key)]
    if value is None or value == "":
        return []
    return [(key, str(value))]


def _header(record: EpisodeRecord) -> str:
    content = record.content if isinstance(record.content, Mapping) else {}
    call = content.get("call")
    result = content.get("result")
    tool = call.get("name") if isinstance(call, Mapping) else None
    status = result.get("status") if isinstance(result, Mapping) else None
    return f"{tool or record.kind.value} {status or ''}".strip()


def summarize_episode(
    record: EpisodeRecord,
    frequency: Mapping[str, int],
    count: int,
    summary_ratio: float,
    salient_threshold: float = 0.5,
    short_value_chars: int = 48,
) -> EpisodeSummary:
    """Structure-aware extractive summary within ``summary_ratio`` of the full token cost.

    Short scalar fields (names, codes, numbers, queues) are kept as ``key=value``; long prose is
    reduced to a prefix plus the identifiers inside it. Fragments are chosen greedily by information
    per token, where information is the inverse document frequency of the salient terms a fragment
    adds. Everything is quoted from the source.
    """

    corpus = record_corpus(record)
    full_tokens = max(1, estimate_tokens({"text": corpus}))
    leaves = _leaves(record.content) if record.content is not None else []
    if not leaves and record.description:
        leaves = [("description", record.description)]
    source_terms = _terms(corpus)
    leaf_terms: set[str] = set()
    for _, value in leaves:
        leaf_terms |= _terms(value)
    salient = frozenset(
        t
        for t in leaf_terms
        if is_identifier(t) or term_information(t, frequency, count) >= salient_threshold
    )
    required = frozenset(t for t in leaf_terms if is_identifier(t))

    fragments: list[str] = []
    for key, value in leaves:
        if len(value) <= short_value_chars:
            fragments.append(f"{key}={value}")
        else:
            codes = sorted(t for t in _terms(value) if is_identifier(t))
            prefix = value[:40]
            if value[40] != " ":
                prefix = prefix.rsplit(" ", 1)[0]
            prefix = prefix.rstrip()
            fragments.append(f"{key}={prefix} ..." + (" " + " ".join(codes) if codes else ""))
    fragments = list(dict.fromkeys(fragments))
    header = _header(record)
    limit = max(1, int(full_tokens * summary_ratio))

    def tokens_of(items: list[str]) -> int:
        return estimate_tokens({"text": " | ".join(items)})

    chosen: list[str] = []
    covered = set(_terms(header))
    remaining = list(fragments)

    def gain(fragment: str) -> float:
        return sum(
            term_information(t, frequency, count) + (1.0 if is_identifier(t) else 0.0)
            for t in _terms(fragment) - covered
            if t in salient
        )

    while remaining:
        best = max(remaining, key=lambda s: (gain(s) / max(tokens_of([s]), 1), s))
        remaining.remove(best)
        if gain(best) <= 0:
            continue
        if tokens_of([header, *chosen, best]) > limit:
            continue
        chosen.append(best)
        covered |= _terms(best)
    ordered = [f for f in fragments if f in chosen]
    parts = [header, *ordered] if header else ordered
    text = " | ".join(parts)
    kept = frozenset(_terms(text)) & source_terms
    total = sum(term_information(t, frequency, count) + 1.0 for t in salient)
    preserved = sum(term_information(t, frequency, count) + 1.0 for t in salient if t in kept)
    fidelity = preserved / total if total else 1.0
    recall = len(required & kept) / len(required) if required else 1.0
    summary_terms = _terms(text)
    short_values = [v for _, v in leaves if len(v) <= short_value_chars]
    field_recall = (
        sum(1 for v in short_values if _terms(v) <= summary_terms) / len(short_values)
        if short_values
        else 1.0
    )
    return EpisodeSummary(
        record.id,
        text,
        tokens_of(parts),
        kept,
        salient,
        min(1.0, max(0.0, fidelity)),
        recall,
        field_recall,
    )


def verify_summary(
    summary: EpisodeSummary, record: EpisodeRecord, min_identifier_recall: float = 0.0
) -> list[str]:
    """Reasons a summary is not acceptable; empty means grounded, smaller and complete enough."""

    problems: list[str] = []
    source_terms = _terms(record_corpus(record))
    foreign = sorted(_terms(summary.text) - source_terms - _terms(_header(record)))
    if foreign:
        problems.append(f"summary contains terms absent from the source: {foreign[:5]}")
    if summary.identifier_recall < min_identifier_recall:
        problems.append(
            f"summary kept {summary.identifier_recall:.2f} of the source identifiers "
            f"(need {min_identifier_recall:.2f})"
        )
    if summary.tokens >= max(1, estimate_tokens({"text": record_corpus(record)})):
        problems.append("summary is not smaller than the source")
    return problems


def conservative_fidelity(summary: EpisodeSummary) -> float:
    """The lower of the information-weighted fidelity and the plain share of short fields kept.

    Any short field may be the one a question asks for, so dropping fields is penalised even when
    the dropped terms look uninformative.
    """

    return min(summary.fidelity, summary.field_recall)


@dataclass(frozen=True)
class FidelityCalibration:
    """Monotone map from a summary's conservative fidelity to the measured rate at which the facts
    of its episode survive in the summary (isotonic regression, linear between knots)."""

    knots_x: tuple[float, ...]
    knots_y: tuple[float, ...]
    samples: int

    @classmethod
    def fit(cls, pairs: list[tuple[float, float]]) -> FidelityCalibration:
        if not pairs:
            raise ValueError("Calibration needs at least one (fidelity, survival) pair.")
        for x, y in pairs:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"Pair ({x}, {y}) must lie in [0, 1].")
        grouped: dict[float, list[float]] = {}
        for x, y in pairs:
            grouped.setdefault(x, []).append(y)
        blocks: list[list[float]] = []
        for x in sorted(grouped):
            blocks.append([x, sum(grouped[x]) / len(grouped[x]), float(len(grouped[x])), x])
            while len(blocks) > 1 and blocks[-2][1] > blocks[-1][1]:
                right = blocks.pop()
                left = blocks.pop()
                weight = left[2] + right[2]
                blocks.append(
                    [
                        (left[0] * left[2] + right[0] * right[2]) / weight,
                        (left[1] * left[2] + right[1] * right[2]) / weight,
                        weight,
                        right[3],
                    ]
                )
        return cls(
            tuple(block[0] for block in blocks),
            tuple(block[1] for block in blocks),
            len(pairs),
        )

    def apply(self, fidelity: float) -> float:
        xs, ys = self.knots_x, self.knots_y
        if fidelity <= xs[0]:
            return ys[0]
        if fidelity >= xs[-1]:
            return ys[-1]
        for k in range(1, len(xs)):
            if fidelity <= xs[k]:
                span = xs[k] - xs[k - 1]
                weight = (fidelity - xs[k - 1]) / span if span else 1.0
                return ys[k - 1] + weight * (ys[k] - ys[k - 1])
        return ys[-1]

    def expected_calibration_error(self, pairs: list[tuple[float, float]], bins: int = 5) -> float:
        """Mean absolute gap between the mapped fidelity and the observed survival, by bin."""

        if not pairs:
            return float("nan")
        total = 0.0
        for index in range(bins):
            low, high = index / bins, (index + 1) / bins
            members = [
                (x, y) for x, y in pairs if low <= x < high or (index == bins - 1 and x == 1.0)
            ]
            if members:
                predicted = sum(self.apply(x) for x, _ in members) / len(members)
                observed = sum(y for _, y in members) / len(members)
                total += len(members) * abs(predicted - observed)
        return total / len(pairs)

    def to_json(self) -> dict[str, object]:
        body = {
            "knots_x": list(self.knots_x),
            "knots_y": list(self.knots_y),
            "samples": self.samples,
        }
        return {**body, "digest": sha256_hex(strict_canonical_json(body))}

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> FidelityCalibration:
        body = {k: data[k] for k in ("knots_x", "knots_y", "samples")}
        if data.get("digest") != sha256_hex(strict_canonical_json(body)):
            raise ValueError("Fidelity calibration failed its integrity digest.")
        return cls(
            tuple(float(x) for x in body["knots_x"]),  # type: ignore[attr-defined]
            tuple(float(y) for y in body["knots_y"]),  # type: ignore[attr-defined]
            int(body["samples"]),  # type: ignore[call-overload]
        )


def plan_tiered_retention(
    context: RetentionContext,
    utilities: Mapping[str, float],
    quantization: QuantizationPolicy | None = None,
    policy: TierPolicy | None = None,
    mandatory_ids: frozenset[str] = frozenset(),
    enable_summaries: bool = True,
    calibration: FidelityCalibration | None = None,
) -> TieredPlan:
    """Choose full, summary or drop for every live episode under ``context.token_budget``."""

    policy = policy or TierPolicy()
    quantization = quantization or QuantizationPolicy()
    records = {r.id: r for r in context.records}
    missing = sorted(set(records) - set(utilities))
    if missing:
        raise ValueError(f"No utility for live episodes {missing[:5]}.")
    quantized, _, _ = quantize_utilities({i: utilities[i] for i in records}, quantization)
    frequency = document_frequency(list(records.values()))
    count = len(records)
    summaries: dict[str, EpisodeSummary] = {}
    fidelity_used: dict[str, float] = {}
    if enable_summaries:
        for episode_id, record in records.items():
            cost = context.token_costs[episode_id]
            candidate = summarize_episode(
                record, frequency, count, policy.summary_ratio, 0.5, policy.short_value_chars
            )
            effective = conservative_fidelity(candidate)
            if calibration is not None:
                effective = calibration.apply(effective)
            if (
                episode_id not in mandatory_ids
                and candidate.tokens <= (1 - policy.min_saving_ratio) * cost
                and effective >= policy.min_fidelity
                and not verify_summary(candidate, record, policy.min_identifier_recall)
            ):
                summaries[episode_id] = candidate
                fidelity_used[episode_id] = effective
    items: list[PckpItem] = []
    base_id = {i: (f"{i}#S" if i in summaries else f"{i}#F") for i in records}
    upgrade_id = {i: f"{i}#U" for i in summaries}
    for episode_id, record in records.items():
        cost = context.token_costs[episode_id]
        raw = float(utilities[episode_id])
        prerequisites = [base_id[d] for d in record.depends_on if d in records]
        mandatory = episode_id in mandatory_ids
        if episode_id in summaries:
            summary = summaries[episode_id]
            fidelity = fidelity_used[episode_id]
            base_u, _, _ = quantize_utilities({"x": raw * fidelity}, quantization)
            up_u, _, _ = quantize_utilities({"x": raw * (1.0 - fidelity)}, quantization)
            up_u["x"] = max(1, up_u["x"])
            items.append(
                PckpItem(
                    item_id=base_id[episode_id],
                    token_cost=summary.tokens,
                    utility=base_u["x"],
                    prerequisites=prerequisites,
                    mandatory=mandatory,
                )
            )
            upgrade_prereq = [base_id[episode_id]]
            if policy.full_requires_prerequisite_tier == "full":
                upgrade_prereq += [
                    upgrade_id.get(d, base_id[d]) for d in record.depends_on if d in records
                ]
            items.append(
                PckpItem(
                    item_id=upgrade_id[episode_id],
                    token_cost=max(0, cost - summary.tokens),
                    utility=up_u["x"],
                    prerequisites=list(dict.fromkeys(upgrade_prereq)),
                    mandatory=mandatory,
                )
            )
        else:
            items.append(
                PckpItem(
                    item_id=base_id[episode_id],
                    token_cost=cost,
                    utility=quantized[episode_id],
                    prerequisites=prerequisites,
                    mandatory=mandatory,
                )
            )
    solution = ExactPckpSolver().solve(PckpProblem(token_budget=context.token_budget, items=items))
    chosen = set(solution.selected_item_ids)
    full, partial, dropped = [], [], []
    for episode_id in sorted(records):
        if episode_id in summaries:
            if upgrade_id[episode_id] in chosen:
                full.append(episode_id)
            elif base_id[episode_id] in chosen:
                partial.append(episode_id)
            else:
                dropped.append(episode_id)
        elif base_id[episode_id] in chosen:
            full.append(episode_id)
        else:
            dropped.append(episode_id)
    represented = [*full, *partial]
    weights = {i: max(float(utilities[i]), 0.0) for i in represented}
    total_weight = sum(weights.values())
    expected = (
        sum(weights[i] * (1.0 if i in full else fidelity_used[i]) for i in represented)
        / total_weight
        if total_weight
        else 1.0
    )
    return TieredPlan(
        tuple(full),
        tuple(partial),
        tuple(dropped),
        {i: summaries[i] for i in partial},
        solution.token_cost,
        solution.status.value,
        solution.optimality_gap,
        solution.utility,
        expected,
        {
            "summarizable": len(summaries),
            "episodes": len(records),
            "guarantee": "optimal for the quantized tiered objective only; fidelity is the "
            "information-weighted share of salient terms preserved, not a model-measured score",
        },
    )
