import hashlib
from datetime import timedelta

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    export_run_head_checkpoint,
    read_checkpoints,
    verify_run_head_checkpoints,
)
from nailong_agent_sdk.observability.timestamp import Rfc3161Authority, parse_token
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    publish_tree_head,
    verify_tree_heads,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.fake_tsa import NOW, FakeTsa

SIGNER = HmacSha256Signer(b"timestamp-key-0123456789abcdef-1", signer_id="ops")
DIGEST = hashlib.sha256(b"evidence").digest()
HASHES = [f"{index:064x}" for index in range(1, 6)]


@pytest.fixture(scope="module", params=["rsa", "ecdsa"])
def tsa(request):
    return FakeTsa(kind=request.param)


def test_a_token_from_a_trusted_authority_yields_the_time_it_attests(tsa):
    verified = parse_token(tsa.stamp(DIGEST), DIGEST, [tsa.root])
    assert verified.gen_time == NOW and verified.signer_subject == "CN=Test TSA"


def test_a_token_is_refused_for_a_different_digest(tsa):
    with pytest.raises(AgentSdkError) as failure:
        parse_token(tsa.stamp(DIGEST), hashlib.sha256(b"other").digest(), [tsa.root])
    assert failure.value.code == "TIMESTAMP_IMPRINT_MISMATCH"


def test_an_altered_time_breaks_the_signature_even_with_the_imprint_intact(tsa):
    der = bytearray(tsa.stamp(DIGEST))
    position = bytes(der).index(b"20261101120000Z")
    der[position + 11] = ord("9")
    with pytest.raises(AgentSdkError) as failure:
        parse_token(bytes(der), DIGEST, [tsa.root])
    assert failure.value.code == "TIMESTAMP_SIGNATURE_INVALID"
    assert "messageDigest attribute" in failure.value.message


def test_a_token_whose_root_is_not_trusted_is_refused_naming_the_certificate():
    authority, stranger = FakeTsa(), FakeTsa()
    with pytest.raises(AgentSdkError) as failure:
        parse_token(authority.stamp(DIGEST), DIGEST, [stranger.root])
    assert failure.value.code == "TIMESTAMP_CERTIFICATE_INVALID"
    assert "No trusted root issues certificate" in failure.value.message


def test_a_certificate_without_the_time_stamping_usage_is_refused():
    authority = FakeTsa(eku=False)
    with pytest.raises(AgentSdkError) as failure:
        parse_token(authority.stamp(DIGEST), DIGEST, [authority.root])
    assert "time-stamping extended key usage" in failure.value.message


def test_a_certificate_that_had_expired_when_the_token_was_made_is_refused():
    authority = FakeTsa(clock=lambda: NOW + timedelta(days=400), lifetime_days=30)
    with pytest.raises(AgentSdkError) as failure:
        authority.stamp  # noqa: B018
        parse_token(authority.stamp(DIGEST), DIGEST, [authority.root])
    assert failure.value.code == "TIMESTAMP_CERTIFICATE_INVALID"
    assert "was not valid at" in failure.value.message


def test_an_unverified_parse_checks_only_the_imprint(tsa):
    verified = parse_token(tsa.stamp(DIGEST), DIGEST, None)
    assert verified.gen_time == NOW and verified.signer_subject == ""


def test_the_http_client_asks_with_a_nonce_and_accepts_only_the_reply_that_carries_it(tsa):
    server = tsa.serve()
    try:
        authority = Rfc3161Authority(f"http://127.0.0.1:{server.server_port}", name="local")
        token = authority.stamp(DIGEST)
        assert parse_token(token, DIGEST, [tsa.root]).gen_time == NOW
    finally:
        server.shutdown()
        server.server_close()


def test_the_http_client_refuses_a_reply_with_no_nonce_and_an_unreachable_authority(tsa):
    class Replayer(FakeTsa):
        def respond(self, request):
            return self.reply(DIGEST, None)

    replay = Replayer()
    server = replay.serve()
    try:
        authority = Rfc3161Authority(f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(AgentSdkError) as failure:
            authority.stamp(DIGEST)
        assert failure.value.code == "TIMESTAMP_NONCE_MISSING"
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(AgentSdkError) as gone:
        Rfc3161Authority("http://127.0.0.1:9", timeout_seconds=1).stamp(DIGEST)
    assert gone.value.code == "TIMESTAMP_UNREACHABLE" and "127.0.0.1:9" in gone.value.message


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


def stamped_checkpoint(services, sink, authority, now=NOW):
    return export_run_head_checkpoint(
        services.telemetry,
        services.audit_logs,
        "run-a",
        SIGNER,
        sink,
        now=now,
        timestamper=authority,
    )


def verify(services, sink, roots, **keywords):
    return verify_run_head_checkpoints(
        services.telemetry,
        services.audit_logs,
        "run-a",
        sink,
        SIGNER,
        trusted_roots=roots,
        **keywords,
    )


def test_a_stamped_checkpoint_carries_the_token_and_verifies_against_the_trusted_root(world):
    services, sink = world
    authority = FakeTsa()
    checkpoint = stamped_checkpoint(services, sink, authority)
    assert checkpoint.timestamp.authority == "fake-tsa" and checkpoint.timestamp.gen_time_utc
    result = verify(services, sink, [authority.root])
    assert result.verdict is CheckpointVerdict.MATCHES
    assert (result.stamped_checkpoints, result.verified_timestamps) == (1, 1)


def test_without_trusted_roots_a_stamp_is_counted_but_not_believed(world):
    services, sink = world
    authority = FakeTsa()
    stamped_checkpoint(services, sink, authority)
    result = verify(services, sink, None)
    assert result.ok and (result.stamped_checkpoints, result.verified_timestamps) == (1, 0)


def test_a_stamp_made_by_an_authority_the_verifier_does_not_trust_refuses_the_checkpoint(world):
    services, sink = world
    stamped_checkpoint(services, sink, FakeTsa())
    result = verify(services, sink, [FakeTsa().root])
    assert result.verdict is CheckpointVerdict.SIGNATURE_INVALID
    assert "the timestamp from fake-tsa is invalid" in result.detail


def test_a_checkpoint_whose_claimed_time_is_far_from_the_stamped_time_is_refused(world):
    services, sink = world
    authority = FakeTsa()
    stamped_checkpoint(services, sink, authority, now=NOW - timedelta(hours=3))
    result = verify(services, sink, [authority.root])
    assert result.verdict is CheckpointVerdict.SIGNATURE_INVALID and "apart" in result.detail
    assert verify(services, sink, [authority.root], max_skew_seconds=4 * 3600).ok


def test_editing_a_stamped_checkpoint_after_the_fact_is_caught_by_the_signature_and_the_stamp(
    world,
):
    services, sink = world
    authority = FakeTsa()
    stamped_checkpoint(services, sink, authority)
    path = sink.directory / "run-a.checkpoints.jsonl"
    original = read_checkpoints(path)[0]
    moved = original.model_copy(update={"audit_sequence": original.audit_sequence + 5})
    path.write_text(moved.model_dump_json() + "\n", encoding="utf-8")
    result = verify(services, sink, [authority.root])
    assert result.verdict is CheckpointVerdict.SIGNATURE_INVALID
    assert "record changed after it was stamped" in result.detail


def test_tree_heads_can_be_stamped_and_the_stamp_verified(tmp_path):
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    authority = FakeTsa()
    publish_tree_head(HASHES, sink, "r1.audit.merkle", SIGNER, now=NOW, timestamper=authority)
    result = verify_tree_heads(
        HASHES, sink, "r1.audit.merkle", SIGNER, trusted_roots=[authority.root]
    )
    assert result.verdict is TreeHeadVerdict.MATCHES
    assert (result.stamped_heads, result.verified_timestamps) == (1, 1)
    bad = verify_tree_heads(HASHES, sink, "r1.audit.merkle", SIGNER, trusted_roots=[FakeTsa().root])
    assert bad.verdict is TreeHeadVerdict.SIGNATURE_INVALID and "is invalid" in bad.detail
