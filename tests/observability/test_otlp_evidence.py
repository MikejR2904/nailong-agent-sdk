import pytest

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.otlp import OtlpExportLedger, OtlpHttpExporter
from nailong_agent_sdk.observability.otlp_evidence import (
    AUDIT_SCOPE,
    INTEGRITY_SCOPE,
    export_evidence,
)
from nailong_agent_sdk.observability.run_checkpoints import HmacSha256Signer
from nailong_agent_sdk.observability.sinks import FileCheckpointSink
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.observability.test_run_checkpoints import RUN_ID, rewrite_audit
from tests.support.agents import agent, arun, definition, final, task
from tests.support.collector import Collector

KEY = b"checkpoint-key-0123456789abcdef!"


@pytest.fixture
def collector():
    instance = Collector()
    yield instance
    instance.stop()


@pytest.fixture
def run(tmp_path):
    run_root = tmp_path / "run"
    telemetry = TelemetryStore(run_root)
    audit = AuditTranscriptStore(run_root)
    subject, _ = agent([final()], definition_=definition(), telemetry=telemetry, audit_logs=audit)
    assert arun(subject.run(task(RUN_ID))).status is AgentRunStatus.COMPLETED
    checkpointer = RunHeadCheckpointer(
        HmacSha256Signer(KEY, signer_id="ops-hmac"), FileCheckpointSink(tmp_path / "elsewhere")
    )
    checkpointer.export(run_root, telemetry, audit, RUN_ID)
    yield run_root, telemetry, audit, checkpointer
    telemetry.close()
    audit.close()


def records(collector, scope):
    found = []
    for request in collector.requests:
        for resource in request["json"]["resourceLogs"]:
            for block in resource["scopeLogs"]:
                if block["scope"]["name"] == scope:
                    found.extend(block["logRecords"])
    return found


def attributes(record):
    return {item["key"]: next(iter(item["value"].values())) for item in record["attributes"]}


def send(run, collector, tmp_path, **options):
    run_root, telemetry, audit, checkpointer = run
    exporter = OtlpHttpExporter(collector.url)
    ledger = OtlpExportLedger(tmp_path / "ledger.json")
    return export_evidence(
        telemetry,
        audit,
        [RUN_ID],
        exporter,
        options.pop("checkpointer", checkpointer),
        run_root,
        ledger=ledger,
        **options,
    )


def test_every_audit_entry_the_checkpoint_and_a_passing_verdict_are_sent(run, collector, tmp_path):
    _, _, audit, _ = run
    report = send(run, collector, tmp_path)
    sent = records(collector, AUDIT_SCOPE)
    assert report.audit_records == len(sent) == audit.entry_count(RUN_ID) > 0
    sequences = [attributes(item)["nailong.audit.sequence"] for item in sent]
    assert sequences == sorted(sequences, key=int)
    chain = [attributes(item) for item in sent]
    assert chain[0]["nailong.audit.integrity_hash"]
    assert chain[1]["nailong.audit.previous_hash"] == chain[0]["nailong.audit.integrity_hash"]
    integrity = {
        item["eventName"]: attributes(item) for item in records(collector, INTEGRITY_SCOPE)
    }
    assert integrity["integrity.checkpoint"]["nailong.checkpoint.signer_id"] == "ops-hmac"
    assert integrity["integrity.verdict"]["nailong.integrity.ok"] is True
    assert report.checkpoint_records == 1 and report.verdict_records == 1


def test_sending_again_sends_nothing_until_the_run_has_something_new(run, collector, tmp_path):
    send(run, collector, tmp_path)
    before = len(collector.requests)
    again = send(run, collector, tmp_path)
    assert len(collector.requests) == before and again.requests == 0


def test_a_rewritten_audit_entry_turns_the_verdict_into_an_error(run, collector, tmp_path):
    run_root, _, audit, _ = run
    rewrite_audit(audit, run_root, 2, lambda payload: payload.update(forged=True))
    send(run, collector, tmp_path)
    verdict = next(
        item
        for item in records(collector, INTEGRITY_SCOPE)
        if item["eventName"] == "integrity.verdict"
    )
    values = attributes(verdict)
    assert values["nailong.integrity.ok"] is False and verdict["severityText"] == "ERROR"
    assert values["nailong.integrity.checkpoint_verdict"] != "matches"


def test_audit_entries_can_be_left_out_and_only_integrity_is_sent(run, collector, tmp_path):
    report = send(run, collector, tmp_path, audit_entries=False)
    assert report.audit_records == 0 and records(collector, AUDIT_SCOPE) == []
    assert report.checkpoint_records == 1


def test_without_a_checkpointer_only_the_audit_entries_go(run, collector, tmp_path):
    report = send(run, collector, tmp_path, checkpointer=None)
    assert report.audit_records > 0 and records(collector, INTEGRITY_SCOPE) == []
    assert report.checkpoint_records == 0 and report.verdict_records == 0


def test_a_finished_run_without_a_checkpoint_is_only_judged_when_asked(run, collector, tmp_path):
    run_root, telemetry, audit, checkpointer = run
    other = FileCheckpointSink(tmp_path / "empty")
    bare = RunHeadCheckpointer(checkpointer.signer, other)
    early = send(run, collector, tmp_path, checkpointer=bare, judge_missing_checkpoints=False)
    assert early.verdict_records == 0
    final = send(run, collector, tmp_path, checkpointer=bare)
    assert final.verdict_records == 1
    verdict = next(
        item
        for item in records(collector, INTEGRITY_SCOPE)
        if item["eventName"] == "integrity.verdict"
    )
    assert attributes(verdict)["nailong.integrity.checkpoint_verdict"] == "no-checkpoint"
