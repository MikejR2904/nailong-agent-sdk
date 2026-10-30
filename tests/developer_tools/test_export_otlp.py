import json

import pytest

from nailong_agent_sdk.agent.model import ModelTurnResponse, ProviderUsage
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.observability.trace_context import graph_run_trace_id
from tests.support.agents import TURN_ADAPTER, arun, definition, final, task
from tests.support.collector import Collector
from tests.support.orchestration import (
    close_all,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)


class OneTurn:
    async def next_turn(self, context):
        return ModelTurnResponse(turn=TURN_ADAPTER.validate_python(final()), usage=ProviderUsage())


@pytest.fixture
def run_root(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    arun(services.create_agent(definition(), OneTurn()).run(task("cli-1")))
    services.audit_logs.close()
    services.telemetry.close()
    return tmp_path


@pytest.fixture
def collector():
    instance = Collector()
    yield instance
    instance.stop()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)


def run_cli(capsys, *arguments):
    status = main(["export-otlp", *map(str, arguments)])
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_a_dry_run_reports_what_would_be_sent_without_a_collector(run_root, capsys):
    status, out, _ = run_cli(capsys, run_root, "--run-id", "cli-1", "--dry-run")
    report = json.loads(out)
    assert status == 0 and report["runs"] == 1 and report["requests"] == 0
    assert report["spans"] == 3 and report["log_records"] > 5


def test_a_run_is_sent_to_the_collector_with_the_given_headers(run_root, collector, capsys):
    status, out, _ = run_cli(
        capsys,
        run_root,
        "--run-id",
        "cli-1",
        "--endpoint",
        collector.url,
        "--header",
        "x-api-key=secret-value-1",
        "--gzip",
        "--signals",
        "traces,logs",
    )
    report = json.loads(out)
    assert status == 0 and report["requests"] == 2 and report["rejected"] == {}
    assert sorted(r["path"] for r in collector.requests) == ["/v1/logs", "/v1/traces"]
    assert all(r["headers"]["x-api-key"] == "secret-value-1" for r in collector.requests)
    assert all(r["headers"]["content-encoding"] == "gzip" for r in collector.requests)


def test_the_standard_environment_variables_supply_the_endpoint_and_headers(
    run_root, collector, capsys, monkeypatch
):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", collector.url)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer%20abc123,x-team=agents")
    status, out, _ = run_cli(capsys, run_root, "--run-id", "cli-1", "--signals", "traces")
    assert status == 0 and json.loads(out)["requests"] == 1
    (request,) = collector.requests
    assert request["headers"]["authorization"] == "Bearer abc123"
    assert request["headers"]["x-team"] == "agents"


def test_a_graph_run_exports_the_controller_the_orchestration_and_every_node(tmp_path, capsys):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    close_all(orchestrator, services)
    status, out, _ = run_cli(
        capsys, tmp_path, "--graph-run", executed.graph_run_id, "--dry-run", "--signals", "traces"
    )
    report = json.loads(out)
    assert status == 0
    assert report["runs"] == 5
    assert report["spans"] == 1 + 3 * 3
    assert report["span_links"] == 1
    again, out, _ = run_cli(
        capsys, tmp_path, "--trace-id", graph_run_trace_id(executed.graph_run_id), "--dry-run"
    )
    assert again == 0 and json.loads(out)["runs"] == 5


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--run-id", "cli-1"], "Give --endpoint or set OTEL_EXPORTER_OTLP_ENDPOINT"),
        ([], "Name what to export with --run-id, --graph-run or --trace-id"),
        (["--graph-run", "ghost", "--dry-run"], "No run in"),
        (["--run-id", "cli-1", "--dry-run", "--signals", "spans"], "--signals takes a subset"),
        (["--run-id", "cli-1", "--dry-run", "--header", "novalue"], "must look like NAME=VALUE"),
        (["--run-id", "cli-1", "--endpoint", "collector:4318"], "absolute http(s) URL"),
    ],
)
def test_a_bad_request_is_an_operational_error_naming_the_problem(
    run_root, capsys, arguments, message
):
    status, out, err = run_cli(capsys, run_root, *arguments)
    assert status == 3 and out == ""
    assert "nailong-agent-sdk-dev export-otlp" in err and message in err


def test_a_collector_that_refuses_the_data_ends_with_the_run_and_status_named(
    run_root, collector, capsys
):
    collector.default = {"status": 401, "body": {"message": "unknown api key"}}
    status, _, err = run_cli(
        capsys, run_root, "--run-id", "cli-1", "--endpoint", collector.url, "--signals", "traces"
    )
    assert status == 3
    assert "AgentSdkError" in err and 'run "cli-1"' in err and "HTTP 401" in err
    assert "unknown api key" in err


def test_a_run_root_without_a_ledger_is_reported_not_created(tmp_path, capsys):
    status, _, err = run_cli(capsys, tmp_path / "empty", "--run-id", "x", "--dry-run")
    assert status == 3 and "does not exist" in err
    assert not (tmp_path / "empty").exists()


def test_exporting_a_run_again_sends_nothing_unless_the_ledger_is_switched_off(
    run_root, collector, capsys
):
    base = (run_root, "--run-id", "cli-1", "--endpoint", collector.url)
    status, out, _ = run_cli(capsys, *base)
    first = json.loads(out)
    assert status == 0 and first["requests"] > 0
    assert (run_root / ".otlp-export-ledger.json").is_file()
    before = len(collector.requests)
    status, out, _ = run_cli(capsys, *base)
    assert status == 0 and json.loads(out)["requests"] == 0
    assert len(collector.requests) == before
    status, out, _ = run_cli(capsys, *base, "--no-ledger")
    assert status == 0 and json.loads(out)["requests"] == first["requests"]
