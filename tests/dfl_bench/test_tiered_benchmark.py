from dfl_bench.live.experiment import LiveConfig, build_live_world
from dfl_bench.tiered import evaluate_task, oracle_utilities, render

from nailong_agent_sdk.memory.tiered_retention import TierPolicy


def tasks():
    return build_live_world(LiveConfig(seed=4300, variants_per_family=1, per_template=1))


def test_each_task_is_evaluated_with_and_without_summaries_and_never_over_budget():
    policy = TierPolicy()
    for task in tasks():
        for utilities in ("static", "oracle"):
            rows = evaluate_task(task, utilities, policy)
            assert set(rows) == {"binary", "tiered"}
            for row in rows.values():
                assert 0.0 <= row["fact_survival"] <= 1.0
                assert 0.0 < row["tokens_used"] <= 1.0
                assert row["status"] == "optimal"
            assert rows["binary"]["summary"] == 0
            assert rows["tiered"]["represented"] >= rows["binary"]["represented"]


def test_oracle_utilities_rate_fact_bearing_episodes_highest_and_the_table_names_its_limits():
    task = tasks()[0]
    ids = [f"episode-{n + 1}" for n in range(len(task.instance.recipe))]
    values = oracle_utilities(task, ids)
    sources = {i for indices in task.fact_sources.values() for i in indices}
    assert {n for n, e in enumerate(ids) if values[e] == 1.0} == sources
    results = {
        f"static/{variant}": [row]
        for variant, row in evaluate_task(task, "static", TierPolicy()).items()
    }
    text = render(results)
    assert "MODEL-FREE PROXY" in text and "necessary condition" in text
