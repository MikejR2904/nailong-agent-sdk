import json
import subprocess
import sys

import pytest

from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.mcp.security import ServerConfigurationError, load_http_service_settings
from nailong_agent_sdk.observability.run_checkpoints import (
    HmacSha256Signer,
    checkpoint_path,
    read_checkpoints,
)
from tests.support.mcp_cases import scripted_run
from tests.support.mcp_server import Server
from tests.support.processes import child_environment

TOKEN = "t" * 32
KEY = "service-checkpoint-key-" + "0123456789abcdef"


def settings(environment=None):
    environ = {"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, **(environment or {})}
    return load_http_service_settings(environ)


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "checkpoint.key"
    path.write_text(KEY + "\n", encoding="utf-8")
    return path


def test_checkpoints_and_the_audit_payload_cap_are_off_unless_configured():
    loaded = settings()
    assert loaded.checkpoint_directory is None and loaded.checkpoint_key_file is None
    assert loaded.checkpointer() is None and loaded.checkpoint_notice() is None
    assert loaded.audit_max_payload_chars is None


def test_the_checkpoint_variables_build_a_signer_and_a_sink_without_exposing_the_key(
    tmp_path, key_file
):
    loaded = settings(
        {
            "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere"),
            "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file),
            "AGENT_RUNTIME_CHECKPOINT_SIGNER_ID": "ops-service",
        }
    )
    checkpointer = loaded.checkpointer()
    assert checkpointer is not None and checkpointer.signer.signer_id == "ops-service"
    assert checkpointer.signer.verify(b"m", HmacSha256Signer(KEY).sign(b"m"))
    assert checkpointer.sink.location("r1").startswith(str(tmp_path / "elsewhere"))
    assert KEY not in repr(loaded) and str(key_file) not in repr(loaded)
    assert "ops-service" in loaded.checkpoint_notice()
    assert loaded.checkpoint_signer_id == "ops-service"


def test_the_signer_id_defaults_to_hmac_sha256(tmp_path, key_file):
    loaded = settings(
        {
            "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere"),
            "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file),
        }
    )
    assert loaded.checkpoint_signer_id == "hmac-sha256"


def test_the_directory_and_the_key_file_must_be_set_together(tmp_path, key_file):
    with pytest.raises(ServerConfigurationError) as only_directory:
        settings({"AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere")})
    assert "must be set together; only AGENT_RUNTIME_CHECKPOINT_DIR is set" in str(
        only_directory.value
    )
    with pytest.raises(ServerConfigurationError) as only_key:
        settings({"AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file)})
    assert "only AGENT_RUNTIME_CHECKPOINT_KEY_FILE is set" in str(only_key.value)


def test_a_key_file_that_cannot_sign_is_refused_without_printing_the_key(tmp_path):
    base = {"AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere")}
    with pytest.raises(ServerConfigurationError, match="is not a file"):
        settings({**base, "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(tmp_path / "missing.key")})
    short = tmp_path / "short.key"
    short.write_text("short-secret\n", encoding="utf-8")
    with pytest.raises(ServerConfigurationError) as refused:
        settings({**base, "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(short)})
    assert "at least 32 bytes, got 12" in str(refused.value)
    assert "short-secret" not in str(refused.value)


def test_a_signer_id_that_is_too_long_is_refused_naming_the_variable(tmp_path, key_file):
    with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_CHECKPOINT_SIGNER_ID"):
        settings(
            {
                "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere"),
                "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file),
                "AGENT_RUNTIME_CHECKPOINT_SIGNER_ID": "x" * 129,
            }
        )


def test_the_audit_payload_cap_is_read_from_the_environment():
    assert settings(
        {"AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS": " 4096 "}
    ).audit_max_payload_chars == (4096)
    assert settings({"AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS": ""}).audit_max_payload_chars is None


@pytest.mark.parametrize("raw", ["many", "255", "0", "-1", "1.5"])
def test_a_bad_audit_payload_cap_is_refused_naming_the_variable_and_value(raw):
    with pytest.raises(ServerConfigurationError) as refused:
        settings({"AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS": raw})
    assert (
        f'AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS must be a whole number of at least 256, got "{raw}"'
        in str(refused.value)
    )


def start_service(environment):
    return subprocess.run(
        [sys.executable, "-m", "nailong_agent_sdk.mcp.server"],
        env=child_environment({"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, **environment}),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_service_exits_with_status_2_when_the_checkpoint_sink_is_inside_the_run_root(
    tmp_path, key_file
):
    run_root = tmp_path / "run"
    completed = start_service(
        {
            "AGENT_RUNTIME_RUN_ROOT": str(run_root),
            "AGENT_RUNTIME_CHECKPOINT_DIR": str(run_root / "checkpoints"),
            "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file),
        }
    )
    assert completed.returncode == 2, completed.stderr
    assert "nailong-agent-sdk MCP server cannot start:" in completed.stderr
    assert "inside the run root" in completed.stderr and "Traceback" not in completed.stderr


def test_the_service_exits_with_status_2_when_only_one_checkpoint_variable_is_set(tmp_path):
    completed = start_service({"AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "elsewhere")})
    assert completed.returncode == 2, completed.stderr
    assert "AGENT_RUNTIME_CHECKPOINT_KEY_FILE" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_a_task_run_over_the_service_is_checkpointed_and_verifies_afterwards(tmp_path, key_file):
    elsewhere = tmp_path / "elsewhere"
    instance = Server(
        tmp_path,
        extra_environment={
            "AGENT_RUNTIME_CHECKPOINT_DIR": str(elsewhere),
            "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key_file),
            "AGENT_RUNTIME_CHECKPOINT_SIGNER_ID": "ops-service",
            "AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS": "300",
        },
    )
    try:
        run = scripted_run(instance, "svc-run")
        assert run["ok"] and run["result"]["status"] == "completed", run
        events = instance.call("get_telemetry_events", {"run_id": "svc-run", "limit": 500})
        assert events["events"][-1]["event_type"] == "run.completed"
        assert events["integrity_chain_valid"] is True
        audit = instance.call("get_audit_log", {"run_id": "svc-run"})
        assert audit["integrity_chain_valid"] is True
    finally:
        instance.stop()
    (record,) = read_checkpoints(checkpoint_path(elsewhere, "svc-run"))
    assert record.signer_id == "ops-service"
    assert record.telemetry_sequence == events["events"][-1]["sequence"]
    assert instance.run_root not in checkpoint_path(elsewhere, "svc-run").parents
    status = main(
        [
            "verify-checkpoints",
            str(instance.run_root),
            "svc-run",
            str(elsewhere),
            "--key-file",
            str(key_file),
            "--signer-id",
            "ops-service",
        ]
    )
    assert status == 0
    log = instance.log_path.read_text("utf-8")
    assert "run-head checkpoints are written to" in log
    assert KEY not in log and json.dumps(KEY) not in log
