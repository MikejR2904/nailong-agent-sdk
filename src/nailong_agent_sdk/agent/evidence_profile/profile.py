# Copyright (c) 2026 David Michael Indraputra

"""One switch that turns on every independent piece of run evidence and checks them together."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ...foundations.errors import AgentSdkError
from ...observability.audit_log import AuditTranscriptStore
from ...observability.recorder import RecorderMirror
from ...observability.run_checkpoints import CheckpointSigner
from ...observability.telemetry_store import TelemetryStore
from ...observability.timestamp import TimestampAuthority
from ..run_checkpointer import RunHeadCheckpointer
from .build import build_components
from .checks import CHECKS, EvidenceContext, EvidenceReport
from .settings import EvidenceSettings

if TYPE_CHECKING:
    from cryptography.x509 import Certificate

    from ..runtime import AgentRuntimeServices


class EvidenceProfile:
    """Writer and verifier sides of the evidence an operator keeps outside the agent's reach.

    Where to look: settings in ``settings.py``, how they become keys, sinks and clients in
    ``build.py``, and what is checked afterwards in ``checks.py``.
    """

    def __init__(
        self, settings: EvidenceSettings, environ: Mapping[str, str] | None = None
    ) -> None:
        self.settings = settings
        self._parts = build_components(settings, os.environ if environ is None else environ)
        self.sink = self._parts.sink
        self.roots: list[Certificate] = self._parts.roots
        self.timestamper: TimestampAuthority | None = self._parts.timestamper

    @property
    def verifier(self) -> CheckpointSigner | None:
        ring = self._parts.ring
        return ring.verifier() if ring is not None else None

    def checkpointer(self) -> RunHeadCheckpointer | None:
        signer = self._parts.signer
        if signer is None:
            if self._parts.writer is None:
                raise AgentSdkError(
                    "EVIDENCE_PROFILE_CANNOT_SIGN",
                    "This evidence profile has neither a signing_key_file nor a recorder, so it "
                    "can verify evidence but not write it.",
                )
            return None
        assert self.sink is not None
        return RunHeadCheckpointer(
            signer=signer,
            sink=self.sink,
            tree_heads=True,
            signed_tombstones=True,
            verifier=self.verifier,
            timestamper=self.timestamper,
            trusted_roots=tuple(self.roots),
        )

    def mirror(self) -> RecorderMirror | None:
        if self._parts.writer is None:
            return None
        return RecorderMirror(self._parts.writer, required=self.settings.require_recorder)

    def apply(
        self, telemetry: TelemetryStore, audit: AuditTranscriptStore
    ) -> RecorderMirror | None:
        mirror = self.mirror()
        if mirror is not None:
            mirror.attach(telemetry, audit)
        return mirror

    def verify(self, services: AgentRuntimeServices, run_id: str) -> EvidenceReport:
        return self.verify_stores(
            services.run_root, services.telemetry, services.audit_logs, run_id
        )

    def verify_stores(
        self,
        run_root: Path,
        telemetry: TelemetryStore,
        audit: AuditTranscriptStore,
        run_id: str,
    ) -> EvidenceReport:
        context = EvidenceContext(self.settings, self._parts, run_root, telemetry, audit, run_id)
        findings = [finding for check in CHECKS for finding in check(context)]
        return EvidenceReport(run_id=run_id, findings=findings)
