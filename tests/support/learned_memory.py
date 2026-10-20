import random

from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import PredictorIdentity
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES, RetentionContext

IDENTITY = PredictorIdentity(
    predictor_id="stub",
    version="1",
    feature_schema_version="episode-utility-features-v1",
    artifact_digest="0" * 64,
)


class MappingPredictor:
    def __init__(self, values=None, default=1.0, identity=IDENTITY):
        self.values = values or {}
        self.default = default
        self._identity = identity
        self.calls = 0

    @property
    def identity(self):
        return self._identity

    def predict(self, context: RetentionContext):
        self.calls += 1
        return {r.id: self.values.get(r.id, self.default) for r in context.records}


class RaisingPredictor(MappingPredictor):
    def predict(self, context):
        raise RuntimeError("model exploded")


class ConstantFeaturePredictor(MappingPredictor):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def predict(self, context):
        return {r.id: self.value for r in context.records}


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


def feature_count():
    return len(FEATURE_NAMES)
