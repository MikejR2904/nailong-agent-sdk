"""Memory-graph instances replayable into the SDK's episode store.

An instance is a recipe, not a dump, so every snapshot is rebuilt through the store's own public
lifecycle (open, close, mark_accessed, attach_source_handle) and obeys its invariants.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore

REGIMES = ("aligned", "shifted", "null", "additive")
ORACLE_FORMS = ("redundant", "additive")
VOCABULARY_SIZE = 36


@dataclass(frozen=True)
class Instance:
    instance_id: str
    template_id: str
    regime: str
    recipe: tuple[dict, ...]
    query: str
    budget: int
    protected_indices: tuple[int, ...]
    requirements: tuple[tuple[int, ...], ...]
    oracle_form: str = "redundant"

    @property
    def size(self) -> int:
        return sum(1 for step in self.recipe if step["op"] in ("explore", "action"))

    def graph_hash(self) -> str:
        edges = [
            [index, sorted(step.get("deps", []))]
            for index, step in enumerate(s for s in self.recipe if s["op"] in ("explore", "action"))
        ]
        return hashlib.sha256(json.dumps(edges).encode()).hexdigest()[:16]

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.recipe, sort_keys=True).encode()).hexdigest()[:16]

    def query_digest(self) -> str:
        return hashlib.sha256(self.query.encode()).hexdigest()[:16]

    def to_json(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "template_id": self.template_id,
            "regime": self.regime,
            "recipe": list(self.recipe),
            "query": self.query,
            "budget": self.budget,
            "protected_indices": list(self.protected_indices),
            "requirements": [list(r) for r in self.requirements],
            "oracle_form": self.oracle_form,
        }

    @classmethod
    def from_json(cls, data: dict) -> Instance:
        return cls(
            instance_id=data["instance_id"],
            template_id=data["template_id"],
            regime=data["regime"],
            recipe=tuple(data["recipe"]),
            query=data["query"],
            budget=int(data["budget"]),
            protected_indices=tuple(data["protected_indices"]),
            requirements=tuple(tuple(r) for r in data["requirements"]),
            oracle_form=data.get("oracle_form", "redundant"),
        )


@dataclass
class BuiltStore:
    store: InMemoryEpisodeStore
    ids: list[str]
    protected: frozenset[str]


def _step_content(step: dict, ids: list[str]) -> tuple[dict, str | None]:
    if "tool" in step:
        from .live.payloads import build_payload

        built = build_payload(step, len(ids), [ids[i] for i in step.get("deps", [])])
        return built.payload, built.summary
    return {"result": {"status": step["status"], "text": step["text"]}}, step.get("description")


def replay(instance: Instance, **store_kwargs) -> BuiltStore:
    store = InMemoryEpisodeStore(**store_kwargs)
    ids: list[str] = []
    for step in instance.recipe:
        operation = step["op"]
        if operation == "explore":
            content, description = _step_content(step, ids)
            substrate = step.get("substrate", False)
            record = store.open_exploratory(
                "bench",
                substrate_backed=substrate,
                snapshot_version="v1" if substrate else None,
                content=content,
            )
            store.close(record.id, description=description)
            ids.append(record.id)
        elif operation == "action":
            content, _ = _step_content(step, ids)
            record = store.open_action("bench", [ids[i] for i in step["deps"]], content=content)
            store.close(record.id)
            ids.append(record.id)
        elif operation == "access":
            store.mark_accessed([ids[i] for i in step["indices"]])
        elif operation == "handle":
            store.attach_source_handle(ids[step["index"]], f"handle-{step['index']}")
        else:
            raise ValueError(f"Unknown recipe operation {operation!r}.")
    return BuiltStore(store, ids, frozenset(ids[i] for i in instance.protected_indices))


def template_vocabulary(template_id: str) -> list[str]:
    seed = int(hashlib.sha256(template_id.encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    return [f"{template_id}{rng.randrange(10**5):05d}" for _ in range(VOCABULARY_SIZE)]


def _sentence(rng: random.Random, vocabulary: list[str], query_words: list[str], hit: float) -> str:
    words = [rng.choice(vocabulary) for _ in range(rng.randint(8, 40))]
    for word in query_words:
        if rng.random() < hit:
            words.insert(rng.randrange(len(words) + 1), word)
    return " ".join(words)


def generate_instance(
    template_id: str,
    regime: str,
    index: int,
    seed: int,
    *,
    label_noise: float = 0.15,
    n_explore: int | None = None,
    n_action: int | None = None,
    max_dependencies: int = 2,
) -> Instance:
    if regime not in REGIMES:
        raise ValueError(f"Unknown regime {regime!r}; expected one of {REGIMES}.")
    rng = random.Random(f"{seed}/{template_id}/{index}")
    vocabulary = template_vocabulary(template_id)
    query_words = rng.sample(vocabulary, 3)
    drawn_explore, drawn_action = rng.randint(5, 8), rng.randint(2, 5)
    n_explore = drawn_explore if n_explore is None else n_explore
    n_action = drawn_action if n_action is None else n_action
    total = n_explore + n_action
    status = ["failed" if rng.random() < 0.28 else "succeeded" for _ in range(total)]
    substrate = [rng.random() < 0.3 for _ in range(n_explore)]
    dependency_choices = [1, 1, 2] if max_dependencies <= 2 else [1, 1, 2, max_dependencies]
    deps = [[] for _ in range(n_explore)] + [
        sorted(rng.sample(range(n_explore), min(n_explore, rng.choice(dependency_choices))))
        for _ in range(n_action)
    ]
    dependents = [sum(1 for d in deps if i in d) for i in range(total)]

    k = rng.randint(2, 4)
    if regime in ("aligned", "additive", "null"):
        pool = list(range(total))
        members = rng.sample(pool, k)
    else:
        weights = [
            1.0
            + 3.0 * (status[i] == "failed")
            + 2.0 * (i < n_explore and substrate[i])
            + 1.5 * dependents[i]
            for i in range(total)
        ]
        members = _weighted_sample(rng, weights, k)
    members = [rng.randrange(total) if rng.random() < label_noise else m for m in members]
    members = list(dict.fromkeys(members))
    requirements = []
    for member in members:
        alternatives = [member]
        if rng.random() < 0.4 and regime != "additive":
            others = [i for i in range(total) if i not in members and i not in alternatives]
            if others:
                alternatives.append(rng.choice(others))
        requirements.append(tuple(sorted(alternatives)))
    member_set = {i for r in requirements for i in r}

    hit_member, hit_other = {
        "aligned": (0.85, 0.15),
        "additive": (0.85, 0.15),
        "null": (0.4, 0.4),
        "shifted": (0.15, 0.7),
    }[regime]
    recipe: list[dict] = []
    for i in range(total):
        hit = hit_member if i in member_set else hit_other
        text = _sentence(rng, vocabulary, query_words, hit)
        if i < n_explore:
            recipe.append(
                {
                    "op": "explore",
                    "text": text,
                    "status": status[i],
                    "substrate": substrate[i],
                    "description": f"{template_id} exploration {i}",
                    "deps": [],
                }
            )
        else:
            recipe.append({"op": "action", "text": text, "status": status[i], "deps": deps[i]})
    accessed = rng.sample(range(n_explore), rng.randint(0, n_explore))
    if accessed:
        recipe.append({"op": "access", "indices": accessed})
    for i in range(total):
        if rng.random() < 0.6:
            recipe.append({"op": "handle", "index": i})
    protected = (rng.randrange(total),) if rng.random() < 0.5 else ()
    draft = Instance(
        instance_id=f"{template_id}-{regime}-{index}",
        template_id=template_id,
        regime=regime,
        recipe=tuple(recipe),
        query=" ".join(query_words),
        budget=0,
        protected_indices=protected,
        requirements=tuple(requirements),
        oracle_form="additive" if regime == "additive" else "redundant",
    )
    built = replay(draft)
    costs = [_episode_tokens(built.store.get(i)) for i in built.ids]
    return Instance(**{**draft.__dict__, "budget": int(sum(costs) * rng.uniform(0.45, 0.65))})


def _weighted_sample(rng: random.Random, weights: list[float], k: int) -> list[int]:
    remaining = list(range(len(weights)))
    chosen: list[int] = []
    for _ in range(min(k, len(remaining))):
        pick = rng.choices(remaining, [weights[i] for i in remaining])[0]
        chosen.append(pick)
        remaining.remove(pick)
    return chosen
