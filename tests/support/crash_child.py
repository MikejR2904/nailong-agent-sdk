"""A child process that records evidence, anchors it, writes some more and dies abruptly.

Usage: python crash_child.py ROOT ELSEWHERE MODE
MODE "clean" exits with status 9 straight after the last write; MODE "torn" first leaves half a
record at the end of the artifact log and of the audit log, as a process killed between the
bytes of one write would.
"""

import os
import sys
from pathlib import Path

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.tools.artifact_anchor import anchor_artifact_log, artifact_anchor_name
from nailong_agent_sdk.tools.artifacts import ArtifactStore

KEY = b"crash-key-0123456789abcdef-0123!"
RUN = "crash-run"


def record(services, store, index):
    store.write_text(f"file{index % 3}.txt", f"version {index}\n", manifest={"agent_run_id": RUN})
    services.audit_logs.append(RUN, "note", {"index": index}, task_id=RUN, iteration=index)
    services.telemetry.emit(
        "note",
        TelemetryContext(run_id=RUN),
        actor=TelemetryActor(kind="system", identifier="child", role="test"),
        authority=TelemetryAuthority.SYSTEM,
        status="ok",
        payload={"index": index},
    )


def main(root: Path, elsewhere: Path, mode: str) -> None:
    signer = HmacSha256Signer(KEY, signer_id="crash")
    sink = FileCheckpointSink(elsewhere)
    services = AgentRuntimeServices.open(
        root / "run", checkpointer=RunHeadCheckpointer(signer, sink)
    )
    store = ArtifactStore(root / "workspace", durable=True)
    for index in range(5):
        record(services, store, index)
    services.checkpoint_run(RUN)
    anchor_artifact_log(store, sink, artifact_anchor_name(RUN), signer)
    for index in range(5, 8):
        record(services, store, index)
    if mode == "torn":
        with (root / "workspace" / ".agent-artifacts" / "history" / "writes.jsonl").open("ab") as f:
            f.write(b'{"write_sequence": 9, "path_key": "file0.txt", "occ')
        audit = root / "run" / ".agent-audit-logs" / f"{file_safe_name(RUN)}.jsonl"
        with audit.open("ab") as f:
            f.write(b'{"sequence": 9, "event_type": "note", "occurred')
    os._exit(9)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
