import json
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.mcp.security import ServerConfigurationError, load_http_service_settings
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.keyring import KeyRing, VerificationKey
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    export_run_head_checkpoint,
    verify_run_head_checkpoints,
)
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    publish_tree_head,
    verify_tree_heads,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
HASHES = [f"{index:064x}" for index in range(1, 6)]


def at(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


@pytest.fixture
def world(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=5),
        ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    assert arun(subject.run(task("run-a"))).status is AgentRunStatus.COMPLETED
    yield services, FileCheckpointSink(tmp_path / "elsewhere")
    services.audit_logs.close()
    services.telemetry.close()


def checkpoint(services, sink, signer, hours, verifier=None):
    return export_run_head_checkpoint(
        services.telemetry,
        services.audit_logs,
        "run-a",
        signer,
        sink,
        now=NOW + timedelta(hours=hours),
        verifier=verifier,
    )


def verdict(services, sink, verifier):
    return verify_run_head_checkpoints(
        services.telemetry, services.audit_logs, "run-a", sink, verifier
    )


def test_a_verifier_with_only_public_keys_accepts_the_key_a_record_names(world):
    services, sink = world
    signer = Ed25519Signer.generate()
    assert signer.signer_id.startswith("ed25519:")
    checkpoint(services, sink, signer, 0)
    result = verdict(services, sink, KeyRing.of(signer).verifier())
    assert result.verdict is CheckpointVerdict.MATCHES and result.ok


def test_a_record_naming_a_key_the_ring_does_not_hold_is_refused_with_the_known_ids(world):
    services, sink = world
    signer, stranger = Ed25519Signer.generate(), Ed25519Signer.generate()
    checkpoint(services, sink, stranger, 0)
    result = verdict(services, sink, KeyRing.of(signer).verifier())
    assert result.verdict is CheckpointVerdict.SIGNATURE_INVALID
    assert f'no key "{stranger.signer_id}"' in result.detail and signer.signer_id in result.detail


def test_rotation_retires_the_old_key_for_later_records_and_starts_the_new_one(world):
    services, sink = world
    old, new = Ed25519Signer.generate("old"), Ed25519Signer.generate("new")
    ring = KeyRing.of(old).rotated(
        "old", VerificationKey(key_id="new", public_key=new.public_key_hex), at(1)
    )
    verifier = ring.verifier()
    checkpoint(services, sink, old, 0)
    assert verdict(services, sink, verifier).ok
    checkpoint(services, sink, new, 2, verifier)
    assert verdict(services, sink, verifier).ok
    late_old = FileCheckpointSink(sink.directory.parent / "late-old")
    checkpoint(services, late_old, old, 2)
    refused = verdict(services, late_old, verifier)
    assert refused.verdict is CheckpointVerdict.SIGNATURE_INVALID and "expired at" in refused.detail
    early_new = FileCheckpointSink(sink.directory.parent / "early-new")
    checkpoint(services, early_new, new, 0)
    refused = verdict(services, early_new, verifier)
    assert refused.verdict is CheckpointVerdict.SIGNATURE_INVALID
    assert "was not valid until" in refused.detail


def test_revocation_rejects_what_the_key_claims_from_the_revocation_time_onward(world):
    services, sink = world
    signer = Ed25519Signer.generate("laptop")
    ring = KeyRing.of(signer).revoked("laptop", at(1))
    checkpoint(services, sink, signer, 0)
    assert verdict(services, sink, ring.verifier()).ok
    after = FileCheckpointSink(sink.directory.parent / "after")
    checkpoint(services, after, signer, 2)
    refused = verdict(services, after, ring.verifier())
    assert (
        refused.verdict is CheckpointVerdict.SIGNATURE_INVALID
        and "was revoked at" in refused.detail
    )


def test_tree_heads_are_resolved_through_the_ring_too(tmp_path):
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    signer = Ed25519Signer.generate("heads")
    publish_tree_head(HASHES, sink, "r1.audit.merkle", signer, now=NOW)
    good = verify_tree_heads(HASHES, sink, "r1.audit.merkle", KeyRing.of(signer).verifier())
    assert good.verdict is TreeHeadVerdict.MATCHES
    revoked = KeyRing.of(signer).revoked("heads", at(-1))
    bad = verify_tree_heads(HASHES, sink, "r1.audit.merkle", revoked.verifier())
    assert bad.verdict is TreeHeadVerdict.SIGNATURE_INVALID and "was revoked at" in bad.detail


def test_the_ring_round_trips_through_a_file_and_refuses_duplicates_and_secrets(tmp_path):
    signer = Ed25519Signer.generate("one")
    ring = KeyRing.of(signer).revoked("one", at(3))
    path = tmp_path / "ring.json"
    ring.write(path)
    assert "private" not in path.read_text("utf-8")
    assert KeyRing.from_file(path).keys == ring.keys
    with pytest.raises(ValueError, match='"one" appears twice'):
        KeyRing([*ring.keys, *ring.keys])
    with pytest.raises(AgentSdkError) as failure:
        KeyRing.from_file(tmp_path / "missing.json")
    assert failure.value.code == "KEYRING_UNREADABLE"
    verifier = ring.verifier()
    with pytest.raises(AgentSdkError) as cannot:
        verifier.verify(b"m", "00")
    assert cannot.value.code == "KEYRING_NEEDS_SIGNER_ID"
    with pytest.raises(AgentSdkError) as sign:
        verifier.sign(b"m")
    assert sign.value.code == "KEYRING_CANNOT_SIGN"


def test_a_malformed_key_names_the_field_it_got_wrong():
    with pytest.raises(ValueError, match="public_key"):
        VerificationKey(key_id="k", public_key="zz" * 32)
    with pytest.raises(ValueError, match="not_after_utc"):
        VerificationKey(key_id="k", public_key="00" * 32, not_after_utc="tomorrow")


def mcp_settings(tmp_path, **extra):
    key = tmp_path / "ed.key"
    key.write_text(Ed25519Signer.generate().private_key_hex(), encoding="utf-8")
    environ = {
        "AGENT_RUNTIME_AUTH_TOKEN": "t" * 40,
        "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "ckpt"),
        "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key),
        **extra,
    }
    return load_http_service_settings(environ)


def test_the_mcp_service_can_sign_checkpoints_with_ed25519(tmp_path):
    settings = mcp_settings(tmp_path, AGENT_RUNTIME_CHECKPOINT_ALGORITHM="ed25519")
    signer = settings.checkpointer().signer
    assert isinstance(signer, Ed25519Signer) and signer.signer_id.startswith("ed25519:")
    assert "ed25519" in settings.checkpoint_notice()


def test_the_mcp_service_refuses_an_unknown_algorithm_and_a_key_that_is_not_hex(tmp_path):
    with pytest.raises(ServerConfigurationError, match="must be one of"):
        mcp_settings(tmp_path, AGENT_RUNTIME_CHECKPOINT_ALGORITHM="rsa")
    key = tmp_path / "bad.key"
    key.write_text("not hex at all", encoding="utf-8")
    with pytest.raises(ServerConfigurationError, match="64 hexadecimal"):
        load_http_service_settings(
            {
                "AGENT_RUNTIME_AUTH_TOKEN": "t" * 40,
                "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "ckpt"),
                "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key),
                "AGENT_RUNTIME_CHECKPOINT_ALGORITHM": "ed25519",
            }
        )


def test_the_cli_signs_with_ed25519_and_verifies_with_a_public_key_or_a_ring(
    world, tmp_path, capsys
):
    services, _ = world
    signer = Ed25519Signer.generate("ops")
    private = tmp_path / "private.key"
    private.write_text(signer.private_key_hex(), encoding="utf-8")
    public = tmp_path / "public.key"
    public.write_text(signer.public_key_hex, encoding="utf-8")
    ring = tmp_path / "ring.json"
    KeyRing.of(signer).write(ring)
    destination = tmp_path / "checkpoints"
    base = [str(services.run_root), "run-a"]
    services.audit_logs.close()
    services.telemetry.close()
    sign = ["--algorithm", "ed25519", "--key-file", str(private), "--signer-id", "ops"]
    assert main(["checkpoint-run", *base, str(destination), *sign]) == 0
    capsys.readouterr()
    for options in (
        ["--public-key-file", str(public), "--signer-id", "ops"],
        ["--keyring", str(ring)],
    ):
        assert main(["verify-checkpoints", *base, str(destination), *options]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["verification"]["verdict"] == "matches"
    assert main(["verify-checkpoints", *base, str(destination)]) != 0
    assert "needs one of --key-file, --public-key-file or --keyring" in capsys.readouterr().err
