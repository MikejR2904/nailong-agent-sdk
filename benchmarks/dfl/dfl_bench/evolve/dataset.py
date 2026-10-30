"""Training and gate data assembled from a retention outcome log. No model calls."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from nailong_agent_sdk.memory.retention_outcomes import RetentionDecision, RetentionOutcomeLog

from ..oracle import MEASURED, SYNTHETIC
from ..preferences import CandidateOutcome

EVIDENCE_KINDS = (SYNTHETIC, MEASURED)


@dataclass(frozen=True)
class TaskRecord:
    task_digest: str
    task_group: str
    token_budget: int
    episode_ids: tuple[str, ...]
    features: dict[str, tuple[float, ...]]
    token_costs: dict[str, int]
    depends_on: dict[str, tuple[str, ...]]
    mandatory_ids: frozenset[str]
    decisions: tuple[RetentionDecision, ...]
    outcomes: tuple[CandidateOutcome, ...]
    feature_schema: str = ""


def split_of(task_digest: str, fractions: tuple[float, float, float] = (0.6, 0.15, 0.25)) -> str:
    position = int(hashlib.sha256(task_digest.encode()).hexdigest()[:8], 16) / 2**32
    if position < fractions[0]:
        return "train"
    return "validation" if position < fractions[0] + fractions[1] else "gate"


def load_tasks(log: RetentionOutcomeLog) -> tuple[list[TaskRecord], str]:
    by_task: dict[str, list[RetentionDecision]] = defaultdict(list)
    for decision in log.decisions():
        by_task[decision.task_digest].append(decision)
    sources: set[str] = set()
    tasks: list[TaskRecord] = []
    for digest, decisions in sorted(by_task.items()):
        first = decisions[0]
        for other in decisions[1:]:
            if other.feature_schema_version != first.feature_schema_version:
                raise ValueError(
                    f'Decisions for task "{digest[:12]}" mix feature schemas '
                    f"{first.feature_schema_version} and {other.feature_schema_version}."
                )
            if other.episode_ids != first.episode_ids or other.features != first.features:
                raise ValueError(
                    f'Decisions for task "{digest[:12]}" disagree on the episode set or features '
                    f"({first.decision_id} vs {other.decision_id}); a task must keep one snapshot."
                )
        scored: dict[frozenset[str], dict] = {}
        for decision in decisions:
            outcomes = log.outcomes(decision.decision_id)
            if not outcomes:
                continue
            sources.update(o.source for o in outcomes)
            entry = scored.setdefault(
                frozenset(decision.selected_ids), {"scores": [], "strategies": []}
            )
            entry["scores"].extend(o.score for o in outcomes)
            entry["strategies"].append(decision.strategy)
        candidates = tuple(
            CandidateOutcome(
                digest,
                selection,
                tuple(dict.fromkeys(entry["strategies"])),
                tuple(entry["scores"]),
                next(iter(sources)) if len(sources) == 1 else "unresolved",
            )
            for selection, entry in sorted(scored.items(), key=lambda kv: sorted(kv[0]))
        )
        tasks.append(
            TaskRecord(
                task_digest=digest,
                task_group=first.task_group,
                token_budget=first.token_budget,
                episode_ids=tuple(first.episode_ids),
                features={k: tuple(v) for k, v in first.features.items()},
                token_costs=dict(first.token_costs),
                depends_on={k: tuple(v) for k, v in first.depends_on.items()},
                mandatory_ids=frozenset(first.mandatory_ids),
                decisions=tuple(decisions),
                outcomes=candidates,
                feature_schema=first.feature_schema_version,
            )
        )
    schemas = {t.feature_schema for t in tasks}
    if len(schemas) > 1:
        raise ValueError(
            f"Refusing to mix feature schemas {sorted(schemas)} in one retraining dataset."
        )
    unknown = sources - set(EVIDENCE_KINDS)
    if unknown:
        raise ValueError(
            f"Outcome sources {sorted(unknown)} are not evidence kinds {EVIDENCE_KINDS}."
        )
    if len(sources) > 1:
        raise ValueError(
            f"Refusing to mix evidence kinds {sorted(sources)} in one retraining dataset; "
            "synthetic and measured outcomes must be trained and gated separately."
        )
    kind = next(iter(sources)) if sources else SYNTHETIC
    final = [
        TaskRecord(
            **{
                **t.__dict__,
                "outcomes": tuple(
                    CandidateOutcome(o.instance_id, o.selection, o.sources, o.outcomes, kind)
                    for o in t.outcomes
                ),
            }
        )
        for t in tasks
    ]
    return final, kind


def dataset_digest(tasks: list[TaskRecord]) -> str:
    digest = hashlib.sha256()
    for task in tasks:
        for outcome in task.outcomes:
            digest.update(task.task_digest.encode())
            digest.update(repr((sorted(outcome.selection), outcome.outcomes)).encode())
    return digest.hexdigest()[:16]
