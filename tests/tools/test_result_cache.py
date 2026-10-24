import pytest

from nailong_agent_sdk.foundations.contracts import ToolExecutionResult
from nailong_agent_sdk.tools.registry import HarnessToolExecutor, HarnessToolRegistry
from nailong_agent_sdk.tools.result_cache import CacheableTool, ToolCachePolicy, ToolResultCache
from tests.support.tools import build, invocation, run, tool_def


def ok(output):
    return ToolExecutionResult(status="succeeded", output=output)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def cache(clock=None, window=0, ttl=60.0, **kwargs):
    return ToolResultCache(
        ToolCachePolicy(
            cacheable={"read_file": CacheableTool(ttl_seconds=ttl, reference_window=window)}
        ),
        clock=clock or Clock(),
        **kwargs,
    )


def test_a_repeated_call_replays_the_earlier_result_and_counts_the_tokens_it_replayed():
    store = cache()
    args = {"path": "a.txt", "limit": 5}
    assert store.lookup("read_file", args, call_id="c1", iteration=1) is None
    assert store.store("read_file", args, ok({"text": "x" * 400}), call_id="c1", iteration=1)
    replay = store.lookup("read_file", {"limit": 5, "path": "a.txt"}, call_id="c2", iteration=2)
    assert replay is not None and replay.output == {"text": "x" * 400}
    stats = store.stats()
    assert (stats.hits, stats.misses, stats.stores) == (1, 1, 1) and stats.replayed_tokens > 90
    other = store.lookup("read_file", {"path": "b.txt"}, call_id="c3", iteration=2)
    assert other is None


def test_entries_expire_after_their_time_to_live():
    clock = Clock()
    store = cache(clock, ttl=10)
    store.store("read_file", {"path": "a"}, ok(1), call_id="c1", iteration=1)
    clock.now = 10
    assert store.lookup("read_file", {"path": "a"}, call_id="c2", iteration=2) is not None
    clock.now = 10.5
    assert store.lookup("read_file", {"path": "a"}, call_id="c3", iteration=3) is None
    assert store.stats().expired == 1


def test_only_successful_results_of_listed_tools_are_stored():
    store = cache()
    failed = ToolExecutionResult(status="failed", error="boom")
    assert not store.store("read_file", {"path": "a"}, failed, call_id="c1", iteration=1)
    assert not store.store("glob", {"pattern": "*"}, ok([]), call_id="c1", iteration=1)
    assert store.stats().stores == 0
    with pytest.raises(ValueError, match='Tool "glob" is not in the cache policy'):
        store.lookup("glob", {}, call_id="c", iteration=1)
    assert not store.cacheable("glob") and store.cacheable("read_file")


def test_a_recent_repeat_can_be_answered_with_a_reference_instead_of_the_text():
    store = cache(window=2)
    store.store("read_file", {"path": "a"}, ok({"text": "y" * 800}), call_id="c1", iteration=1)
    near = store.lookup("read_file", {"path": "a"}, call_id="c2", iteration=3)
    assert near is not None and near.output["unchanged_since"] == {
        "tool_call_id": "c1",
        "iteration": 1,
    }
    assert near.output["result_tokens"] > 150
    far = store.lookup("read_file", {"path": "a"}, call_id="c3", iteration=4)
    assert far is not None and far.output == {"text": "y" * 800}
    assert store.stats().references == 1 and store.stats().hits == 1


def test_invalidation_clears_everything_and_the_entry_bound_evicts_the_oldest():
    store = cache(max_entries=2)
    for index in range(3):
        store.store("read_file", {"path": str(index)}, ok(index), call_id=f"c{index}", iteration=1)
    assert store.stats().evictions == 1
    assert store.lookup("read_file", {"path": "0"}, call_id="x", iteration=2) is None
    assert store.invalidate("write_draft (mutating)") == 2
    assert store.lookup("read_file", {"path": "2"}, call_id="x", iteration=2) is None
    assert store.stats().invalidation_reasons == ["write_draft (mutating)"]
    with pytest.raises(ValueError, match="max_entries must be at least 1"):
        cache(max_entries=0)


def executor_with_cache(tmp_path, **kwargs):
    executor, context = build(tmp_path, declared=("draft.txt",))
    store = cache(**kwargs)
    return HarnessToolExecutor(HarnessToolRegistry(), context, store), context, store


def read(executor, path, call_id="c1", iteration=1):
    call = invocation("read_file", {"path": path}, call_id)
    call = type(call)(**{**call.__dict__, "iteration": iteration})
    return run(executor.execute(tool_def("read_file"), call))


def test_a_read_after_a_write_by_the_same_run_is_never_served_from_before_the_write(tmp_path):
    executor, context, store = executor_with_cache(tmp_path)
    (context.run_root / "draft.txt").write_text("old\n", "utf-8")
    first = read(executor, "draft.txt")
    assert "old" in str(first.output)
    (context.run_root / "draft.txt").write_text("changed outside\n", "utf-8")
    assert "old" in str(read(executor, "draft.txt", "c2", 2).output)
    write = run(
        executor.execute(
            tool_def("write_draft"),
            invocation("write_draft", {"path": "draft.txt", "content": "new\n"}, "c3"),
        )
    )
    assert write.status == "succeeded"
    assert "new" in str(read(executor, "draft.txt", "c4", 4).output)
    stats = store.stats()
    assert (
        stats.hits == 1
        and stats.invalidations == 1
        and "write_draft" in stats.invalidation_reasons[0]
    )


def test_a_cache_hit_never_bypasses_capability_policy(tmp_path):
    executor, context, store = executor_with_cache(tmp_path)
    (context.run_root / "a.txt").write_text("secret\n", "utf-8")
    assert read(executor, "a.txt").status == "succeeded"
    traversal = read(executor, "../outside.txt", "c2", 2)
    assert traversal.status != "succeeded"
    assert store.stats().stores == 1
    unauthorised, _ = build(tmp_path / "other", grants=[])
    denied = HarnessToolExecutor(HarnessToolRegistry(), _, store)
    result = run(denied.execute(tool_def("read_file"), invocation("read_file", {"path": "a.txt"})))
    assert result.status == "blocked" and store.stats().hits == 0


def test_without_a_cache_nothing_changes(tmp_path):
    executor, context = build(tmp_path)
    (context.run_root / "a.txt").write_text("v1\n", "utf-8")
    assert "v1" in str(read(executor, "a.txt").output)
    (context.run_root / "a.txt").write_text("v2\n", "utf-8")
    assert "v2" in str(read(executor, "a.txt", "c2", 2).output)
