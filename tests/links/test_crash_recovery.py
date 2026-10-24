import sqlite3
import subprocess
import sys

import pytest

from nailong_agent_sdk.developer_tools.diagnose import open_ledgers
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    check_run_heads,
)
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    artifact_anchor_name,
    verify_artifact_anchor,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.support.crash_child import KEY, RUN
from tests.support.paths import REPO_ROOT
from tests.support.processes import child_environment

SIGNER = HmacSha256Signer(KEY, signer_id="crash")
CHILD = REPO_ROOT / "tests" / "support" / "crash_child.py"


def crash(tmp_path, mode):
    done = subprocess.run(
        [sys.executable, str(CHILD), str(tmp_path / "root"), str(tmp_path / "elsewhere"), mode],
        capture_output=True,
        text=True,
        timeout=120,
        env=child_environment(),
        cwd=str(REPO_ROOT),
    )
    assert done.returncode == 9, done.stderr[-2000:]


def sink_of(tmp_path):
    return FileCheckpointSink(tmp_path / "elsewhere")


def workspace_of(tmp_path):
    return tmp_path / "root" / "workspace"


def run_root_of(tmp_path):
    return tmp_path / "root" / "run"


def heads(tmp_path):
    with open_ledgers(run_root_of(tmp_path), RUN) as ledgers:
        return check_run_heads(ledgers.telemetry, ledgers.audit, RUN, sink_of(tmp_path), SIGNER)


def chains(tmp_path):
    with open_ledgers(run_root_of(tmp_path), RUN) as ledgers:
        return ledgers.telemetry.chain_break(RUN), ledgers.audit.chain_break(RUN)


def artifact_anchor(tmp_path):
    store = ArtifactStore(workspace_of(tmp_path), read_only=True)
    return verify_artifact_anchor(store, sink_of(tmp_path), artifact_anchor_name(RUN), SIGNER)


def log_path(tmp_path):
    return workspace_of(tmp_path) / ".agent-artifacts" / "history" / "writes.jsonl"


@pytest.mark.parametrize("mode", ["clean", "torn"])
def test_after_an_abrupt_exit_every_chain_is_intact_and_the_checkpoints_say_advanced(
    tmp_path, mode
):
    crash(tmp_path, mode)
    telemetry_break, audit_break = chains(tmp_path)
    assert telemetry_break is None, telemetry_break
    assert audit_break is None, audit_break
    assert ArtifactStore(workspace_of(tmp_path), read_only=True).verify_history().ok
    report = heads(tmp_path)
    assert report.verification.verdict is CheckpointVerdict.ADVANCED, report.verification.detail
    assert artifact_anchor(tmp_path).verdict is ArtifactAnchorVerdict.ADVANCED


def test_a_writer_that_resumes_after_a_torn_exit_repairs_the_tail_and_the_chains_continue(
    tmp_path,
):
    crash(tmp_path, "torn")
    store = ArtifactStore(workspace_of(tmp_path), durable=True)
    store.write_text("file0.txt", "after the crash\n", manifest={"agent_run_id": RUN})
    assert store.verify_history().ok and store.verify_history().records == 9
    audit = AuditTranscriptStore(run_root_of(tmp_path))
    try:
        audit.append(RUN, "note", {"after": "crash"}, task_id=RUN, iteration=9)
        assert audit.chain_break(RUN) is None
    finally:
        audit.close()
    assert artifact_anchor(tmp_path).verdict is ArtifactAnchorVerdict.ADVANCED


def test_removing_records_from_before_the_last_checkpoint_after_a_crash_is_detected(tmp_path):
    crash(tmp_path, "clean")
    lines = log_path(tmp_path).read_text("utf-8").splitlines()
    log_path(tmp_path).write_text("".join(f"{line}\n" for line in lines[:3]), "utf-8")
    assert artifact_anchor(tmp_path).verdict is ArtifactAnchorVerdict.TRUNCATED
    database = run_root_of(tmp_path) / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM events WHERE run_id = ? AND sequence > 3", (RUN,))
    connection.commit()
    connection.close()
    assert heads(tmp_path).verification.verdict is CheckpointVerdict.TRUNCATED


def test_removing_only_what_was_written_after_the_last_checkpoint_is_not_detected(tmp_path):
    crash(tmp_path, "clean")
    lines = log_path(tmp_path).read_text("utf-8").splitlines()
    log_path(tmp_path).write_text("".join(f"{line}\n" for line in lines[:5]), "utf-8")
    assert artifact_anchor(tmp_path).verdict is ArtifactAnchorVerdict.MATCHES
    database = run_root_of(tmp_path) / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(database)
    last = connection.execute(
        "SELECT sequence FROM events WHERE run_id = ? ORDER BY sequence LIMIT 1 OFFSET 4", (RUN,)
    ).fetchone()[0]
    connection.execute("DELETE FROM events WHERE run_id = ? AND sequence > ?", (RUN, last))
    connection.commit()
    connection.close()
    audit = run_root_of(tmp_path) / ".agent-audit-logs" / f"{file_safe_name(RUN)}.jsonl"
    kept = audit.read_text("utf-8").splitlines()[:5]
    audit.write_text("".join(f"{line}\n" for line in kept), "utf-8")
    assert heads(tmp_path).verification.verdict is CheckpointVerdict.MATCHES


def test_an_audit_log_cut_back_past_its_checkpoint_after_a_crash_is_detected(tmp_path):
    crash(tmp_path, "clean")
    path = run_root_of(tmp_path) / ".agent-audit-logs" / f"{file_safe_name(RUN)}.jsonl"
    lines = path.read_text("utf-8").splitlines()
    path.write_text("".join(f"{line}\n" for line in lines[:2]), "utf-8")
    assert heads(tmp_path).verification.verdict is CheckpointVerdict.TRUNCATED


def test_a_checkpoint_append_cut_off_by_a_crash_does_not_block_later_checkpoints(tmp_path):
    crash(tmp_path, "clean")
    path = sink_of(tmp_path).location(RUN)
    with open(path, "ab") as stream:
        stream.write(b'{"schema_version": "agent-sdk-run-head-checkpoint-v1", "run_id": "crash-r')
    report = heads(tmp_path)
    assert report.verification.verdict is CheckpointVerdict.ADVANCED, report.verification.detail
    from nailong_agent_sdk.observability.run_checkpoints import export_run_head_checkpoint

    with open_ledgers(run_root_of(tmp_path), RUN) as ledgers:
        taken = export_run_head_checkpoint(
            ledgers.telemetry, ledgers.audit, RUN, SIGNER, sink_of(tmp_path)
        )
    assert taken.telemetry_sequence > 0
    assert heads(tmp_path).verification.verdict is CheckpointVerdict.MATCHES
