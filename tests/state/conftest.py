import pytest

from nailong_agent_sdk.state.graph import StateGraph


@pytest.fixture(autouse=True)
def graph_snapshots_equal_a_fresh_dump(monkeypatch):
    cached = StateGraph.snapshot

    def checked(self):
        snapshot = cached(self)
        if len(snapshot["nodes"]) > 100:
            return snapshot
        remembered = (self._dumped_items, self._dumped_shared_state)
        self._dumped_items, self._dumped_shared_state = {}, None
        try:
            fresh = cached(self)
        finally:
            self._dumped_items, self._dumped_shared_state = remembered
        assert snapshot == fresh, "the reused item dumps differ from a fresh dump of the graph"
        return snapshot

    monkeypatch.setattr(StateGraph, "snapshot", checked)
    return cached


@pytest.fixture
def plain_graph_snapshots(graph_snapshots_equal_a_fresh_dump, monkeypatch):
    monkeypatch.setattr(StateGraph, "snapshot", graph_snapshots_equal_a_fresh_dump)
