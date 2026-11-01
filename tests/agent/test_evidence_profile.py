import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.evidence_profile import (
    EvidenceProfile,
    EvidenceSettings,
    RecorderSettings,
)
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.retention import RetentionPolicy, tombstone_path
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import RecorderCore, RecorderServer
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.fake_tsa import FakeTsa

APPEND, READ = "append-token-aaaa", "read-token-bbbb"
POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


@pytest.fixture
def recorder(tmp_path):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "recorder", key, head_every=3)
    server = RecorderServer(core, append_token=APPEND, read_token=READ)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    public = tmp_path / "recorder.pub"
    public.write_text(key.public_key_hex, encoding="utf-8")
    yield server, public
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def profile(tmp_path, monkeypatch, recorder, **overrides):
    server, public = recorder
    monkeypatch.setenv("EVIDENCE_APPEND", APPEND)
    monkeypatch.setenv("EVIDENCE_READ", READ)
    key = tmp_path / "writer.key"
    key.write_text(Ed25519Signer.generate("ops").private_key_hex(), encoding="utf-8")
    settings = EvidenceSettings(
        signing_key_file=key,
        signer_id="ops",
        checkpoint_directory=tmp_path / "elsewhere",
        recorder=RecorderSettings(
            host="127.0.0.1",
            port=server.port,
            append_token_variable="EVIDENCE_APPEND",
            read_token_variable="EVIDENCE_READ",
            public_key_file=public,
        ),
        **overrides,
    )
    return EvidenceProfile(settings)


def run_task(services, task_id="run-a"):
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=5),
        ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    assert arun(subject.run(task(task_id))).status is AgentRunStatus.COMPLETED
    assert services.checkpoint_run(task_id) is not None


def by_check(report):
    return {item.check: item for item in report.findings}


def test_one_profile_turns_on_checkpoints_tree_heads_the_recorder_and_signed_tombstones(
    tmp_path, monkeypatch, recorder
):
    evidence = profile(tmp_path, monkeypatch, recorder)
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        assert services.checkpointer.signed_tombstones and services.checkpointer.tree_heads
        run_task(services)
        report = evidence.verify(services, "run-a")
        assert report.ok, [item for item in report.findings if not item.ok]
        found = by_check(report)
        assert {"telemetry-chain", "audit-chain", "run-heads", "recorder", "terminal-link"} <= set(
            found
        )
        assert found["tree-heads:audit"].ok and "matches" in found["recorder"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_run_pruned_by_the_runtime_leaves_a_signed_tombstone_the_profile_accepts(
    tmp_path, monkeypatch, recorder
):
    evidence = profile(tmp_path, monkeypatch, recorder)
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        run_task(services)
        assert services.retention().prune(POLICY, dry_run=False, now=later()).runs
        found = by_check(evidence.verify(services, "run-a"))
        assert "pruned" in found["run-heads"].detail and found["signed-tombstones"].ok
        assert "vouched for by a verified signature" in found["signed-tombstones"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_forged_tombstone_for_a_deleted_run_fails_the_profile(tmp_path, monkeypatch, recorder):
    evidence = profile(tmp_path, monkeypatch, recorder)
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        run_task(services)
        assert services.retention().prune(POLICY, dry_run=False, now=later()).runs
        path = tombstone_path(services.run_root)
        record = json.loads(path.read_text("utf-8").splitlines()[0])
        record["signature"] = "00" * 64
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        report = evidence.verify(services, "run-a")
        assert not report.ok
        assert not by_check(report)["run-heads"].ok
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_recorder_that_cannot_be_reached_stops_the_run_instead_of_leaving_it_unwitnessed(
    tmp_path, monkeypatch, recorder
):
    evidence = profile(tmp_path, monkeypatch, recorder)
    server, _ = recorder
    server.shutdown()
    server.server_close()
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        with pytest.raises(AgentSdkError) as failure:
            services.audit_logs.append("run-a", "event", {})
        assert failure.value.code == "RECORDER_UNREACHABLE"
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_stamped_profile_reports_whether_every_checkpoint_carries_a_verified_timestamp(
    tmp_path, monkeypatch, recorder
):
    authority = FakeTsa(clock=lambda: datetime.now(UTC))
    root = tmp_path / "root.pem"
    from cryptography.hazmat.primitives import serialization

    root.write_bytes(authority.root.public_bytes(serialization.Encoding.PEM))
    evidence = profile(
        tmp_path, monkeypatch, recorder, trusted_roots=[root], require_timestamps=True
    )
    evidence.timestamper = authority
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        run_task(services)
        found = by_check(evidence.verify(services, "run-a"))
        assert "1 of 1" in found["timestamps"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_profile_states_what_it_is_missing(tmp_path, monkeypatch, recorder):
    key = tmp_path / "w.key"
    key.write_text(Ed25519Signer.generate("ops").private_key_hex(), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint_directory, witness or a recorder"):
        EvidenceProfile(EvidenceSettings(signing_key_file=key))
    with pytest.raises(ValueError, match="require_recorder is set but no recorder"):
        EvidenceProfile(EvidenceSettings(signing_key_file=key, checkpoint_directory=tmp_path / "x"))
    with pytest.raises(ValueError, match="require_timestamps needs trusted_roots"):
        EvidenceProfile(
            EvidenceSettings(
                signing_key_file=key,
                checkpoint_directory=tmp_path / "x",
                require_recorder=False,
                require_timestamps=True,
            )
        )
    monkeypatch.delenv("EVIDENCE_APPEND", raising=False)
    server, _ = recorder
    with pytest.raises(AgentSdkError) as failure:
        EvidenceProfile(
            EvidenceSettings(
                signing_key_file=key,
                checkpoint_directory=tmp_path / "x",
                recorder=RecorderSettings(
                    host="127.0.0.1", port=server.port, append_token_variable="EVIDENCE_APPEND"
                ),
            )
        )
    assert failure.value.code == "EVIDENCE_PROFILE_TOKEN_MISSING"
    assert "EVIDENCE_APPEND" in failure.value.message
    verify_only = EvidenceProfile(
        EvidenceSettings(
            keyring_file=None,
            signing_key_file=key,
            checkpoint_directory=tmp_path / "x",
            require_recorder=False,
        )
    )
    assert verify_only.mirror() is None


def test_the_cli_checks_a_run_against_a_profile_file(tmp_path, monkeypatch, recorder, capsys):
    from nailong_agent_sdk.developer_tools.cli import main

    evidence = profile(tmp_path, monkeypatch, recorder)
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        run_task(services)
    finally:
        services.audit_logs.close()
        services.telemetry.close()
    document = evidence.settings.model_dump(mode="json")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["verify-run-evidence", str(tmp_path / "run"), "run-a", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == "run-a" and all(item["ok"] for item in report["findings"])
