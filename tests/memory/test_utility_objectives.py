import math
import random

import pytest

from nailong_agent_sdk.memory.utility_models import LinearRankingModel, MlpRankingModel
from nailong_agent_sdk.memory.utility_objectives import (
    ObjectiveConfig,
    ObjectiveDataset,
    ObjectiveTerm,
    build_objective_dataset,
    dro_update,
    effect_weights,
    log_softmax,
    objective_gradient,
    objective_group_losses,
    objective_loss,
    term_accuracy,
    train_objective,
)
from nailong_agent_sdk.memory.utility_training import RankingDataset, RankingPair, ranking_loss

D = 4


def np_diff(losses):
    return losses["major"] - losses["minor"]


def planted(seed, groups=("a", "b"), snapshots=24, size=6):
    rng = random.Random(seed)
    truth = [1.0, -0.6, 0.8, 0.0]
    features, labels, quality = {}, {}, {}
    for s in range(snapshots):
        name = f"s{s}"
        labels[name] = groups[s % len(groups)]
        features[name] = {f"e{i}": tuple(rng.uniform(0, 1) for _ in range(D)) for i in range(size)}
        quality[name] = {
            i: sum(t * x for t, x in zip(truth, v, strict=True)) for i, v in features[name].items()
        }
    return features, labels, quality


def candidate_sets(rng, size, count=4):
    return [sorted(rng.sample(range(size), 3)) for _ in range(count)]


def build(seed=0, kinds=("pair", "list", "reg")):
    rng = random.Random(seed)
    features, labels, quality = planted(seed)
    pairs, lists, points = [], [], []
    for name in features:
        sets = candidate_sets(rng, 6)
        values = [sum(quality[name][f"e{i}"] for i in s) for s in sets]
        ids = [[f"e{i}" for i in s] for s in sets]
        if "pair" in kinds:
            order = sorted(range(len(sets)), key=lambda j: -values[j])
            pairs.append((name, ids[order[0]], ids[order[-1]], rng.uniform(0.5, 2.0)))
        if "list" in kinds:
            lists.append((name, ids, values, rng.uniform(0.5, 2.0)))
        if "reg" in kinds:
            points.append((name, ids[0], values[0], rng.uniform(0.5, 2.0)))
    return build_objective_dataset(features, labels, pairs=pairs, lists=lists, points=points)


def numeric_gradient(model, dataset, config, weights=None, eps=1e-6):
    base = model.parameters
    out = []
    for k in range(len(base)):
        up, down = list(base), list(base)
        up[k] += eps
        down[k] -= eps
        out.append(
            (
                objective_loss(model.with_parameters(up), dataset, config, weights)
                - objective_loss(model.with_parameters(down), dataset, config, weights)
            )
            / (2 * eps)
        )
    return out


@pytest.mark.parametrize("objective", ["pairwise", "regret-pairwise", "listwise", "regression"])
@pytest.mark.parametrize("model_type", ["linear", "mlp"])
def test_analytic_gradient_matches_finite_differences(objective, model_type):
    dataset = build(1)
    config = ObjectiveConfig(objective=objective, model_type=model_type, l2=0.05, tau=0.7)
    rng = random.Random(3)
    model = (
        LinearRankingModel([rng.uniform(-1, 1) for _ in range(D)])
        if model_type == "linear"
        else MlpRankingModel.initialize(D, 5, 3)
    )
    analytic = objective_gradient(model, dataset, config)
    numeric = numeric_gradient(model, dataset, config)
    assert analytic == pytest.approx(numeric, rel=1e-4, abs=1e-6)


def test_group_weighted_gradient_matches_finite_differences():
    dataset = build(2)
    config = ObjectiveConfig(l2=0.01)
    model = LinearRankingModel([0.3, -0.2, 0.5, 0.1])
    weights = {"a": 0.8, "b": 0.2}
    assert objective_gradient(model, dataset, config, weights) == pytest.approx(
        numeric_gradient(model, dataset, config, weights), rel=1e-4, abs=1e-6
    )


def test_pair_terms_reproduce_the_baseline_pairwise_loss():
    dataset = build(4, kinds=("pair",))
    model = LinearRankingModel([0.4, -0.3, 0.2, 0.1])
    config = ObjectiveConfig(tau=0.8, l2=0.0)
    baseline = RankingDataset(
        dataset.items,
        tuple(RankingPair(t.candidates[0], t.candidates[1], t.weight) for t in dataset.terms),
    )
    mean_baseline = ranking_loss(model, baseline, tau=0.8, l2=0.0) / len(dataset.terms)
    assert objective_loss(model, dataset, config) == pytest.approx(mean_baseline, rel=1e-12)


def test_listwise_loss_is_stable_under_extreme_logits():
    items = ((1.0,), (2.0,))
    term = ObjectiveTerm("list", ((0,), (1,)), (1.0, 0.0), 1.0, "g")
    dataset = ObjectiveDataset(items, (term,))
    config = ObjectiveConfig(tau=1e-3, tau_quality=1e-3, l2=0.0)
    model = LinearRankingModel([1e6])
    loss = objective_loss(model, dataset, config)
    gradient = objective_gradient(model, dataset, config)
    assert math.isfinite(loss) and all(math.isfinite(g) for g in gradient)
    assert log_softmax([1e9, 0.0], 1e-3)[0] == pytest.approx(0.0)


def test_listwise_loss_is_invariant_to_items_shared_by_every_candidate():
    features = {"s": {"a": (1.0, 0.0), "b": (0.0, 1.0), "c": (0.5, 0.5)}}
    base = build_objective_dataset(
        features, {"s": "g"}, lists=[("s", [["a"], ["b"]], [1.0, 0.0], 1.0)]
    )
    shared = build_objective_dataset(
        features, {"s": "g"}, lists=[("s", [["a", "c"], ["b", "c"]], [1.0, 0.0], 1.0)]
    )
    model = LinearRankingModel([0.7, -0.2])
    config = ObjectiveConfig(l2=0.0)
    assert objective_loss(model, base, config) == pytest.approx(
        objective_loss(model, shared, config)
    )


def test_listwise_target_ties_give_a_uniform_target_and_no_manufactured_order():
    features = {"s": {"a": (1.0,), "b": (-1.0,)}}
    dataset = build_objective_dataset(
        features, {"s": "g"}, lists=[("s", [["a"], ["b"]], [0.5, 0.5], 1.0)]
    )
    model = LinearRankingModel([0.0])
    assert objective_gradient(model, dataset, ObjectiveConfig(l2=0.0)) == pytest.approx([0.0])
    assert term_accuracy(model, dataset) == pytest.approx(1.0)


def test_list_with_fewer_than_two_candidates_or_mismatched_qualities_is_rejected():
    features = {"s": {"a": (1.0,)}}
    with pytest.raises(ValueError, match="needs >= 2 candidates"):
        build_objective_dataset(features, {"s": "g"}, lists=[("s", [["a"]], [1.0], 1.0)])
    with pytest.raises(ValueError, match="no environment label"):
        build_objective_dataset(features, {}, points=[("s", ["a"], 1.0, 1.0)])
    with pytest.raises(ValueError, match="weights must be finite and positive"):
        build_objective_dataset(features, {"s": "g"}, points=[("s", ["a"], 1.0, 0.0)])


def test_contradictory_pairs_cancel_in_the_gradient_at_a_symmetric_point():
    features = {"s": {"a": (1.0,), "b": (2.0,)}}
    dataset = build_objective_dataset(
        features,
        {"s": "g"},
        pairs=[("s", ["a"], ["b"], 1.0), ("s", ["b"], ["a"], 1.0)],
    )
    config = ObjectiveConfig(l2=1e-3, max_iterations=200)
    model, _ = train_objective(dataset, config, feature_dim=1)
    assert abs(model.parameters[0]) < 1e-3
    zero = LinearRankingModel([0.0])
    assert objective_gradient(zero, dataset, ObjectiveConfig(l2=0.0)) == pytest.approx([0.0])


@pytest.mark.parametrize("objective", ["listwise", "regression", "pairwise"])
def test_linear_training_recovers_a_planted_ordering(objective):
    dataset = build(5)
    config = ObjectiveConfig(
        objective=objective, l2=1e-4, max_iterations=600, learning_rate=0.08, tau_quality=0.5
    )
    model, report = train_objective(dataset, config, feature_dim=D)
    assert report.training.train_accuracy >= 0.9 or objective == "regression"
    truth = [1.0, -0.6, 0.8, 0.0]
    cosine = sum(a * b for a, b in zip(model.parameters, truth, strict=True)) / (
        math.hypot(*model.parameters) * math.hypot(*truth)
    )
    assert cosine > 0.9


def test_training_is_deterministic_for_a_fixed_seed():
    dataset = build(6)
    config = ObjectiveConfig(model_type="mlp", objective="listwise", max_iterations=40, seed=9)
    first, _ = train_objective(dataset, config, feature_dim=D)
    second, _ = train_objective(dataset, config, feature_dim=D)
    assert first.parameters == second.parameters


def test_effect_weights_cap_normalise_and_reject_zero_effects():
    weights = effect_weights([0.1, 0.2, 5.0], [1.0, 1.0, 1.0], gamma=1.0, cap_ratio=3.0)
    assert sum(weights) / 3 == pytest.approx(1.0)
    assert max(weights) / min(weights) <= 3.0 / 0.1 * 0.2 + 1e-9
    uncapped = effect_weights([0.1, 0.2, 5.0], [1.0] * 3, gamma=1.0, cap_ratio=None)
    assert max(uncapped) / min(uncapped) == pytest.approx(50.0)
    flat = effect_weights([0.1, 0.4], [1.0, 1.0], gamma=0.0, normalize=False)
    assert flat == [1.0, 1.0]
    with pytest.raises(ValueError, match="zero effect"):
        effect_weights([0.0, 0.3], [1.0, 1.0], gamma=1.0)
    with pytest.raises(ValueError, match="gamma"):
        effect_weights([0.1], [1.0], gamma=-1.0)


def test_dro_update_moves_weight_to_the_worse_group_and_stays_on_the_simplex():
    updated = dro_update({"a": 0.5, "b": 0.5}, {"a": 1.0, "b": 3.0}, step=1.0)
    assert updated["b"] > 0.5 > updated["a"]
    assert sum(updated.values()) == pytest.approx(1.0)
    assert dro_update({"a": 0.5, "b": 0.5}, {"a": 1e6, "b": 0.0}, 1.0)["a"] == pytest.approx(1.0)


def test_group_dro_lowers_the_worst_group_loss_against_pooled_training():
    rng = random.Random(0)
    features, labels = {}, {}
    pairs = []
    for s in range(40):
        name = f"s{s}"
        group = "major" if s < 34 else "minor"
        labels[name] = group
        sign = 1.0 if group == "major" else -1.0
        features[name] = {"a": (1.0, rng.uniform(0, 1)), "b": (0.0, rng.uniform(0, 1))}
        winner, loser = ("a", "b") if sign > 0 else ("b", "a")
        pairs.append((name, [winner], [loser], 1.0))
    dataset = build_objective_dataset(features, labels, pairs=pairs)
    pooled_config = ObjectiveConfig(l2=1e-3, max_iterations=300, learning_rate=0.05)
    dro_config = pooled_config.model_copy(update={"group_dro": True, "dro_step": 0.5})
    pooled, _ = train_objective(dataset, pooled_config, feature_dim=2)
    robust, report = train_objective(dataset, dro_config, feature_dim=2)
    worst_pooled = max(objective_group_losses(pooled, dataset, pooled_config).values())
    worst_robust = max(objective_group_losses(robust, dataset, dro_config).values())
    assert worst_robust < worst_pooled
    robust_gap = abs(np_diff(objective_group_losses(robust, dataset, dro_config)))
    pooled_gap = abs(np_diff(objective_group_losses(pooled, dataset, pooled_config)))
    assert robust_gap < pooled_gap
    assert sum(report.group_weights.values()) == pytest.approx(1.0)


def test_training_with_no_terms_is_rejected_by_name():
    with pytest.raises(ValueError, match="at least one term"):
        train_objective(ObjectiveDataset((), ()), ObjectiveConfig(), feature_dim=D)


def test_early_stopping_returns_a_trained_model_unless_the_untrained_one_is_kept():
    features = {"s": {"a": (1.0, 0.0), "b": (0.0, 1.0)}, "v": {"a": (1.0, 0.0), "b": (0.0, 1.0)}}
    labels = {"s": "g", "v": "g"}
    train = build_objective_dataset(features, labels, pairs=[("s", ["a"], ["b"], 1.0)])
    conflicting = build_objective_dataset(features, labels, pairs=[("v", ["b"], ["a"], 1.0)])
    config = ObjectiveConfig(l2=1e-3, max_iterations=50, patience=5)
    trained, _ = train_objective(train, config, feature_dim=2, validation=conflicting)
    assert any(trained.parameters)
    kept, _ = train_objective(
        train,
        config.model_copy(update={"keep_initial": True}),
        feature_dim=2,
        validation=conflicting,
    )
    assert not any(kept.parameters)


def test_a_prior_centres_the_penalty_the_gradient_and_the_initial_model():
    dataset = build(7)
    prior = (0.5, -0.2, 0.1, 0.3)
    config = ObjectiveConfig(l2=0.3, prior=prior)
    model = LinearRankingModel([0.1, 0.4, -0.3, 0.2])
    assert objective_gradient(model, dataset, config) == pytest.approx(
        numeric_gradient(model, dataset, config), rel=1e-4, abs=1e-6
    )
    heavy = ObjectiveConfig(l2=1e6, prior=prior, max_iterations=20, keep_initial=True)
    trained, _ = train_objective(dataset, heavy, feature_dim=D)
    assert trained.parameters == pytest.approx(list(prior), abs=1e-4)
    with pytest.raises(ValueError, match="only defined for linear"):
        train_objective(dataset, ObjectiveConfig(model_type="mlp", prior=prior), feature_dim=D)
    with pytest.raises(ValueError, match="prior has 2 entries"):
        objective_loss(model, dataset, ObjectiveConfig(prior=(0.0, 0.0)))
