"""Offline promotion gate: candidate against incumbent on held-out logged tasks.

Everything is computed from the log. The gate never calls a model and never trusts a single
pooled number: it requires a significant ranking gain, no regression in any task group, optimal
solver certificates, no under-use of the token budget, and, where logged outcomes allow, no loss on
the selections the two models would actually make.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nailong_agent_sdk.foundations.optimization import ExactPckpSolver, PckpItem, PckpProblem
from nailong_agent_sdk.memory.episode_models import CompactionStrategy
from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy, quantize_utilities
from nailong_agent_sdk.memory.utility_models import RankingModel

from ..preferences import PreferencePolicy, build_preferences
from ..stats import mean, sign_test_p
from .dataset import TaskRecord

Scorer = Callable[[TaskRecord], dict[str, float] | None]


@dataclass(frozen=True)
class Scoring:
    name: str
    scorer: Scorer


def static_scoring() -> Scoring:
    static = CompactionStrategy.EXACT_PCKP.value

    def scorer(task: TaskRecord) -> dict[str, float] | None:
        for decision in task.decisions:
            if decision.predictor is None and decision.strategy == static and decision.utilities:
                return dict(decision.utilities)
        return None

    return Scoring("static-pckp", scorer)


def model_scoring(model: RankingModel, name: str) -> Scoring:
    def scorer(task: TaskRecord) -> dict[str, float] | None:
        return {i: float(model.score(task.features[i])) for i in task.episode_ids}

    return Scoring(name, scorer)


@dataclass(frozen=True)
class GatePolicy:
    min_tasks: int = 8
    min_pairs: int = 20
    min_accuracy_gain: float = 0.0
    alpha: float = 0.05
    group_tolerance: float = 0.05
    min_group_pairs: int = 3
    min_budget_use_ratio: float = 0.9
    matched_tolerance: float = 0.0
    min_matched_tasks: int = 5


@dataclass
class GateDecision:
    promote: bool
    reasons: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"promote": self.promote, "reasons": self.reasons, "metrics": self.metrics}


def solve_selection(
    task: TaskRecord, utilities: dict[str, float], quantization: QuantizationPolicy | None = None
):
    quantized, _, _ = quantize_utilities(utilities, quantization or QuantizationPolicy())
    known = set(task.episode_ids)
    problem = PckpProblem(
        token_budget=task.token_budget,
        items=[
            PckpItem(
                item_id=i,
                token_cost=task.token_costs[i],
                utility=quantized[i],
                prerequisites=[d for d in task.depends_on[i] if d in known],
                mandatory=i in task.mandatory_ids,
            )
            for i in task.episode_ids
        ],
    )
    return ExactPckpSolver().solve(problem)


def _margin(
    utilities: dict[str, float], positive: frozenset[str], negative: frozenset[str]
) -> float:
    return sum(utilities[i] for i in positive - negative) - sum(
        utilities[i] for i in negative - positive
    )


def _correct(margin: float) -> float:
    return 1.0 if margin > 0 else 0.5 if margin == 0 else 0.0


def evaluate_gate(
    candidate: Scoring,
    incumbent: Scoring,
    tasks: list[TaskRecord],
    *,
    preference_policy: PreferencePolicy | None = None,
    policy: GatePolicy | None = None,
    quantization: QuantizationPolicy | None = None,
) -> GateDecision:
    policy = policy or GatePolicy()
    preference_policy = preference_policy or PreferencePolicy(min_trials=1)
    reasons: list[str] = []
    quantization = quantization or QuantizationPolicy()
    metrics: dict[str, Any] = {
        "candidate": candidate.name,
        "incumbent": incumbent.name,
        "quantization_floor": quantization.floor,
    }
    wins: list[float] = []
    by_group: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for task in tasks:
        if not task.outcomes:
            continue
        candidate_u, incumbent_u = candidate.scorer(task), incumbent.scorer(task)
        if candidate_u is None or incumbent_u is None:
            continue
        for pair in build_preferences(list(task.outcomes), preference_policy):
            c = _correct(_margin(candidate_u, pair.positive, pair.negative))
            i = _correct(_margin(incumbent_u, pair.positive, pair.negative))
            wins.append(c - i)
            by_group[task.task_group].append((c, i))
    pair_count = len(wins)
    metrics.update(heldout_tasks=len(tasks), heldout_pairs=pair_count)
    if len(tasks) < policy.min_tasks or pair_count < policy.min_pairs:
        reasons.append(
            f"insufficient held-out data: {len(tasks)} tasks (need {policy.min_tasks}) and "
            f"{pair_count} comparable preference pairs (need {policy.min_pairs})"
        )
    if pair_count:
        total_c = sum(c for pairs in by_group.values() for c, _ in pairs) / pair_count
        total_i = sum(i for pairs in by_group.values() for _, i in pairs) / pair_count
        p_value = sign_test_p(wins)
        gain = total_c - total_i
        metrics.update(
            candidate_accuracy=total_c,
            incumbent_accuracy=total_i,
            accuracy_gain=gain,
            sign_test_p=p_value,
            pair_wins=sum(1 for w in wins if w > 0),
            pair_losses=sum(1 for w in wins if w < 0),
        )
        if not (gain > policy.min_accuracy_gain and p_value < policy.alpha):
            reasons.append(
                f"no significant ranking gain: candidate {total_c:.3f} vs incumbent {total_i:.3f} "
                f"(gain {gain:+.3f}, sign-test p {p_value:.3f}; need gain > "
                f"{policy.min_accuracy_gain} and p < {policy.alpha})"
            )
    group_metrics = {}
    for group, pairs in sorted(by_group.items()):
        if len(pairs) < policy.min_group_pairs:
            continue
        c = sum(a for a, _ in pairs) / len(pairs)
        i = sum(b for _, b in pairs) / len(pairs)
        group_metrics[group] = {"pairs": len(pairs), "candidate": c, "incumbent": i}
        if c < i - policy.group_tolerance:
            reasons.append(
                f'regression in task group "{group}": candidate {c:.3f} vs incumbent {i:.3f} '
                f"over {len(pairs)} pairs (tolerance {policy.group_tolerance})"
            )
    metrics["groups"] = group_metrics

    non_optimal, budget_ratios, matched = 0, [], []
    for task in tasks:
        candidate_u, incumbent_u = candidate.scorer(task), incumbent.scorer(task)
        if candidate_u is None or incumbent_u is None:
            continue
        chosen_c = solve_selection(task, candidate_u, quantization)
        chosen_i = solve_selection(task, incumbent_u, quantization)
        if chosen_c.status.value != "optimal":
            non_optimal += 1
        cost_c = sum(task.token_costs[i] for i in chosen_c.selected_item_ids)
        cost_i = sum(task.token_costs[i] for i in chosen_i.selected_item_ids)
        if cost_i > 0:
            budget_ratios.append(cost_c / cost_i)
        scores = {o.selection: mean(list(o.outcomes)) for o in task.outcomes}
        set_c, set_i = frozenset(chosen_c.selected_item_ids), frozenset(chosen_i.selected_item_ids)
        if set_c in scores and set_i in scores:
            matched.append(scores[set_c] - scores[set_i])
    metrics.update(
        non_optimal_solves=non_optimal,
        budget_use_ratio=mean(budget_ratios) if budget_ratios else None,
        matched_tasks=len(matched),
        matched_outcome_difference=mean(matched) if matched else None,
    )
    if non_optimal:
        reasons.append(f"{non_optimal} candidate solves were not proven optimal")
    if budget_ratios and mean(budget_ratios) < policy.min_budget_use_ratio:
        reasons.append(
            f"candidate under-uses the token budget: {mean(budget_ratios):.3f} of the incumbent's "
            f"spend (need at least {policy.min_budget_use_ratio})"
        )
    if len(matched) >= policy.min_matched_tasks and mean(matched) < -policy.matched_tolerance:
        reasons.append(
            f"candidate loses on matched logged outcomes: {mean(matched):+.3f} over "
            f"{len(matched)} tasks (tolerance {policy.matched_tolerance})"
        )
    return GateDecision(not reasons, reasons, metrics)
