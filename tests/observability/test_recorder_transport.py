import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    client_ssl_context,
    scoped_append_token,
    server_ssl_context,
)
from tests.support.pki import make_pki
from tests.support.recorder_cases import (
    APPEND,
    serve,
    stop,
)


def test_a_scoped_token_opens_one_run_and_the_server_secret_itself_opens_none(tmp_path):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core, scoped_append=True)
    mine = RecorderClient(
        "127.0.0.1", server.port, append_token=lambda run: scoped_append_token(APPEND, run)
    )
    try:
        mine.append("run-a", "audit", 1, "ab" * 32, None)
        leaked = scoped_append_token(APPEND, "run-a")
        thief = RecorderClient("127.0.0.1", server.port, append_token=leaked)
        thief.append("run-a", "audit", 2, "cd" * 32, "ab" * 32)
        with pytest.raises(AgentSdkError) as other_run:
            thief.append("run-b", "audit", 1, "ef" * 32, None)
        assert other_run.value.code == "RECORDER_TOKEN_REFUSED"
        assert "append token for this run" in other_run.value.message
        master = RecorderClient("127.0.0.1", server.port, append_token=APPEND)
        with pytest.raises(AgentSdkError) as admin:
            master.append("run-a", "audit", 3, "01" * 32, "cd" * 32)
        assert admin.value.code == "RECORDER_TOKEN_REFUSED"
        assert core.entries("run-b", "audit") == [] and len(core.entries("run-a", "audit")) == 2
    finally:
        mine.close()
        stop(server)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return make_pki(tmp_path_factory.mktemp("pki"))


def test_a_recorder_over_tls_with_client_certificates_refuses_everyone_else(tmp_path, pki):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(
        core,
        tls=server_ssl_context(pki["server_cert"], pki["server_key"], client_ca_file=pki["ca"]),
    )
    try:
        good = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(
                pki["ca"], certificate_file=pki["client_cert"], key_file=pki["client_key"]
            ),
            server_hostname="localhost",
        )
        assert good.append("r1", "audit", 1, "ab" * 32, None)["size"] == 1
        good.close()
        anonymous = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(pki["ca"]),
            server_hostname="localhost",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError) as failure:
            anonymous.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        assert failure.value.code == "RECORDER_UNREACHABLE"
        plain = RecorderClient("127.0.0.1", server.port, append_token=APPEND, timeout_seconds=2)
        with pytest.raises(AgentSdkError):
            plain.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        other = make_pki(tmp_path / "other", "other")
        impostor_trust = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(
                other["ca"], certificate_file=pki["client_cert"], key_file=pki["client_key"]
            ),
            server_hostname="localhost",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError):
            impostor_trust.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        assert len(core.entries("r1", "audit")) == 1
    finally:
        stop(server)


def test_a_client_refuses_a_server_whose_name_is_not_in_its_certificate(tmp_path, pki):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core, tls=server_ssl_context(pki["server_cert"], pki["server_key"]))
    try:
        wrong_name = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(pki["ca"]),
            server_hostname="recorder.example",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError) as failure:
            wrong_name.append("r1", "audit", 1, "ab" * 32, None)
        assert failure.value.code == "RECORDER_UNREACHABLE"
    finally:
        stop(server)
