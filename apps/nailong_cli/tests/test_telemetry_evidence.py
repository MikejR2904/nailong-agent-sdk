from __future__ import annotations

import asyncio
import json

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.config import ConfigError
from tests.conftest import call, final, run_cli, write_user_config
from tests.test_sdk_features import Collector, user_only_project


@pytest.fixture
def collector():
    server = Collector()
    yield server
    server.stop()


def scopes(server):
    names = set()
    for _, _, body in server.requests:
        if not body:
            continue
        for resource in json.loads(body).get("resourceLogs", []):
            names.update(block["scope"]["name"] for block in resource["scopeLogs"])
    return names


def test_everything_the_observability_layer_records_reaches_the_collector(project, home, collector):
    _, out, _, _ = run_cli(
        project,
        home,
        ["--otlp-endpoint", collector.url],
        final("done"),
        lines="Say done\n/telemetry now\n/exit\n",
        interactive=True,
    )
    assert {"nailong.audit", "nailong.integrity"} <= scopes(collector)
    assert "sending     telemetry, audit, integrity" in out
    assert "audit entr(ies)" in out and "integrity verdict(s)" in out


def test_the_user_can_narrow_what_is_sent(project, home, collector):
    write_user_config(
        home,
        'provider = "anthropic"\nmodel = "test-model"\napi_key_env = "TEST_KEY"\n'
        f'otlp_endpoint = "{collector.url}"\notlp_include = ["telemetry"]\n',
    )
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        final("done"),
        lines="Say done\n/telemetry now\n/exit\n",
        interactive=True,
    )
    assert "sending     telemetry" in out
    assert not ({"nailong.audit", "nailong.integrity"} & scopes(collector))


@pytest.mark.parametrize("value", ['["logs"]', "[]", '["audit", "audit"]', '"audit"'])
def test_a_bad_include_list_names_the_choices(project, home, value):
    with pytest.raises(ConfigError, match="otlp_include must list some of"):
        user_only_project(project, home, f"otlp_include = {value}\n")


class WaitsForCollector:
    def __init__(self, inner, server, seen):
        self.inner = inner
        self.server = server
        self.seen = seen
        self.calls_made = 0

    async def next_turn(self, context):
        self.calls_made += 1
        if self.calls_made == 2:
            for _ in range(100):
                if self.server.requests:
                    break
                await asyncio.sleep(0.1)
            self.seen.append(len(self.server.requests))
        return await self.inner.next_turn(context)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_records_reach_the_collector_while_the_run_is_still_going(project, home, collector):
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    seen: list[int] = []
    model = WaitsForCollector(
        ScriptedModel([call("read_file", path="a.txt"), final("done")]), collector, seen
    )
    run_cli(
        project,
        home,
        ["--otlp-endpoint", collector.url],
        lines="Read it\n/exit\n",
        interactive=True,
        build=lambda config, environ: ModelSetup(model, "scripted", "scripted-1"),
    )
    assert seen and seen[0] > 0


def test_a_finished_turn_ends_with_a_passing_integrity_verdict(project, home, collector):
    run_cli(
        project,
        home,
        ["--otlp-endpoint", collector.url],
        final("done"),
        lines="Say done\n/exit\n",
        interactive=True,
    )
    verdicts = []
    for _, _, body in collector.requests:
        for resource in json.loads(body or b"{}").get("resourceLogs", []):
            for block in resource["scopeLogs"]:
                verdicts += [
                    r for r in block["logRecords"] if r.get("eventName") == "integrity.verdict"
                ]
    values = [
        {item["key"]: next(iter(item["value"].values())) for item in record["attributes"]}
        for record in verdicts
    ]
    assert values and values[-1]["nailong.integrity.ok"] is True
    assert values[-1]["nailong.integrity.checkpoint_verdict"] in {"matches", "advanced"}
