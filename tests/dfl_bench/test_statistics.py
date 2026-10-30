import pytest
from dfl_bench.report import primary_hypotheses
from dfl_bench.stats import cluster_bootstrap, template_of


def test_cluster_bootstrap_detects_a_consistent_effect_and_a_null():
    positive = {f"t{i}": [0.1, 0.12] for i in range(12)}
    result = cluster_bootstrap(positive, resamples=500)
    assert result["ci_low"] > 0 and result["p"] < 0.01 and result["clusters"] == 12
    symmetric = {f"t{i}": [0.1 * (-1) ** i] for i in range(12)}
    assert cluster_bootstrap(symmetric, resamples=500)["p"] > 0.5
    with pytest.raises(ValueError, match="at least one cluster"):
        cluster_bootstrap({})


def test_cluster_resampling_covers_zero_when_clusters_disagree():
    clustered = {f"t{i}": [0.2 * (-1) ** i] * 6 for i in range(10)}
    result = cluster_bootstrap(clustered, resamples=800)
    assert result["ci_low"] < 0 < result["ci_high"] and result["n"] == 60


def test_template_of_strips_regime_and_index():
    assert template_of("T3000x4-shifted-2") == "T3000x4"


def summary_with(p, diff):
    return {
        "summary": {
            "paired_vs_static": {
                m: {
                    "mean_difference": diff,
                    "cluster_ci_low": diff - 0.01,
                    "cluster_ci_high": diff + 0.01,
                    "cluster_p": p,
                }
                for m in ("linear-dfl", "mlp-dfl")
            }
        }
    }


def test_primary_family_applies_holm_over_the_four_cells():
    out = primary_hypotheses(
        {"shifted": summary_with(0.001, 0.2), "aligned": summary_with(0.03, 0.05)}
    )
    assert set(out) == {
        f"{r}/{m}" for r in ("shifted", "aligned") for m in ("linear-dfl", "mlp-dfl")
    }
    assert out["shifted/linear-dfl"]["holm_p"] == pytest.approx(0.004)
    assert out["shifted/linear-dfl"]["supported"] is True
    assert out["aligned/mlp-dfl"]["holm_p"] == pytest.approx(0.06)
    assert out["aligned/mlp-dfl"]["supported"] is False


def test_a_significant_negative_difference_is_not_supported():
    out = primary_hypotheses({"shifted": summary_with(0.0001, -0.1)})
    assert not any(cell["supported"] for cell in out.values())
