import random

from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import (
    FixedUtilityPredictor,
    LearnedUtilityPredictor,
)
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import LinearRankingModel


def episode_ids(count: int) -> list[str]:
    return [f"episode-{n}" for n in range(1, count + 1)]


def fixed_predictor(values: dict[str, float] | None = None, *, ids=(), default: float = 1.0):
    table = {episode_id: default for episode_id in ids}
    table.update(values or {})
    return FixedUtilityPredictor(table, predictor_id="fixed-table", version="1")


def mismatched_model_predictor():
    model = LinearRankingModel([0.5] * (len(FEATURE_NAMES) - 1))
    return LearnedUtilityPredictor(
        model, predictor_id="mismatched-width", version="1", artifact_digest="e" * 64
    )


def build_store(rng: random.Random, n_exploratory: int, n_action: int, *, size=400, **kwargs):
    store = InMemoryEpisodeStore(**kwargs)
    explore = []
    for i in range(n_exploratory):
        record = store.open_exploratory(
            "a",
            content={"result": {"status": "succeeded", "text": "e" * rng.randint(size // 4, size)}},
        )
        store.close(record.id, description=f"explore {i} alpha")
        explore.append(record.id)
    actions = []
    for _ in range(n_action):
        deps = rng.sample(explore, min(len(explore), rng.choice([0, 1, 1, 2])))
        record = store.open_action(
            "a",
            deps,
            content={"result": {"status": "failed", "text": "a" * rng.randint(size // 4, size)}},
        )
        store.close(record.id)
        actions.append(record.id)
    return store, explore, actions
