import itertools
import random

from nailong_agent_sdk.foundations.dependency_graph import deterministic_cycles


def brute_cycle_exists(nodes, edges):
    adjacency = {node: [] for node in nodes}
    for source, target in edges:
        adjacency[source].append(target)
    state = {}

    def visit(node):
        state[node] = 1
        for neighbour in adjacency[node]:
            if state.get(neighbour) == 1:
                return True
            if neighbour not in state and visit(neighbour):
                return True
        state[node] = 2
        return False

    return any(node not in state and visit(node) for node in nodes)


def test_cycle_detection_matches_brute_force_on_random_graphs():
    rng = random.Random(7)
    for _ in range(400):
        size = rng.randint(1, 8)
        nodes = [f"N{index}" for index in range(size)]
        edges = [(rng.choice(nodes), rng.choice(nodes)) for _ in range(rng.randint(0, 12))]
        cycles = deterministic_cycles(nodes, edges)
        assert bool(cycles) == brute_cycle_exists(nodes, edges), (nodes, edges)
        edge_set = set(edges)
        for cycle in cycles:
            assert cycle[0] == cycle[-1]
            assert all((a, b) in edge_set for a, b in itertools.pairwise(cycle))


def test_a_long_chain_has_no_cycle_and_a_long_ring_has_exactly_one():
    chain_length = 20_000
    chain_nodes = [f"R{index}" for index in range(chain_length)]
    chain = [(f"R{index}", f"R{index + 1}") for index in range(chain_length - 1)]
    assert deterministic_cycles(chain_nodes, chain) == []
    ring_length = 5_000
    ring_nodes = [f"R{index}" for index in range(ring_length)]
    ring = [(f"R{index}", f"R{(index + 1) % ring_length}") for index in range(ring_length)]
    cycles = deterministic_cycles(ring_nodes, ring)
    assert len(cycles) == 1 and len(cycles[0]) == ring_length + 1


def test_a_repeated_edge_does_not_repeat_the_cycle():
    edges = [("R1", "R2"), ("R1", "R2"), ("R2", "R1")]
    assert len(deterministic_cycles(["R1", "R2"], edges)) == 1
