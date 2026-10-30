import asyncio
import dataclasses
import time
from pathlib import Path

import pytest
from dfl_bench.ledger import OutcomeLedger
from dfl_bench.live.context import build_scene
from dfl_bench.live.executor import (
    LiveExecutor,
    LiveSettings,
    Throttle,
    UsageTotals,
    api_key_from_environment,
)
from dfl_bench.live.scenarios import FAMILIES, evidence_free_ids, generate_task

from nailong_agent_sdk.foundations.errors import AgentSdkError

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MODEL = "accounts/fireworks/models/glm-5p3-flash"
SETTINGS = LiveSettings(
    model=MODEL, concurrency=4, parameters={"temperature": 0.6, "max_tokens": 4096}
)


@pytest.fixture(scope="module")
def api_key():
    try:
        return api_key_from_environment("FIREWORKS_API_KEY", ENV_FILE)
    except AgentSdkError as error:
        pytest.skip(f"live model tests need FIREWORKS_API_KEY ({error.code})")


def tasks_for(seed):
    return [generate_task(family, 0, 0, seed) for family in FAMILIES]


def test_the_key_lookup_names_the_variable_and_reads_an_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DFL_PROBE_KEY", raising=False)
    with pytest.raises(AgentSdkError) as raised:
        api_key_from_environment("DFL_PROBE_KEY", tmp_path / "absent.env")
    assert raised.value.code == "LIVE_API_KEY_MISSING" and "DFL_PROBE_KEY" in raised.value.message
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=1\nDFL_PROBE_KEY='value-from-file'\n")
    assert api_key_from_environment("DFL_PROBE_KEY", env_file) == "value-from-file"
    monkeypatch.setenv("DFL_PROBE_KEY", "value-from-env")
    assert api_key_from_environment("DFL_PROBE_KEY", env_file) == "value-from-env"


def test_recorded_outcomes_are_served_without_any_model_call(tmp_path):
    task = tasks_for(21)[0]
    scene = build_scene(task.instance)
    selection = set(scene.ids[:2])
    ledger = OutcomeLedger(tmp_path / "ledger.jsonl", create=True)
    ledger.record(task.instance.instance_id, selection, 0, 2 / 3, {"model": "recorded"})
    executor = LiveExecutor({task.instance.instance_id: task}, SETTINGS, ledger, "never-used")
    assert executor.run(task.instance, selection, 0) == pytest.approx(2 / 3)
    batch = executor.run_batch([(task.instance, selection, 0)] * 3)
    assert batch == [pytest.approx(2 / 3)] * 3
    assert executor.usage.calls == 0 and executor.usage.cached >= 2


def test_the_token_cap_stops_before_a_call_is_made(tmp_path):
    task = tasks_for(22)[0]
    scene = build_scene(task.instance)
    ledger = OutcomeLedger(tmp_path / "ledger.jsonl", create=True)
    usage = UsageTotals(input_tokens=10)
    capped = dataclasses.replace(SETTINGS, input_token_cap=10)
    executor = LiveExecutor({task.instance.instance_id: task}, capped, ledger, "never-used", usage)
    with pytest.raises(AgentSdkError) as raised:
        executor.run(task.instance, set(scene.ids[:1]), 0)
    assert raised.value.code == "LIVE_TOKEN_CAP_REACHED" and "10" in raised.value.message
    assert len(ledger) == 0 and usage.calls == 0


def test_an_over_budget_selection_is_refused_not_scored(tmp_path):
    task = tasks_for(23)[0]
    scene = build_scene(task.instance)
    ledger = OutcomeLedger(tmp_path / "ledger.jsonl", create=True)
    executor = LiveExecutor({task.instance.instance_id: task}, SETTINGS, ledger, "never-used")
    with pytest.raises(ValueError, match="over the episode budget"):
        executor.run(task.instance, set(scene.ids), 0)
    assert len(ledger) == 0


@pytest.mark.live
def test_a_real_model_answers_from_retained_evidence_and_fails_without_it(api_key, tmp_path):
    tasks = tasks_for(31)
    ledger = OutcomeLedger(tmp_path / "ledger.jsonl", create=True)
    relaxed = dataclasses.replace(SETTINGS, enforce_budget=False)
    executor = LiveExecutor({t.instance.instance_id: t for t in tasks}, relaxed, ledger, api_key)
    full = [(t.instance, set(build_scene(t.instance).ids), 0) for t in tasks]
    none = [(t.instance, evidence_free_ids(t), 0) for t in tasks]
    with_evidence = executor.run_batch(full)
    without_evidence = executor.run_batch(none)
    assert sum(with_evidence) / len(tasks) >= 0.9
    assert sum(without_evidence) / len(tasks) <= 0.1
    assert executor.usage.input_tokens > 0 and executor.usage.output_tokens > 0
    assert len(ledger) == 2 * len(tasks)


@pytest.mark.live
def test_a_second_evaluation_is_served_from_the_ledger_and_survives_a_restart(api_key, tmp_path):
    task = tasks_for(32)[1]
    path = tmp_path / "ledger.jsonl"
    relaxed = dataclasses.replace(SETTINGS, enforce_budget=False)
    selection = set(build_scene(task.instance).ids)
    first = LiveExecutor(
        {task.instance.instance_id: task}, relaxed, OutcomeLedger(path, create=True), api_key
    )
    score = first.run(task.instance, selection, 0)
    assert first.usage.calls == 1
    resumed = LiveExecutor(
        {task.instance.instance_id: task}, relaxed, OutcomeLedger(path), "never-used"
    )
    assert resumed.run(task.instance, selection, 0) == score
    assert resumed.usage.calls == 0 and resumed.usage.cached == 1


@pytest.mark.live
def test_rejected_credentials_raise_instead_of_scoring_zero(tmp_path):
    task = tasks_for(33)[2]
    ledger = OutcomeLedger(tmp_path / "ledger.jsonl", create=True)
    relaxed = dataclasses.replace(SETTINGS, enforce_budget=False, max_attempts=1)
    executor = LiveExecutor({task.instance.instance_id: task}, relaxed, ledger, "fw_invalid_key")
    with pytest.raises(AgentSdkError) as raised:
        executor.run(task.instance, set(build_scene(task.instance).ids), 0)
    assert raised.value.code == "OPENAI_COMPATIBLE_HTTP_ERROR"
    assert len(ledger) == 0


@pytest.mark.live
def test_retention_under_the_real_projection_changes_what_the_model_can_answer(api_key, tmp_path):
    task = tasks_for(34)[0]
    scene = build_scene(task.instance)
    failure_sources = set(task.fact_sources["error_code"])
    only_failure = {scene.ids[i] for i in failure_sources}
    relaxed = dataclasses.replace(SETTINGS, enforce_budget=False)
    executor = LiveExecutor(
        {task.instance.instance_id: task},
        relaxed,
        OutcomeLedger(tmp_path / "ledger.jsonl", create=True),
        api_key,
    )
    partial, everything = executor.run_batch(
        [(task.instance, only_failure, 0), (task.instance, set(scene.ids), 0)]
    )
    assert partial == pytest.approx(1 / 3) and everything >= 2 / 3 and everything > partial


def test_a_rejection_halves_the_concurrency_pauses_everyone_and_success_regrows_it():
    throttle = Throttle(8, base_seconds=0.05, ceiling_seconds=0.4, spread_seconds=0.01)
    assert throttle.limit == 8
    first = throttle.rejected(None)
    assert throttle.limit == 4 and 0.05 <= first <= 0.05 * 1.25
    second = throttle.rejected(None)
    assert throttle.limit == 2 and 0.1 <= second <= 0.1 * 1.25
    assert throttle.rejected(10.0) <= 0.4 * 1.25 and throttle.limit == 1

    async def wait_out_the_cooldown():
        started = time.monotonic()
        await throttle.acquire()
        throttle.release()
        return time.monotonic() - started

    assert asyncio.run(wait_out_the_cooldown()) >= 0.35
    for _ in range(40):
        throttle.succeeded()
    assert throttle.limit == 8


def test_in_flight_calls_never_exceed_the_current_limit():
    throttle = Throttle(3, spread_seconds=0.0)
    peak = 0

    async def worker():
        nonlocal peak
        await throttle.acquire()
        try:
            peak = max(peak, throttle._in_flight)
            await asyncio.sleep(0.02)
        finally:
            throttle.release()

    async def run():
        await asyncio.gather(*(worker() for _ in range(12)))

    asyncio.run(run())
    assert peak == 3 and throttle._in_flight == 0


def test_a_throttle_that_was_never_triggered_does_not_delay_anyone():
    throttle = Throttle(2, base_seconds=5.0, ceiling_seconds=60.0, spread_seconds=5.0)
    started = time.monotonic()
    asyncio.run(throttle.acquire())
    assert time.monotonic() - started < 0.5
    with pytest.raises(ValueError, match="at least 1"):
        Throttle(0)
