import dataclasses
import json
from pathlib import Path

import pytest
from dfl_bench.ledger import LedgerExecutor, OutcomeLedger
from dfl_bench.live.breakdown import cross_model_summary
from dfl_bench.live.executor import (
    LiveExecutor,
    LiveSettings,
    UsageTotals,
    api_key_from_environment,
)
from dfl_bench.live.experiment import LiveConfig, build_live_world, run_live_replicate
from dfl_bench.report import MEASURED_BANNER, primary_hypotheses, render, summarize

from nailong_agent_sdk.foundations.errors import AgentSdkError

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MODEL = "accounts/fireworks/models/glm-5p3-flash"


@pytest.fixture(scope="module")
def api_key():
    try:
        return api_key_from_environment("FIREWORKS_API_KEY", ENV_FILE)
    except AgentSdkError as error:
        pytest.skip(f"live model tests need FIREWORKS_API_KEY ({error.code})")


@pytest.mark.live
def test_a_small_measured_replicate_runs_end_to_end_on_real_model_calls(api_key, tmp_path):
    config = LiveConfig(
        seed=9100,
        variants_per_family=2,
        per_template=2,
        train_trials=2,
        eval_trials=1,
        pair_trials=1,
        rounds=1,
        run_mlp=False,
    )
    tasks = build_live_world(config)
    settings = LiveSettings(
        model=MODEL, concurrency=16, parameters={"temperature": 0.6, "max_tokens": 16384}
    )
    ledger = OutcomeLedger(tmp_path / "outcomes.jsonl", create=True)
    usage = UsageTotals()
    by_id = {t.instance.instance_id: t for t in tasks}
    executor = LiveExecutor(by_id, settings, ledger, api_key, usage)
    other_model = "accounts/fireworks/models/deepseek-v4p1-flash"
    other = LiveExecutor(
        by_id,
        dataclasses.replace(settings, model=other_model),
        OutcomeLedger(tmp_path / "other.jsonl", create=True),
        api_key,
        UsageTotals(),
    )
    relaxed = LiveExecutor(
        by_id, dataclasses.replace(settings, enforce_budget=False), ledger, api_key, usage
    )
    result = run_live_replicate(
        config,
        executor,
        relaxed,
        tasks,
        progress=lambda message: None,
        cross_executors={other_model: other},
    )

    assert result["evidence_kind"] == "measured" and usage.calls > 0
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    validity = result["measurement_validity"]
    assert validity["all_evidence_mean"] > validity["no_fact_evidence_mean"]
    rows = result["per_method"]["static-pckp"]
    assert rows and {r["solver_status"] for r in rows} == {"optimal"}
    for method_rows in result["per_method"].values():
        for row in method_rows:
            assert 0.0 <= row["quality"] <= 1.0 and 0 < row["token_fraction"] <= 1.0
            assert len(row["trial_scores"]) == config.eval_trials

    cross = result["cross_model"][other_model]
    assert set(cross) == set(result["per_method"]) and other.usage.calls > 0
    for method, method_rows in cross.items():
        assert [r["instance_id"] for r in method_rows] == [
            r["instance_id"] for r in result["per_method"][method]
        ]
        assert all(0.0 <= r["quality"] <= 1.0 for r in method_rows)
    document = {"regimes": {"live": {"replicates": [result]}}}
    table = cross_model_summary(document)[other_model]["static-pckp"]
    assert table["tasks"] == len(rows) and table["difference_vs_static"] is None
    summary = summarize([result])
    text = render("live", [result], summary)
    assert MEASURED_BANNER in text and "SYNTHETIC" not in text
    assert summary["paired_regret_vs_static"] == {}
    primary = primary_hypotheses({"live": {"summary": summary}}, (("live", "linear-dfl"),))
    assert set(primary) == {"live/linear-dfl"}

    replay = LedgerExecutor(tmp_path / "outcomes.jsonl")
    assert len(replay) == len(ledger) > 0
    rows_on_disk = [
        json.loads(line) for line in (tmp_path / "outcomes.jsonl").read_text().splitlines()
    ]
    assert {row["detail"]["model"] for row in rows_on_disk if "detail" in row} == {MODEL}
