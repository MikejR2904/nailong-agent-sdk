from dfl_bench.experiment import ExperimentConfig, run_replicate
from dfl_bench.report import render, summarize


def tiny(seed=1, regime="aligned"):
    return ExperimentConfig(
        regime=regime,
        seed=seed,
        templates=6,
        per_template=3,
        train_trials=6,
        eval_trials=10,
        rounds=2,
        run_mlp=False,
        run_ablations=False,
    )


def test_replicate_is_deterministic_and_stamped_synthetic():
    first = run_replicate(tiny())
    second = run_replicate(tiny())
    assert first["evidence_kind"] == "synthetic"
    for method, rows in first["per_method"].items():
        assert [r["quality"] for r in rows] == [r["quality"] for r in second["per_method"][method]]
    assert first["sizes"] == second["sizes"]


def test_every_method_returns_feasible_selections_and_certified_solvers():
    result = run_replicate(tiny(seed=2))
    for method, rows in result["per_method"].items():
        assert rows
        for row in rows:
            assert 0 < row["token_fraction"] <= 1.0
            assert 0.03 - 1e-12 <= row["quality"] <= 0.95 + 1e-12 and row["regret"] >= -1e-12
    for method in ("static-pckp", "linear-dfl", "regression-pckp"):
        assert {r["solver_status"] for r in result["per_method"][method]} == {"optimal"}
        assert {r["optimality_gap"] for r in result["per_method"][method]} == {"0"}
    assert {r["learned_status"] for r in result["per_method"]["linear-dfl"]} == {"applied"}


def test_summary_and_tables_render_with_the_synthetic_banner():
    replicates = [run_replicate(tiny(seed=3)), run_replicate(tiny(seed=4))]
    summary = summarize(replicates)
    text = render("aligned", replicates, summary)
    assert "SYNTHETIC GROUND TRUTH" in text and "Holm p" in text
    for r in summary["paired_vs_static"].values():
        assert (
            0.0 <= r["holm_p"] <= 1.0 and r["n"] == summary["methods"]["static-pckp"]["n_instances"]
        )
    assert set(summary["pair_accuracy"]) >= {"static-pckp", "linear-dfl"}


def test_ablations_report_every_feature_group():
    config = tiny(seed=5)
    config.run_ablations = True
    result = run_replicate(config)
    groups = set(result["ablations"]["feature_groups"])
    assert groups == {"task", "graph", "provenance", "dynamics", "outcome"}
    assert set(result["ablations"]["training_size"]) == {"0.25", "0.5", "1.0"}
