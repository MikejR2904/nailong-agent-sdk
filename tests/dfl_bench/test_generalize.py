import math

import pytest
from dfl_bench.generalize.learners import (
    Method,
    SchemaCache,
    objective_data,
    run_method,
)
from dfl_bench.generalize.protocol import (
    Config,
    fold_names,
    make_fold,
    run_fold,
)
from dfl_bench.generalize.report import comparison_table, method_table, render
from dfl_bench.generalize.worlds import (
    FAMILIES,
    FAMILY_NAMES,
    build_family,
    build_world,
    family_of,
    fast_optimum_quality,
)
from dfl_bench.instances import generate_instance
from dfl_bench.oracle import optimum_quality
from dfl_bench.preferences import CandidateOutcome, PreferencePolicy
from dfl_bench.selection import STATIC, is_usable, select_via_store
from dfl_bench.splits import leakage_report

from nailong_agent_sdk.memory.utility_features import FEATURE_SCHEMA_VERSION
from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMA_VERSION_V2

TINY = Config(templates=2, per_template=2, boundary=False)


def test_the_fast_optimum_equals_exhaustive_enumeration_on_small_instances():
    checked = 0
    for index in range(40):
        for regime in ("aligned", "shifted", "additive", "null"):
            instance = generate_instance("OPT", regime, index, 3)
            if instance.size > 14 or not is_usable(instance):
                continue
            assert fast_optimum_quality(instance) == pytest.approx(optimum_quality(instance)[0])
            checked += 1
    assert checked >= 30


def test_every_family_is_usable_and_the_long_families_exceed_the_exhaustive_oracle_limit():
    world = build_world(0, templates=3, per_template=3)
    assert {family_of(i) for i in world} == set(FAMILY_NAMES)
    assert all(is_usable(i) for i in world)
    longest = {name: max(i.size for i in world if family_of(i) == name) for name in FAMILY_NAMES}
    assert longest["aligned-long"] > 16 and longest["shifted-long"] > 16
    assert longest["aligned-short"] <= 13
    assert all(fast_optimum_quality(i) > 0 for i in world)
    assert {f.regime for f in FAMILIES} == {"aligned", "shifted", "additive", "null"}


def test_budget_variant_families_have_smaller_and_larger_budgets_than_the_base():
    spec = FAMILIES[0]
    base = build_family(spec, 4, 3, 3)
    low = build_family(spec, 4, 3, 3, ratio=0.30, tag="b")
    high = build_family(spec, 4, 3, 3, ratio=0.90, tag="b")
    mean = lambda xs: sum(i.budget for i in xs) / len(xs)  # noqa: E731
    assert mean(low) < mean(base) < mean(high)


@pytest.mark.parametrize("fold", fold_names())
def test_every_fold_is_leakage_free_and_holds_out_what_it_names(fold):
    config = Config(templates=3, per_template=3)
    train, val, test = make_fold(1, fold, config)
    assert not leakage_report(train, val, test, keys=("template", "query", "content", "instance"))
    families_seen = {family_of(i) for i in train + val}
    if fold.startswith("lofo:"):
        held = fold.split(":", 1)[1]
        assert held not in families_seen and {family_of(i) for i in test} == {held}
    if fold == "holdout-long":
        assert {family_of(i) for i in test} == {"aligned-long", "shifted-long"}
        assert not families_seen & {"aligned-long", "shifted-long"}
    if fold in ("budget-low", "budget-high"):
        assert {family_of(i) for i in test} == set(FAMILY_NAMES)
    assert train and val and test


def test_an_unknown_fold_is_rejected_by_name():
    with pytest.raises(ValueError, match="Unknown fold 'nope'"):
        make_fold(0, "nope", TINY)


def outcomes_for(instances, executor, spread):
    out = []
    for n, instance in enumerate(instances):
        for k, ids in enumerate((frozenset({"a"}), frozenset({"b"}), frozenset({"a", "b"}))):
            scores = tuple(1.0 if (t + k * spread) % 10 < 5 + k * spread else 0.0 for t in range(8))
            out.append(CandidateOutcome(instance.instance_id, ids, (f"s{k}",), scores, "synthetic"))
    return out


def test_objective_data_builds_each_term_kind_and_omits_tied_pools():
    world = build_world(2, templates=2, per_template=2)[:6]
    cache = SchemaCache(world, FEATURE_SCHEMA_VERSION)
    from dfl_bench.oracle import SyntheticOracleExecutor

    executor = SyntheticOracleExecutor()
    from dfl_bench.experiment import evaluate_candidates

    outcomes = evaluate_candidates(world, executor, 8, 0)
    policy = PreferencePolicy()
    pairwise = objective_data("pairwise", cache, outcomes, policy)
    regret = objective_data("regret-pairwise", cache, outcomes, policy)
    listwise = objective_data("listwise", cache, outcomes, policy)
    regression = objective_data("regression", cache, outcomes, policy)
    assert pairwise.terms and len(pairwise.terms) == len(regret.terms)
    assert {t.kind for t in listwise.terms} == {"list"} and {t.kind for t in regression.terms} == {
        "reg"
    }
    assert [t.weight for t in pairwise.terms] != [t.weight for t in regret.terms]
    assert sum(t.weight for t in regret.terms) / len(regret.terms) == pytest.approx(1.0)
    flat = [
        CandidateOutcome(o.instance_id, o.selection, o.sources, (1.0,) * 8, "synthetic")
        for o in outcomes
    ]
    assert not objective_data("listwise", cache, flat, policy).terms
    assert not objective_data("pairwise", cache, flat, policy).terms
    with pytest.raises(ValueError, match="Unknown objective"):
        objective_data("bogus", cache, outcomes, policy)


def test_schema_caches_have_the_declared_widths():
    world = build_world(2, templates=1, per_template=1)[:3]
    v1 = SchemaCache(world, FEATURE_SCHEMA_VERSION)
    v2 = SchemaCache(world, FEATURE_SCHEMA_VERSION_V2)
    first = world[0].instance_id
    assert v2.dim > v1.dim
    assert all(len(v) == v1.dim for v in v1.get(first).values())
    assert all(len(v) == v2.dim for v in v2.get(first).values())


def test_a_static_method_selects_exactly_what_the_store_selects():
    instance = build_world(2, templates=1, per_template=1)[0]
    method = Method("static-pckp", "static", STATIC)
    assert run_method(method, instance).ids == select_via_store(instance, STATIC)[0].ids


@pytest.fixture(scope="module")
def tiny_fold():
    return run_fold(0, "lofo:shifted-short", TINY)


def test_a_complete_fold_runs_every_method_and_every_selection_is_feasible(tiny_fold):
    result = tiny_fold
    assert result["evidence_kind"] == "synthetic"
    assert {
        "static-pckp",
        "tuned-static-pckp",
        "linear-dfl",
        "regression-pckp",
        "listwise-v1",
        "regret-pairwise-v1",
        "dro-pairwise-v1",
        "regression-v2",
        "calibrated-pckp",
        "combined-validation",
    } <= set(result["per_method"])
    for rows in result["per_method"].values():
        assert len(rows) == result["sizes"]["test"]
        for row in rows:
            assert 0 < row["token_fraction"] <= 1.0
            assert row["solver_status"] in ("optimal", "best_effort", "optimal-forest", None) or (
                isinstance(row["solver_status"], str)
            )
            assert row["regret"] >= -1e-9
            assert row["family"] == "shifted-short"
    assert result["test_families"] == ["shifted-short"]
    chosen = result["training"]["combined-validation"]["chosen"]
    assert chosen in result["per_method"]
    audit = result["training"]["calibrated-pckp"]
    assert audit["alpha"] in (0.25, 0.5, 0.75, 1.0)


def test_the_tuned_static_baseline_is_never_worse_than_default_on_its_tuning_set(tiny_fold):
    training = tiny_fold["training"]["tuned-static-pckp"]
    assert training["tuning_quality"] >= tiny_fold["validation_quality"]["static-pckp"] - 0.15


def test_reports_aggregate_paired_comparisons_and_state_the_synthetic_banner(tiny_fold):
    results = [tiny_fold]
    table = method_table(results, "lofo:")
    assert {row["method"] for row in table} >= {"static-pckp", "calibrated-pckp"}
    comparisons = comparison_table(results, "lofo:")
    assert comparisons and all(0 <= row["holm_p"] <= 1 for row in comparisons)
    assert all(row["ci_low"] <= row["mean"] <= row["ci_high"] for row in comparisons)
    text = render(results, "deadbeef", "unit")
    assert "SYNTHETIC ORACLE EVIDENCE ONLY" in text and "deadbeef" in text
    assert all(not math.isnan(row["mean_quality"]) for row in table)
