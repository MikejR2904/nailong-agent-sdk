import json
from urllib import request as urlrequest
from urllib.error import HTTPError

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    publish_tree_head,
    verify_tree_heads,
)
from nailong_agent_sdk.observability.witness import HttpWitnessSink, WitnessServer

APPEND, READ = "append-token-1", "read-token-2"
HASHES = [f"{index:064x}" for index in range(1, 18)]
NAME = "r1.telemetry.merkle"


@pytest.fixture
def server(tmp_path):
    instance = WitnessServer(append_token=APPEND, read_token=READ, directory=tmp_path / "witness")
    instance.start()
    yield instance
    instance.stop()


def writer(server):
    return HttpWitnessSink(server.url, append_token=APPEND, allow_insecure_http=True)


def verifier_sink(server):
    return HttpWitnessSink(
        server.url, append_token=APPEND, read_token=READ, allow_insecure_http=True
    )


def raw(server, method, path, token, body=None):
    call = urlrequest.Request(
        server.url + path,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlrequest.urlopen(call, timeout=5) as response:
            return response.status
    except HTTPError as failure:
        return failure.code


def test_a_record_appended_by_the_writer_is_read_back_by_the_verifier(server):
    writer(server).append("run-1", "first")
    writer(server).append("run-1", "second")
    assert verifier_sink(server).read_all("run-1") == ["first", "second"]
    assert verifier_sink(server).read_all("never-written") == []
    assert server.records("run-1") == ["first", "second"]


def test_the_writer_holds_no_read_token_and_cannot_read_what_it_wrote(server):
    sink = writer(server)
    sink.append("run-1", "first")
    with pytest.raises(AgentSdkError) as refused:
        sink.read_all("run-1")
    assert refused.value.code == "WITNESS_READ_TOKEN_MISSING"
    assert "holds only the append token" in str(refused.value)


def test_the_witness_refuses_the_wrong_token_and_every_way_to_delete_or_rewrite(server):
    sink = HttpWitnessSink(server.url, append_token="wrong", allow_insecure_http=True)
    with pytest.raises(AgentSdkError) as refused:
        sink.append("run-1", "x")
    assert refused.value.code == "WITNESS_WRITE_FAILED" and "HTTP 403" in str(refused.value)
    writer(server).append("run-1", "kept")
    assert raw(server, "GET", "/v1/records/run-1", APPEND) == 403
    for method in ("DELETE", "PUT", "PATCH"):
        assert raw(server, method, "/v1/records/run-1", READ, b"{}") == 405
        assert raw(server, method, "/v1/records/run-1", APPEND, b"{}") == 405
    assert server.records("run-1") == ["kept"]


def test_malformed_bodies_are_refused_and_nothing_is_stored(server):
    for body in (b"not json", b'{"other": 1}', b'{"record": 5}', b'{"record": ""}'):
        assert raw(server, "POST", "/v1/records/run-1", APPEND, body) == 400
    assert raw(server, "POST", "/v1/elsewhere", APPEND, b"{}") == 404
    assert server.records("run-1") == []


def test_records_survive_a_restart_of_the_witness(tmp_path):
    directory = tmp_path / "witness"
    first = WitnessServer(append_token=APPEND, read_token=READ, directory=directory)
    first.start()
    try:
        writer(first).append("run/1", "kept across restarts")
    finally:
        first.stop()
    second = WitnessServer(append_token=APPEND, read_token=READ, directory=directory)
    second.start()
    try:
        assert verifier_sink(second).read_all("run/1") == ["kept across restarts"]
    finally:
        second.stop()


def test_an_unreachable_witness_is_named(server):
    sink = writer(server)
    server.stop()
    with pytest.raises(AgentSdkError) as failed:
        sink.append("run-1", "x")
    assert failed.value.code == "WITNESS_UNREACHABLE"
    assert server.url in str(failed.value)


def test_the_sink_refuses_plain_http_unless_loopback_is_allowed_and_checks_its_arguments():
    with pytest.raises(ValueError, match="is plain http; use https"):
        HttpWitnessSink("http://example.org", append_token="t")
    with pytest.raises(ValueError, match="loopback address only"):
        HttpWitnessSink("http://example.org", append_token="t", allow_insecure_http=True)
    with pytest.raises(ValueError, match="must be an http"):
        HttpWitnessSink("ftp://example.org", append_token="t")
    with pytest.raises(ValueError, match="append_token is required"):
        HttpWitnessSink("https://example.org", append_token="")
    with pytest.raises(ValueError, match="must differ"):
        WitnessServer(append_token="same", read_token="same")


def test_the_whole_chain_ed25519_heads_in_a_remote_witness_catch_a_local_cut(server):
    signer = Ed25519Signer.generate("run-signer")
    publish_tree_head(HASHES, writer(server), NAME, signer)
    verifier = signer.verifier()
    assert not verifier.can_sign
    sink = verifier_sink(server)
    assert verify_tree_heads(HASHES, sink, NAME, verifier).verdict is TreeHeadVerdict.MATCHES
    cut = verify_tree_heads(HASHES[:-4], sink, NAME, verifier)
    assert cut.verdict is TreeHeadVerdict.TRUNCATED
    resealed = [*HASHES[:6], *(f"{0xEE00 + index:064x}" for index in range(11))]
    assert verify_tree_heads(resealed, sink, NAME, verifier).verdict is TreeHeadVerdict.DIVERGED
    other = Ed25519Signer.generate("run-signer").verifier()
    forged = verify_tree_heads(HASHES, sink, NAME, other)
    assert forged.verdict is TreeHeadVerdict.SIGNATURE_INVALID


def test_a_writer_that_holds_only_the_append_token_cannot_erase_its_own_heads(server):
    signer = Ed25519Signer.generate()
    publish_tree_head(HASHES, writer(server), NAME, signer)
    assert raw(server, "DELETE", "/v1/records/" + NAME, APPEND) == 405
    assert len(json.loads(json.dumps(server.records(NAME)))) == 1
