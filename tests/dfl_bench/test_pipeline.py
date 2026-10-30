import pytest
from dfl_bench.experiment import ExperimentConfig, run_replicate
from dfl_bench.perf import run_latency, run_scale
from dfl_bench.report import render, summarize


def tiny(seed=1, regime="aligned", **overrides):
    values = {
        "regime": regime,
        "seed": seed,
        "templates": 6,
        "per_template": 3,
        "train_trials": 6,
        "eval_trials": 10,
        "rounds": 2,
        "run_mlp": False,
        "run_ablations": False,
    }
    return ExperimentConfig(**{**values, **overrides})


@pytest.fixture(scope="module")
def replicates():
    return [run_replicate(tiny(seed=1)), run_replicate(tiny(seed=2))]


def test_replicate_is_deterministic_and_stamped_synthetic(replicates):
    again = run_replicate(tiny(seed=1))
    assert replicates[0]["evidence_kind"] == "synthetic"
    assert replicates[0]["sizes"] == again["sizes"]
    for method, rows in replicates[0]["per_method"].items():
        assert [r["quality"] for r in rows] == [r["quality"] for r in again["per_method"][method]]


def test_every_method_returns_feasible_selections_and_certified_solvers(replicates):
    result = replicates[1]
    for rows in result["per_method"].values():
        assert rows
        for row in rows:
            assert 0 < row["token_fraction"] <= 1.0
            assert 0.03 - 1e-12 <= row["quality"] <= 0.95 + 1e-12 and row["regret"] >= -1e-12
    for method in ("static-pckp", "linear-dfl", "regression-pckp"):
        assert {r["solver_status"] for r in result["per_method"][method]} == {"optimal"}
        assert {r["optimality_gap"] for r in result["per_method"][method]} == {"0"}
    assert {r["learned_status"] for r in result["per_method"]["linear-dfl"]} == {"applied"}


def test_summary_and_tables_carry_the_banner_and_clustered_statistics(replicates):
    summary = summarize(replicates)
    text = render("aligned", replicates, summary)
    assert "SYNTHETIC GROUND TRUTH" in text and "Template-clustered" in text
    size = summary["methods"]["static-pckp"]["n_instances"]
    for r in summary["paired_vs_static"].values():
        assert r["n"] == size
        assert 0.0 <= r["holm_p"] <= 1.0 and 0.0 < r["cluster_p"] <= 1.0
        assert r["cluster_ci_low"] <= r["mean_difference"] <= r["cluster_ci_high"]
    assert set(summary["paired_among_learned"]) == {
        "linear-dfl - regression-pckp",
        "linear-dfl - topk-repair",
    }
    assert set(summary["pair_accuracy"]) >= {"static-pckp", "linear-dfl"}


def test_ablations_report_every_feature_group_with_intervals():
    runs = [run_replicate(tiny(seed=s, run_ablations=True)) for s in (5, 6)]
    groups = set(runs[0]["ablations"]["feature_groups"])
    assert groups == {"task", "graph", "provenance", "dynamics", "outcome"}
    assert set(runs[0]["ablations"]["training_size"]) == {"0.25", "0.5", "1.0"}
    effects = summarize(runs)["ablations"]
    for effect in effects["feature_groups"].values():
        assert effect["ci_low"] <= effect["diff_vs_full"] <= effect["ci_high"]


@pytest.mark.parametrize("regime", ["null", "additive"])
def test_control_regimes_run_a_full_replicate(regime):
    result = run_replicate(tiny(seed=11, regime=regime, templates=5))
    assert result["evidence_kind"] == "synthetic" and result["sizes"]["test_instances"] > 0


def test_scale_and_latency_reports_have_certificates_and_percentiles():
    row = run_scale(sizes=(12,), per_size=2)["sizes"]["12"]["static-pckp"]
    assert row["status_counts"] == {"optimal": 2} and row["max_relative_gap"] == 0.0
    latency = run_latency(size=10, instances=2, repeats=2, warmup=1)
    assert set(latency["methods"]) == {"static-pckp", "greedy-pask", "linear-dfl", "mlp-dfl"}
    for stats in latency["methods"].values():
        assert stats["p10_ms"] <= stats["median_ms"] <= stats["p90_ms"]
