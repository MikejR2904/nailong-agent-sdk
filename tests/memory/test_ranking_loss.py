import math
import random

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.utility_models import LinearRankingModel, MlpRankingModel
from nailong_agent_sdk.memory.utility_training import (
    RankingDataset,
    RankingPair,
    RankingTrainingConfig,
    build_ranking_dataset,
    linear_ranking_hessian,
    pairwise_accuracy,
    ranking_gradient,
    ranking_loss,
    sigmoid,
    softplus,
    train_ranking_model,
)

D = 4


def random_dataset(seed, n_items=14, n_pairs=40, weights=False):
    rng = random.Random(seed)
    items = tuple(tuple(rng.uniform(-1, 1) for _ in range(D)) for _ in range(n_items))
    pairs = []
    for _ in range(n_pairs):
        ids = rng.sample(range(n_items), 6)
        pairs.append(
            RankingPair(tuple(ids[:3]), tuple(ids[3:]), rng.uniform(0.2, 1.0) if weights else 1.0)
        )
    return RankingDataset(items, tuple(pairs))


def planted_dataset(seed, n_items=30, n_pairs=200):
    rng = random.Random(seed)
    truth = [1.0, -0.5, 0.8, 0.0]
    items = tuple(tuple(rng.uniform(0, 1) for _ in range(D)) for _ in range(n_items))
    value = [sum(t * x for t, x in zip(truth, item)) for item in items]
    pairs = []
    while len(pairs) < n_pairs:
        a, b = rng.sample(range(n_items), 2)
        if abs(value[a] - value[b]) > 0.05:
            pairs.append(
                RankingPair((a,), (b,), 1.0)
                if value[a] > value[b]
                else RankingPair((b,), (a,), 1.0)
            )
    return RankingDataset(items, tuple(pairs))


def test_softplus_and_sigmoid_are_stable_at_extremes():
    assert softplus(1000.0) == pytest.approx(1000.0)
    assert softplus(-1000.0) == pytest.approx(0.0, abs=1e-300)
    assert softplus(0.0) == pytest.approx(math.log(2))
    assert sigmoid(-1000.0) == 0.0 and sigmoid(1000.0) == 1.0
    assert sigmoid(0.3) + sigmoid(-0.3) == pytest.approx(1.0)


def test_loss_matches_hand_computation():
    model = LinearRankingModel([1.0, 0.0])
    dataset = RankingDataset(((2.0, 9.0), (0.5, -3.0)), (RankingPair((0,), (1,), 0.5),))
    margin = 2.0 - 0.5
    expected = 0.5 * math.log(1 + math.exp(-margin / 2.0)) + 0.1 * (1.0)
    assert ranking_loss(model, dataset, tau=2.0, l2=0.1) == pytest.approx(expected)


def test_a_correctly_ordered_pair_costs_less_than_a_reversed_one():
    dataset = RankingDataset(((1.0,), (0.0,)), (RankingPair((0,), (1,), 1.0),))
    good = LinearRankingModel([2.0])
    bad = LinearRankingModel([-2.0])
    assert ranking_loss(good, dataset, tau=1.0, l2=0.0) < ranking_loss(
        bad, dataset, tau=1.0, l2=0.0
    )


def numeric_gradient(model, dataset, tau, l2, step=1e-6):
    base = model.parameters
    result = []
    for index in range(len(base)):
        up, down = list(base), list(base)
        up[index] += step
        down[index] -= step
        result.append(
            (
                ranking_loss(model.with_parameters(up), dataset, tau=tau, l2=l2)
                - ranking_loss(model.with_parameters(down), dataset, tau=tau, l2=l2)
            )
            / (2 * step)
        )
    return result


@pytest.mark.parametrize("tau", [0.5, 1.0, 3.0])
def test_linear_gradient_matches_finite_differences(tau):
    dataset = random_dataset(1, weights=True)
    model = LinearRankingModel([0.3, -0.7, 0.2, 0.5])
    analytic = ranking_gradient(model, dataset, tau=tau, l2=0.05)
    numeric = numeric_gradient(model, dataset, tau, 0.05)
    assert analytic == pytest.approx(numeric, rel=1e-5, abs=1e-7)


def test_mlp_gradient_matches_finite_differences():
    dataset = random_dataset(2, weights=True)
    model = MlpRankingModel.initialize(D, 5, seed=3)
    analytic = ranking_gradient(model, dataset, tau=1.5, l2=0.02)
    numeric = numeric_gradient(model, dataset, 1.5, 0.02)
    assert analytic == pytest.approx(numeric, rel=1e-4, abs=1e-6)


def test_hessian_matches_finite_differences_of_the_gradient():
    dataset = random_dataset(4, weights=True)
    model = LinearRankingModel([0.1, 0.2, -0.3, 0.4])
    hessian = linear_ranking_hessian(model, dataset, tau=1.2, l2=0.03)
    step = 1e-6
    for j in range(D):
        up, down = model.parameters, model.parameters
        up[j] += step
        down[j] -= step
        high = ranking_gradient(model.with_parameters(up), dataset, tau=1.2, l2=0.03)
        low = ranking_gradient(model.with_parameters(down), dataset, tau=1.2, l2=0.03)
        for i in range(D):
            assert hessian[i][j] == pytest.approx(
                (high[i] - low[i]) / (2 * step), rel=1e-5, abs=1e-7
            )


def test_hessian_is_symmetric_and_strongly_convex_with_modulus_two_lambda():
    dataset = random_dataset(5, weights=True)
    l2 = 0.07
    rng = random.Random(0)
    for _ in range(20):
        model = LinearRankingModel([rng.uniform(-3, 3) for _ in range(D)])
        hessian = linear_ranking_hessian(model, dataset, tau=0.8, l2=l2)
        for i in range(D):
            for j in range(D):
                assert hessian[i][j] == pytest.approx(hessian[j][i])
        for _ in range(20):
            v = [rng.uniform(-1, 1) for _ in range(D)]
            quad = sum(v[i] * hessian[i][j] * v[j] for i in range(D) for j in range(D))
            assert quad >= 2 * l2 * sum(x * x for x in v) - 1e-9


def test_convexity_along_random_segments():
    dataset = random_dataset(6)
    rng = random.Random(1)
    for _ in range(30):
        a = [rng.uniform(-2, 2) for _ in range(D)]
        b = [rng.uniform(-2, 2) for _ in range(D)]
        t = rng.random()
        mid = [t * x + (1 - t) * y for x, y in zip(a, b)]
        f = lambda p: ranking_loss(LinearRankingModel(p), dataset, tau=1.0, l2=0.01)  # noqa: E731
        assert f(mid) <= t * f(a) + (1 - t) * f(b) + 1e-9


def test_newton_reaches_a_stationary_point_and_the_minimizer_is_unique():
    dataset = planted_dataset(7)
    config = RankingTrainingConfig(tau=1.0, l2=0.5, max_iterations=60)
    model, report = train_ranking_model(dataset, config, feature_dim=D)
    assert report.converged and report.gradient_norm < 1e-7
    for start in ([5.0, -5.0, 5.0, -5.0], [0.0] * D):
        other = LinearRankingModel(start)
        for _ in range(200):
            gradient = ranking_gradient(other, dataset, tau=1.0, l2=0.5)
            other = other.with_parameters(
                [p - 0.02 * g for p, g in zip(other.parameters, gradient)]
            )
        assert other.parameters == pytest.approx(model.parameters, abs=1e-3)


def test_linear_training_recovers_the_planted_ordering():
    dataset = planted_dataset(8)
    model, report = train_ranking_model(
        dataset, RankingTrainingConfig(l2=1e-3, max_iterations=100), feature_dim=D
    )
    assert report.train_accuracy > 0.97
    assert pairwise_accuracy(model, dataset) == report.train_accuracy


def test_training_is_deterministic_for_a_fixed_seed():
    dataset = planted_dataset(9)
    config = RankingTrainingConfig(model_type="mlp", seed=11, max_iterations=40, hidden_units=4)
    first, _ = train_ranking_model(dataset, config, feature_dim=D)
    second, _ = train_ranking_model(dataset, config, feature_dim=D)
    assert first.parameters == second.parameters
    other, _ = train_ranking_model(dataset, config.model_copy(update={"seed": 12}), feature_dim=D)
    assert other.parameters != first.parameters


def test_mlp_training_lowers_the_loss_and_stays_finite():
    dataset = planted_dataset(10)
    config = RankingTrainingConfig(model_type="mlp", seed=1, max_iterations=80, learning_rate=0.05)
    start = MlpRankingModel.initialize(D, config.hidden_units, 1)
    model, report = train_ranking_model(dataset, config, feature_dim=D)
    assert all(math.isfinite(p) for p in model.parameters)
    assert report.final_loss < ranking_loss(start, dataset, tau=1.0, l2=config.l2)


def test_zero_l2_without_spanning_pairs_names_the_non_convex_cause():
    dataset = RankingDataset(((1.0, 0.0), (0.0, 0.0)), (RankingPair((0,), (1,), 1.0),))
    with pytest.raises(AgentSdkError) as raised:
        train_ranking_model(dataset, RankingTrainingConfig(l2=0.0), feature_dim=2)
    assert raised.value.code == "UTILITY_TRAINING_NOT_STRONGLY_CONVEX"
    assert "l2=0.0" in raised.value.message


def test_training_requires_pairs_and_valid_hyperparameters():
    with pytest.raises(ValueError, match="at least one preference pair"):
        train_ranking_model(RankingDataset((), ()), RankingTrainingConfig(), feature_dim=D)
    for bad in ({"tau": 0.0}, {"tau": float("nan")}, {"l2": -1.0}, {"l2": float("inf")}):
        with pytest.raises(ValueError):
            RankingTrainingConfig(**bad)


def features_for(snapshot):
    return {snapshot: {"a": (1.0, 0.0), "b": (0.0, 1.0), "c": (1.0, 1.0)}}


def test_dataset_cancels_common_items_and_skips_identical_sets():
    dataset = build_ranking_dataset(
        features_for("s"), [("s", ["a", "b"], ["b", "c"], 1.0), ("s", ["a"], ["a"], 1.0)]
    )
    assert len(dataset.pairs) == 1
    pair = dataset.pairs[0]
    assert [dataset.item_keys[i] for i in pair.plus] == ["s/a"]
    assert [dataset.item_keys[i] for i in pair.minus] == ["s/c"]


def test_conflicting_pairs_cancel_in_gradient_at_the_symmetric_point():
    features = features_for("s")
    dataset = build_ranking_dataset(features, [("s", ["a"], ["b"], 1.0), ("s", ["b"], ["a"], 1.0)])
    gradient = ranking_gradient(LinearRankingModel([0.0, 0.0]), dataset, tau=1.0, l2=0.0)
    assert gradient == pytest.approx([0.0, 0.0])
    model, report = train_ranking_model(dataset, RankingTrainingConfig(l2=0.1), feature_dim=2)
    assert model.parameters == pytest.approx([0.0, 0.0], abs=1e-9)
    assert report.train_accuracy == 0.5


def test_duplicate_pairs_double_the_weight_of_one_pair():
    features = features_for("s")
    once = build_ranking_dataset(features, [("s", ["a"], ["b"], 2.0)])
    twice = build_ranking_dataset(features, [("s", ["a"], ["b"], 1.0)] * 2)
    model = LinearRankingModel([0.4, -0.2])
    assert ranking_loss(model, once, tau=1.0, l2=0.0) == pytest.approx(
        ranking_loss(model, twice, tau=1.0, l2=0.0)
    )


def test_dataset_rejects_unknown_snapshots_episodes_and_bad_weights():
    features = features_for("s")
    with pytest.raises(ValueError, match='unknown snapshot "x"'):
        build_ranking_dataset(features, [("x", ["a"], ["b"], 1.0)])
    with pytest.raises(ValueError, match='episode "z"'):
        build_ranking_dataset(features, [("s", ["z"], ["b"], 1.0)])
    for weight in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="weights must be finite and positive"):
            build_ranking_dataset(features, [("s", ["a"], ["b"], weight)])
