from nailong_agent_sdk.foundations.contracts import ModelObservation
from nailong_agent_sdk.memory.context import assemble_initial_context
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy, ContextProjector
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from tests.support.agents import definition, task


def observations(count):
    return [
        ModelObservation(kind="agent-error", iteration=n + 1, message=f"obs {n:03d} " + "x" * 400)
        for n in range(count)
    ]


def window(block, count, budget=2_000):
    policy = ContextProjectionPolicy(
        context_token_budget=budget, episode_token_budget=256, observation_block=block
    )
    projection = ContextProjector(policy).project(
        assemble_initial_context(definition(), task()),
        observations(count),
        [],
        InMemoryEpisodeStore(),
    )
    return projection


def indices(projection):
    return [int(o.message.split()[1]) for o in projection.observations]


def test_without_a_block_the_window_slides_one_observation_at_a_time():
    fronts = [indices(window(0, n))[0] for n in range(30, 40)]
    assert fronts == sorted(fronts) and len(set(fronts)) == len(fronts)


def test_a_block_moves_the_front_only_once_per_block_and_starts_on_a_boundary():
    block = 4
    fronts = [indices(window(block, n))[0] for n in range(30, 46)]
    assert all(front % block == 0 for front in fronts)
    assert len(set(fronts)) <= 16 // block + 1
    assert fronts == sorted(fronts)


def test_a_block_window_is_contiguous_newest_aligned_and_within_the_budget():
    for n in range(20, 40):
        projection = window(4, n)
        kept = indices(projection)
        assert kept == list(range(kept[0], n))
        assert projection.metadata.estimated_tokens <= 2_000
        assert projection.metadata.omitted_observation_count == n - len(kept)
        sliding = indices(window(0, n))
        assert len(kept) <= len(sliding) and len(sliding) - len(kept) < 4


def test_nothing_is_evicted_while_everything_fits():
    projection = window(4, 3)
    assert indices(projection) == [0, 1, 2]
    assert projection.metadata.omitted_observation_count == 0
