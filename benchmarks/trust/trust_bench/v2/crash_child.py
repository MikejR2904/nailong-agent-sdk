# Copyright (c) 2026 David Michael Indraputra

"""Run one scripted case and die at the k-th model request, as a killed process would (V2.2).

``python -m trust_bench.v2.crash_child RUN_DIR FAMILY SEED K MODE`` runs the SDK arm with a
checkpointer, a checkpoint every 20 ms and 40 ms of latency per model call, and calls
``os._exit(9)`` when the k-th request arrives. Mode ``torn`` first appends half a record to the
audit log, the artifact log and the checkpoint file. A run shorter than k requests ends normally
with status 0.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from nailong_agent_sdk.foundations.identifiers import file_safe_name

from .. import wire
from ..evaluate import FAMILIES
from ..systems import nailong

INTERVAL_SECONDS = 0.02
LATENCY_SECONDS = 0.04
CRASH_STATUS = 9
TORN = {
    "audit": b'{"sequence": 99, "event_type": "note", "occurred',
    "artifacts": b'{"write_sequence": 99, "path_key": "x", "occ',
    "checkpoints": b'{"schema_version": "agent-sdk-run-head-checkpoint-v1", "run_id": "x"',
}


def torn_paths(run_dir: Path, run_id: str) -> dict[str, Path]:
    run_root = run_dir / "run"
    workspace = run_root / "workspaces" / file_safe_name(run_id)
    return {
        "audit": run_root / ".agent-audit-logs" / f"{file_safe_name(run_id)}.jsonl",
        "artifacts": workspace / ".agent-artifacts" / "history" / "writes.jsonl",
        "checkpoints": run_dir / "anchor" / f"{file_safe_name(run_id)}.checkpoints.jsonl",
    }


def main(argv: list[str]) -> int:
    run_dir, family, seed, crash_at, mode = (
        Path(argv[0]),
        argv[1],
        int(argv[2]),
        int(argv[3]),
        argv[4],
    )
    case = FAMILIES[family](seed)
    run_id = nailong.run_id_for(case, None)
    requests = {"count": 0}
    real_post = wire.SdkTransport.post_json
    real_options = nailong.options_for

    def post_json(self, url, *, headers, payload, timeout_seconds):  # type: ignore[no-untyped-def]
        requests["count"] += 1
        time.sleep(LATENCY_SECONDS)
        if requests["count"] >= crash_at:
            if mode == "torn":
                for name, path in torn_paths(run_dir, run_id).items():
                    if path.exists():
                        with path.open("ab") as stream:
                            stream.write(TORN[name])
            os._exit(CRASH_STATUS)
        return real_post(
            self, url, headers=headers, payload=payload, timeout_seconds=timeout_seconds
        )

    def options_for(case_):  # type: ignore[no-untyped-def]
        options = real_options(case_)
        return options.model_copy(update={"checkpoint_interval_seconds": INTERVAL_SECONDS})

    wire.SdkTransport.post_json = post_json  # type: ignore[method-assign]
    nailong.options_for = options_for  # type: ignore[assignment]
    nailong.run_sdk(case, None, run_dir, anchored=True)
    print(f"requests={requests['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
