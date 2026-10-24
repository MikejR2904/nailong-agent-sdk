import itertools
import random

import pytest

from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy, quantize_utilities
from nailong_agent_sdk.memory.tiered_retention import (
    TierPolicy,
    conservative_fidelity,
    document_frequency,
    plan_tiered_retention,
    summarize_episode,
    verify_summary,
)

FILLER = "the quick routine pass completed normally with nothing unusual to report today".split()


def build(seed=1, explore=6, actions=3, words=70):
    rng = random.Random(seed)
    store = InMemoryEpisodeStore()
    ids = []
    for n in range(explore):
        text = " ".join(rng.choice(FILLER) for _ in range(words))
        text += (
            f" error_code E{rng.randint(100, 999)} account acct_{rng.randint(1000, 9999)}"
            f" balance {rng.randint(10, 99)}"
        )
        record = store.open_exploratory(
            "a", content={"result": {"status": "succeeded", "text": text}}
        )
        store.close(record.id, description=f"step {n} lookup")
        ids.append(record.id)
    for _ in range(actions):
        deps = rng.sample(ids[:explore], 2)
        text = " ".join(rng.choice(FILLER) for _ in range(words))
        record = store.open_action(
            "a", deps, content={"result": {"status": "succeeded", "text": text + " ticket_77"}}
        )
        store.close(record.id)
        ids.append(record.id)
    return store


def utilities(store, rng):
    return {r.id: rng.uniform(0.2, 1.0) for r in store.list()}


def context(store, fraction):
    return store._retention_context(int(store.estimate_tokens() * fraction), "error_code account")


def test_summaries_are_grounded_keep_every_identifier_and_are_smaller():
    store = build()
    records = store.list()
    frequency = document_frequency(records)
    for record in records:
        summary = summarize_episode(record, frequency, len(records), 0.3)
        assert verify_summary(summary, record) == []
        assert 0.0 <= summary.fidelity <= 1.0
        assert summary == summarize_episode(record, frequency, len(records), 0.3)
    first = summarize_episode(records[0], frequency, len(records), 0.3)
    assert "acct_" in first.text and "e" in first.text


def test_verify_summary_reports_fabricated_terms_and_lost_identifiers():
    store = build()
    records = store.list()
    frequency = document_frequency(records)
    real = summarize_episode(records[0], frequency, len(records), 0.3)
    forged = type(real)(
        real.episode_id,
        real.text + " invented_9999",
        real.tokens,
        real.kept_terms,
        real.salient_terms,
        real.fidelity,
    )
    assert any("absent from the source" in p for p in verify_summary(forged, records[0]))
    lossy = type(real)(
        real.episode_id,
        "nothing here",
        real.tokens,
        frozenset(),
        real.salient_terms,
        real.fidelity,
        0.0,
    )
    assert any("of the source identifiers" in p for p in verify_summary(lossy, records[0], 0.5))


def test_a_tight_budget_uses_summaries_to_represent_more_episodes_than_binary_retention():
    store = build(seed=2)
    ctx = context(store, 0.45)
    values = utilities(store, random.Random(3))
    binary = plan_tiered_retention(ctx, values, enable_summaries=False)
    tiered = plan_tiered_retention(ctx, values)
    assert binary.summary_ids == ()
    assert tiered.summary_ids
    represented = len(tiered.full_ids) + len(tiered.summary_ids)
    assert represented > len(binary.full_ids)
    assert tiered.token_cost <= ctx.token_budget and binary.token_cost <= ctx.token_budget
    assert 0.0 < tiered.expected_fidelity <= 1.0
    assert tiered.objective >= binary.objective - len(values)
    assert tiered.solver_status == "optimal" and binary.solver_status == "optimal"


def test_the_plan_equals_exhaustive_search_over_tier_assignments():
    store = build(seed=4, explore=3, actions=2, words=60)
    ctx = context(store, 0.5)
    values = utilities(store, random.Random(5))
    plan = plan_tiered_retention(ctx, values)
    records = {r.id: r for r in store.list()}
    quant, _, _ = quantize_utilities(values, QuantizationPolicy())
    summaries = _all_summaries(store, ctx)
    options = {
        i: ["drop", "summary", "full"] if i in summaries else ["drop", "full"] for i in records
    }
    best = -1
    for choice in itertools.product(*(options[i] for i in records)):
        tiers = dict(zip(records, choice, strict=True))
        cost = 0
        value = 0
        feasible = True
        for i, tier in tiers.items():
            if tier == "drop":
                continue
            if i in summaries:
                fid = conservative_fidelity(summaries[i])
                base = quantize_utilities({"x": values[i] * fid}, QuantizationPolicy())[0]["x"]
                rest = quantize_utilities({"x": values[i] * (1 - fid)}, QuantizationPolicy())[0][
                    "x"
                ]
                cost += summaries[i].tokens
                value += base
                if tier == "full":
                    cost += ctx.token_costs[i] - summaries[i].tokens
                    value += max(1, rest)
            else:
                cost += ctx.token_costs[i]
                value += quant[i]
            for d in records[i].depends_on:
                feasible &= tiers.get(d, "drop") != "drop"
        if feasible and cost <= ctx.token_budget:
            best = max(best, value)
    assert plan.objective == best


def _all_summaries(store, ctx):
    records = store.list()
    frequency = document_frequency(records)
    policy = TierPolicy()
    out = {}
    for record in records:
        candidate = summarize_episode(
            record, frequency, len(records), policy.summary_ratio, 0.5, policy.short_value_chars
        )
        cost = ctx.token_costs[record.id]
        if (
            candidate.tokens <= (1 - policy.min_saving_ratio) * cost
            and conservative_fidelity(candidate) >= policy.min_fidelity
            and not verify_summary(candidate, record, policy.min_identifier_recall)
        ):
            out[record.id] = candidate
    return out


def test_prerequisites_are_represented_and_mandatory_episodes_are_kept_in_full():
    store = build(seed=6)
    ctx = context(store, 0.4)
    values = utilities(store, random.Random(7))
    records = {r.id: r for r in store.list()}
    protected = frozenset({next(iter(records))})
    plan = plan_tiered_retention(ctx, values, mandatory_ids=protected)
    for episode_id in protected:
        assert plan.tier_of(episode_id) == "full"
    for episode_id in plan.full_ids + plan.summary_ids:
        for dependency in records[episode_id].depends_on:
            assert plan.tier_of(dependency) != "drop"
    strict = plan_tiered_retention(
        ctx, values, policy=TierPolicy(full_requires_prerequisite_tier="full")
    )
    for episode_id in strict.full_ids:
        for dependency in records[episode_id].depends_on:
            assert strict.tier_of(dependency) == "full"


def test_a_generous_budget_keeps_everything_in_full_and_a_zero_budget_keeps_only_mandatory():
    store = build(seed=8)
    values = utilities(store, random.Random(9))
    plenty = plan_tiered_retention(context(store, 2.0), values)
    assert plenty.dropped_ids == () and plenty.summary_ids == ()
    assert len(plenty.full_ids) == len(values)
    assert plenty.expected_fidelity == pytest.approx(1.0)
    none = plan_tiered_retention(context(store, 0.0), values)
    assert none.full_ids == () and none.summary_ids == ()


def test_missing_utilities_fail_by_name_and_planning_is_deterministic():
    store = build(seed=10)
    ctx = context(store, 0.5)
    values = utilities(store, random.Random(11))
    partial = dict(list(values.items())[:-1])
    with pytest.raises(ValueError, match="No utility for live episodes"):
        plan_tiered_retention(ctx, partial)
    first = plan_tiered_retention(ctx, values)
    second = plan_tiered_retention(ctx, values)
    assert (first.full_ids, first.summary_ids, first.objective) == (
        second.full_ids,
        second.summary_ids,
        second.objective,
    )


def test_isotonic_calibration_is_monotone_interpolates_and_is_integrity_checked():
    from nailong_agent_sdk.memory.tiered_retention import FidelityCalibration

    pairs = [(0.1, 0.0), (0.2, 0.1), (0.4, 0.05), (0.6, 0.5), (0.8, 0.9), (1.0, 0.8), (1.0, 1.0)]
    calibration = FidelityCalibration.fit(pairs)
    mapped = [calibration.apply(x / 20) for x in range(21)]
    assert all(b >= a - 1e-12 for a, b in zip(mapped, mapped[1:], strict=False))
    assert 0.0 <= min(mapped) and max(mapped) <= 1.0
    assert calibration.apply(-1.0) == calibration.knots_y[0]
    assert calibration.apply(2.0) == calibration.knots_y[-1]
    assert calibration.expected_calibration_error(pairs) < 0.2
    restored = FidelityCalibration.from_json(calibration.to_json())
    assert restored == calibration
    tampered = {**calibration.to_json(), "samples": 999}
    with pytest.raises(ValueError, match="integrity digest"):
        FidelityCalibration.from_json(tampered)
    with pytest.raises(ValueError, match="at least one"):
        FidelityCalibration.fit([])
    with pytest.raises(ValueError, match="must lie in"):
        FidelityCalibration.fit([(1.5, 0.2)])


def test_a_pessimistic_calibration_stops_the_planner_from_trusting_summaries():
    from nailong_agent_sdk.memory.tiered_retention import FidelityCalibration

    store = build(seed=2)
    ctx = context(store, 0.45)
    values = utilities(store, random.Random(3))
    trusting = plan_tiered_retention(ctx, values)
    sceptical = plan_tiered_retention(
        ctx, values, calibration=FidelityCalibration.fit([(0.0, 0.0), (1.0, 0.1)])
    )
    assert trusting.summary_ids
    assert sceptical.summary_ids == ()
    assert sceptical.token_cost <= ctx.token_budget
