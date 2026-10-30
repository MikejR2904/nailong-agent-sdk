# Copyright (c) 2026 David Michael Indraputra

"""Opt-in signed checkpoints of a run's chain heads, taken by the runtime when a run ends."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex
from ..foundations.logging import get_logger
from ..observability.audit_log import AuditTranscriptStore
from ..observability.run_checkpoints import (
    CheckpointSigner,
    CheckpointSink,
    FileCheckpointSink,
    HmacSha256Signer,
    RunHeadCheckpoint,
    RunHeadReport,
    check_run_heads,
    export_run_head_checkpoint,
)
from ..observability.telemetry_store import TelemetryStore
from ..observability.transparency import publish_run_tree_heads
from .retention import pruned_heads_lookup

_logger = get_logger("run-checkpointer")
ANCHOR_HOME_VARIABLE = "NAILONG_ANCHOR_HOME"
ANCHOR_KEY_FILE = "anchor.key"
ANCHOR_KEY_BYTES = 32


class _LocalAnchor:
    def __repr__(self) -> str:
        return "LOCAL_ANCHOR"


LOCAL_ANCHOR = _LocalAnchor()


@dataclass(frozen=True)
class RunHeadCheckpointer:
    """A signer and a sink: where the runtime writes and reads a run's signed head records."""

    signer: CheckpointSigner
    sink: CheckpointSink
    tree_heads: bool = False

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
        checkpoint = export_run_head_checkpoint(
            telemetry,
            audit,
            run_id,
            self.signer,
            self.sink,
            pruned_lookup=pruned_heads_lookup(run_root),
        )
        if self.tree_heads:
            publish_run_tree_heads(telemetry, audit, run_id, self.sink, self.signer)
        return checkpoint

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

    @classmethod
    def local(cls, run_root: Path, home: Path | None = None) -> RunHeadCheckpointer:
        base = home or _anchor_home()
        key_path = base / ANCHOR_KEY_FILE
        base.mkdir(parents=True, exist_ok=True)
        if not key_path.is_file():
            _write_new_key(key_path)
        key = key_path.read_text(encoding="utf-8").strip()
        label = sha256_hex(str(run_root.resolve()))[:16]
        return cls(
            HmacSha256Signer(key, signer_id="local-anchor"),
            FileCheckpointSink(base / "anchors" / label),
        )


def _anchor_home() -> Path:
    configured = os.environ.get(ANCHOR_HOME_VARIABLE, "").strip()
    return Path(configured) if configured else Path.home() / ".nailong-agent-sdk"


def _write_new_key(path: Path) -> None:
    material = secrets.token_hex(ANCHOR_KEY_BYTES)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(material)


def resolve_checkpointer(
    value: RunHeadCheckpointer | None | _LocalAnchor, run_root: Path
) -> RunHeadCheckpointer | None:
    if not isinstance(value, _LocalAnchor):
        return value
    try:
        return RunHeadCheckpointer.local(run_root)
    except (OSError, ValueError, AgentSdkError) as error:
        _logger.warning(
            'No local anchor for run root "%s": %s: %s. Evidence is tamper-evident only; pass '
            "a RunHeadCheckpointer or set %s to a writable directory.",
            run_root,
            type(error).__name__,
            error,
            ANCHOR_HOME_VARIABLE,
        )
        return None
