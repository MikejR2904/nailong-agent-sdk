# Copyright (c) 2026 David Michael Indraputra

"""Opt-in signed checkpoints of a run's chain heads, taken by the runtime when a run ends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..foundations.errors import AgentSdkError
from ..observability.audit_log import AuditTranscriptStore
from ..observability.run_checkpoints import (
    CheckpointSigner,
    CheckpointSink,
    RunHeadCheckpoint,
    RunHeadReport,
    check_run_heads,
    export_run_head_checkpoint,
)
from ..observability.telemetry_store import TelemetryStore
from .retention import pruned_heads_lookup


@dataclass(frozen=True)
class RunHeadCheckpointer:
    """A signer and a sink: where the runtime writes and reads a run's signed head records."""

    signer: CheckpointSigner
    sink: CheckpointSink

    def require_outside(self, run_root: Path) -> None:
        if self.sink.is_inside(run_root):
            raise AgentSdkError(
                "RUN_CHECKPOINT_INSIDE_RUN_ROOT",
                f'The checkpoint sink stores its records inside the run root "{run_root}"; a '
                "checkpoint kept next to the run can be rewritten with it, so write it to "
                "another volume or host.",
                {"run_root": str(run_root)},
            )

    def export(
        self,
        run_root: Path,
        telemetry: TelemetryStore,
        audit: AuditTranscriptStore,
        run_id: str,
    ) -> RunHeadCheckpoint:
        return export_run_head_checkpoint(
            telemetry,
            audit,
            run_id,
            self.signer,
            self.sink,
            pruned_lookup=pruned_heads_lookup(run_root),
        )

    def check(
        self,
        run_root: Path,
        telemetry: TelemetryStore,
        audit: AuditTranscriptStore,
        run_id: str,
    ) -> RunHeadReport:
        return check_run_heads(
            telemetry,
            audit,
            run_id,
            self.sink,
            self.signer,
            pruned_lookup=pruned_heads_lookup(run_root),
        )
