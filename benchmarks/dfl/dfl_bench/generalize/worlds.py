"""Structurally different synthetic task families and an exact oracle optimum at any size."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens

from ..instances import Instance, generate_instance, replay
from ..oracle import _closure_mask, quality_from_indices
from ..selection import is_usable

BASE_RATIO = 0.55


@dataclass(frozen=True)
class FamilySpec:
    name: str
    regime: str
    n_explore: tuple[int, int]
    n_action: tuple[int, int]
    max_dependencies: int
    long_history: bool = False


FAMILIES: tuple[FamilySpec, ...] = (
    FamilySpec("aligned-short", "aligned", (5, 8), (2, 5), 2),
    FamilySpec("shifted-short", "shifted", (5, 8), (2, 5), 2),
    FamilySpec("aligned-long", "aligned", (13, 17), (6, 10), 3, True),
    FamilySpec("shifted-long", "shifted", (13, 17), (6, 10), 3, True),
    FamilySpec("additive-deep", "additive", (8, 11), (6, 9), 3),
    FamilySpec("uninformative", "null", (6, 9), (3, 6), 2),
)
FAMILY_NAMES = tuple(f.name for f in FAMILIES)


def family_of(instance: Instance) -> str:
    return instance.template_id.split(":", 1)[0]


def rescale_budget(instance: Instance, ratio: float) -> Instance:
    return replace(instance, budget=max(1, int(instance.budget * ratio / BASE_RATIO)))


def build_family(
    spec: FamilySpec,
    seed: int,
    templates: int,
    per_template: int,
    ratio: float = BASE_RATIO,
    tag: str = "",
) -> list[Instance]:
    instances: list[Instance] = []
    for template in range(templates):
        template_id = f"{spec.name}:{tag}T{seed}x{template}"
        for index in range(per_template):
            shape_seed = f"{seed}/{template_id}/{index}"
            lo_e, hi_e = spec.n_explore
            lo_a, hi_a = spec.n_action
            span = (
                hash_int(shape_seed) % (hi_e - lo_e + 1),
                hash_int(shape_seed + "a") % (hi_a - lo_a + 1),
            )
            instance = generate_instance(
                template_id,
                spec.regime,
                index,
                seed,
                n_explore=lo_e + span[0],
                n_action=lo_a + span[1],
                max_dependencies=spec.max_dependencies,
            )
            instance = rescale_budget(instance, ratio) if ratio != BASE_RATIO else instance
            if is_usable(instance) and _needs_compaction(instance):
                instances.append(instance)
    return instances


def hash_int(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def build_world(seed: int, templates: int = 4, per_template: int = 4) -> list[Instance]:
    return [
        instance
        for spec in FAMILIES
        for instance in build_family(spec, seed, templates, per_template)
    ]


def fast_optimum_quality(instance: Instance) -> float:
    """Exact best oracle quality for any history length.

    Quality depends only on which requirements are met. An optimal feasible selection can be taken
    as the dependency closure of the mandatory set plus one alternative per requirement in some
    subset, so it suffices to enumerate (requirement subset, alternative choice) pairs.
    """

    built = replay(instance)
    ids = built.ids
    position = {episode_id: n for n, episode_id in enumerate(ids)}
    costs = [_episode_tokens(built.store.get(i)) for i in ids]
    deps = [[position[d] for d in built.store.get(i).depends_on] for i in ids]
    mandatory = _closure_mask(deps, [position[i] for i in built.protected])
    requirements = instance.requirements
    best = -1.0
    options = [(None, *alts) for alts in requirements]
    for choice in itertools.product(*options):
        seeds = [c for c in choice if c is not None]
        mask = _closure_mask(deps, seeds) | mandatory
        if sum(costs[i] for i in range(len(ids)) if mask >> i & 1) > instance.budget:
            continue
        chosen = {i for i in range(len(ids)) if mask >> i & 1}
        best = max(best, quality_from_indices(instance, chosen))
    if best < 0:
        raise ValueError(f'Instance "{instance.instance_id}" has no feasible selection.')
    return best


def _needs_compaction(instance: Instance) -> bool:
    built = replay(instance)
    return instance.budget < sum(_episode_tokens(built.store.get(i)) for i in built.ids)
