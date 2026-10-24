import json

import pytest
from dfl_bench.ledger import OutcomeLedger
from dfl_bench.live.context import build_scene, model_context
from dfl_bench.live.executor import SUMMARY_SUFFIX, LiveExecutor, LiveSettings
from dfl_bench.live.experiment import LiveConfig, build_live_world
from dfl_bench.live.fewshot_run import tiered_selection

from nailong_agent_sdk.foundations.contracts import ModelBinding
from nailong_agent_sdk.memory.episode_models import SemanticSummaryView
from nailong_agent_sdk.memory.tiered_retention import FidelityCalibration

BINDING = ModelBinding(provider="p", model="m")


def task():
    return build_live_world(LiveConfig(seed=4310, variants_per_family=1, per_template=1))[0]


def test_summary_views_reach_the_model_context_and_count_against_the_budget():
    live = task()
    scene = build_scene(live.instance)
    full, summarised = scene.ids[:2], scene.ids[2]
    view = SemanticSummaryView(
        summary_id=f"tier-{summarised}",
        level=1,
        summary_text="get_account succeeded | owner=Ada",
        source_episode_ids=[summarised],
        source_episode_count=1,
    )
    context = model_context(
        scene, full, live.output_schema, BINDING, enforce_budget=False, summaries=[view]
    )
    assert [s.summary_id for s in context.semantic_summaries] == [f"tier-{summarised}"]
    assert all(c.episode_id != summarised for c in context.compacted_episodes) is False
    with pytest.raises(ValueError, match="over the episode budget"):
        model_context(
            scene,
            full,
            live.output_schema,
            BINDING,
            summaries=[view],
            summary_tokens=live.instance.budget + 1,
        )


def test_a_tiered_selection_is_within_budget_marks_summaries_and_registers_their_text(tmp_path):
    live = task()
    settings = LiveSettings(model="m")
    executor = LiveExecutor({}, settings, OutcomeLedger(tmp_path / "a.jsonl", create=True), "k")
    relaxed = LiveExecutor({}, settings, OutcomeLedger(tmp_path / "b.jsonl", create=True), "k")
    calibration = FidelityCalibration.fit([(0.0, 0.8), (1.0, 1.0)])
    selection = tiered_selection(
        "tiered-static", live.instance, None, calibration, (executor, relaxed)
    )
    encoded = selection.meta["live_ids"]
    summarised = {i[: -len(SUMMARY_SUFFIX)] for i in encoded if i.endswith(SUMMARY_SUFFIX)}
    assert summarised and summarised <= selection.ids
    assert len(encoded) == len(selection.ids)
    assert 0 < selection.meta["token_fraction"] <= 1.0
    for episode_id in summarised:
        text, tokens = executor.summaries[(live.instance.instance_id, episode_id)]
        assert text and tokens > 0 and relaxed.summaries[(live.instance.instance_id, episode_id)]
    ledger = OutcomeLedger(tmp_path / "c.jsonl", create=True)
    ledger.record(live.instance.instance_id, encoded, 0, 1.0, {})
    assert ledger.lookup(live.instance.instance_id, encoded, 0) == 1.0
    assert ledger.lookup(live.instance.instance_id, selection.ids, 0) is None
    assert json.loads(json.dumps(calibration.to_json()))["samples"] == 2
