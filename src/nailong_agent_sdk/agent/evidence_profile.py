# Copyright (c) 2026 David Michael Indraputra

"""One switch that turns on every independent piece of run evidence and checks them together."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..observability.attestation import AttestationVerdict, verify_attestations
from ..observability.audit_log import AuditTranscriptStore
from ..observability.clock_check import find_time_regressions, parse_utc
from ..observability.ed25519 import Ed25519Signer
from ..observability.keyring import KeyRing
from ..observability.recorder import (
    RecorderClient,
    RecorderMirror,
    RecorderVerdict,
    client_ssl_context,
    compare_with_recorder,
    scoped_append_token,
)
from ..observability.run_checkpoints import (
    CheckpointSigner,
    CheckpointSink,
    CheckpointVerdict,
    FileCheckpointSink,
    TerminalLinkState,
    check_run_heads,
)
from ..observability.telemetry_store import TelemetryStore
from ..observability.timestamp import Rfc3161Authority, TimestampAuthority
from ..observability.transparency import verify_run_tree_heads
from ..observability.witness import HttpWitnessSink
from .retention import pruned_heads_lookup
from .run_checkpointer import RunHeadCheckpointer

if TYPE_CHECKING:
    from .runtime import AgentRuntimeServices

PROFILE_SCHEMA = "agent-sdk-evidence-profile-v1"
DEFAULT_CLOCK_SKEW_SECONDS = 5.0


class WitnessSettings(StrictModel):
    url: str = Field(min_length=1)
    append_token_variable: str = Field(min_length=1)
    read_token_variable: str | None = None
    allow_insecure_http: bool = False


class RecorderSettings(StrictModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    append_token_variable: str | None = None
    read_token_variable: str | None = None
    append_token_file: Path | None = None
    read_token_file: Path | None = None
    scoped_append: bool = False
    public_key_file: Path | None = None
    signer_id: str = "recorder"
    tls_ca_file: Path | None = None
    tls_client_certificate_file: Path | None = None
    tls_client_key_file: Path | None = None
    tls_server_hostname: str | None = None
    allow_remote_plaintext: bool = False


class EvidenceSettings(StrictModel):
    schema_version: str = PROFILE_SCHEMA
    signing_key_file: Path | None = None
    keyring_file: Path | None = None
    signer_id: str | None = None
    checkpoint_directory: Path | None = None
    witness: WitnessSettings | None = None
    recorder: RecorderSettings | None = None
    timestamp_authority_url: str | None = None
    trusted_roots: list[Path] = Field(default_factory=list)
    require_recorder: bool = True
    require_timestamps: bool = False
    require_final_attestation: bool = False
    require_ordered_time: bool = False
    max_receipt_delay_seconds: float | None = Field(default=None, gt=0)
    clock_skew_tolerance_seconds: float = Field(default=DEFAULT_CLOCK_SKEW_SECONDS, ge=0)

    @classmethod
    def load(cls, path: Path) -> EvidenceSettings:
        try:
            return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_UNREADABLE",
                f'The evidence profile "{path}" cannot be read: {type(error).__name__}: {error}',
                {"path": str(path)},
            ) from error


class EvidenceFinding(StrictModel):
    check: str
    ok: bool
    detail: str


class EvidenceReport(StrictModel):
    run_id: str
    findings: list[EvidenceFinding]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.findings)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _secret(
    environ: Mapping[str, str], variable: str | None, file: Path | None, label: str
) -> str | None:
    if file is not None:
        try:
            value = Path(file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_TOKEN_MISSING",
                f'The {label} token file "{file}" cannot be read: {type(error).__name__}: {error}',
                {"file": str(file)},
            ) from error
        if not value:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_TOKEN_MISSING",
                f'The {label} token file "{file}" is empty.',
                {"file": str(file)},
            )
        return value
    if variable is None:
        return None
    value = environ.get(variable, "")
    if not value:
        raise AgentSdkError(
            "EVIDENCE_PROFILE_TOKEN_MISSING",
            f"The {label} token is read from the environment variable {variable}, which is not "
            "set or is empty.",
            {"variable": variable},
        )
    return value


class EvidenceProfile:
    """Writer and verifier sides of the evidence an operator keeps outside the agent's reach."""

    def __init__(
        self, settings: EvidenceSettings, environ: Mapping[str, str] | None = None
    ) -> None:
        variables = os.environ if environ is None else environ
        self.settings = settings
        self._signer = self._load_signer(settings)
        self._ring = self._load_ring(settings, self._signer)
        self.sink = self._build_sink(settings, variables)
        if self._signer is not None and self.sink is None:
            raise ValueError(
                "A profile that signs checkpoints needs somewhere outside the run root to keep "
                "them: set checkpoint_directory or witness."
            )
        self._writer, self._reader = self._build_recorder(settings, variables)
        self._recorder_verifier = self._load_recorder_verifier(settings)
        self.timestamper: TimestampAuthority | None = (
            Rfc3161Authority(settings.timestamp_authority_url)
            if settings.timestamp_authority_url
            else None
        )
        self.roots = self._load_roots(settings)
        if settings.require_timestamps and not self.roots:
            raise ValueError(
                "require_timestamps needs trusted_roots: a stamp that cannot be verified proves "
                "nothing about when the record existed."
            )
        if settings.require_final_attestation and (
            self._reader is None or self._recorder_verifier is None
        ):
            raise ValueError(
                "require_final_attestation needs a recorder with a read token and a "
                "public_key_file, because only the recorder can say the run was closed."
            )
        self._require_own_key_valid()

    @staticmethod
    def _load_signer(settings: EvidenceSettings) -> Ed25519Signer | None:
        if settings.signing_key_file is None:
            return None
        key = Path(settings.signing_key_file).read_text(encoding="utf-8").strip()
        return Ed25519Signer(key, signer_id=settings.signer_id)

    @staticmethod
    def _load_ring(settings: EvidenceSettings, signer: Ed25519Signer | None) -> KeyRing | None:
        if settings.keyring_file is not None:
            return KeyRing.from_file(settings.keyring_file)
        if signer is not None:
            return KeyRing.of(signer)
        if settings.recorder is None:
            raise ValueError(
                "An evidence profile needs signing_key_file or keyring_file to check host-signed "
                "evidence, or a recorder whose attestation replaces it; none is set."
            )
        return None

    @staticmethod
    def _build_sink(
        settings: EvidenceSettings, variables: Mapping[str, str]
    ) -> CheckpointSink | None:
        if settings.witness is not None:
            witness = settings.witness
            append = _secret(variables, witness.append_token_variable, None, "witness append")
            return HttpWitnessSink(
                witness.url,
                append_token=append or "",
                read_token=_secret(variables, witness.read_token_variable, None, "witness read"),
                allow_insecure_http=witness.allow_insecure_http,
            )
        if settings.checkpoint_directory is None:
            if settings.recorder is None:
                raise ValueError(
                    "An evidence profile needs somewhere outside the run root to keep evidence: "
                    "set checkpoint_directory, witness or a recorder."
                )
            return None
        return FileCheckpointSink(settings.checkpoint_directory)

    @staticmethod
    def _build_recorder(
        settings: EvidenceSettings, variables: Mapping[str, str]
    ) -> tuple[RecorderClient | None, RecorderClient | None]:
        recorder = settings.recorder
        if recorder is None:
            if settings.require_recorder:
                raise ValueError(
                    "require_recorder is set but no recorder is configured; add a recorder "
                    "section or set require_recorder to false."
                )
            return None, None
        encrypted = recorder.tls_ca_file is not None
        remote = not _is_loopback(recorder.host)
        if remote and not encrypted and not recorder.allow_remote_plaintext:
            raise ValueError(
                f'The recorder host "{recorder.host}" is not a loopback address and no '
                "tls_ca_file is set, so its tokens and every record would cross the network in "
                "clear text; set tls_ca_file, tunnel the connection, or set allow_remote_plaintext."
            )
        secret = _secret(
            variables, recorder.append_token_variable, recorder.append_token_file, "recorder append"
        )
        if not secret:
            raise ValueError(
                "A recorder needs an append token: set append_token_variable or append_token_file."
            )
        token: str | Callable[[str], str] = secret
        if recorder.scoped_append:

            def token(run_id: str, _secret: str = secret) -> str:
                return scoped_append_token(_secret, run_id)

        read = _secret(
            variables, recorder.read_token_variable, recorder.read_token_file, "recorder read"
        )
        tls = (
            client_ssl_context(
                recorder.tls_ca_file,
                certificate_file=recorder.tls_client_certificate_file,
                key_file=recorder.tls_client_key_file,
            )
            if recorder.tls_ca_file is not None
            else None
        )
        options: dict[str, Any] = {"tls": tls, "server_hostname": recorder.tls_server_hostname}
        writer = RecorderClient(recorder.host, recorder.port, append_token=token, **options)
        reader = (
            RecorderClient(
                recorder.host, recorder.port, append_token=token, read_token=read, **options
            )
            if read is not None
            else None
        )
        return writer, reader

    @staticmethod
    def _load_recorder_verifier(settings: EvidenceSettings) -> Ed25519Signer | None:
        recorder = settings.recorder
        if recorder is None or recorder.public_key_file is None:
            return None
        public = Path(recorder.public_key_file).read_text(encoding="utf-8").strip()
        return Ed25519Signer(public_key=public, signer_id=recorder.signer_id)

    @staticmethod
    def _load_roots(settings: EvidenceSettings) -> list[Any]:
        from cryptography import x509

        roots = []
        for path in settings.trusted_roots:
            try:
                roots.append(x509.load_pem_x509_certificate(Path(path).read_bytes()))
            except (OSError, ValueError) as error:
                raise AgentSdkError(
                    "EVIDENCE_PROFILE_ROOT_UNREADABLE",
                    f'The trusted root "{path}" cannot be read as a PEM certificate: '
                    f"{type(error).__name__}: {error}",
                    {"path": str(path)},
                ) from error
        return roots

    def _require_own_key_valid(self) -> None:
        if self._signer is None or self._ring is None:
            return
        _, problem = self._ring.resolve(self._signer.signer_id, datetime.now(UTC).isoformat())
        if problem is not None:
            raise ValueError(
                f'The profile signs as "{self._signer.signer_id}" but its key ring does not '
                f"accept that key now: {problem}. Evidence written with it would fail verification."
            )

    @property
    def verifier(self) -> CheckpointSigner | None:
        return self._ring.verifier() if self._ring is not None else None

    def checkpointer(self) -> RunHeadCheckpointer | None:
        if self._signer is None:
            if self._writer is None:
                raise AgentSdkError(
                    "EVIDENCE_PROFILE_CANNOT_SIGN",
                    "This evidence profile has neither a signing_key_file nor a recorder, so it "
                    "can verify evidence but not write it.",
                )
            return None
        assert self.sink is not None
        return RunHeadCheckpointer(
            signer=self._signer,
            sink=self.sink,
            tree_heads=True,
            signed_tombstones=True,
            verifier=self.verifier,
            timestamper=self.timestamper,
            trusted_roots=tuple(self.roots),
        )

    def mirror(self) -> RecorderMirror | None:
        if self._writer is None:
            return None
        return RecorderMirror(self._writer, required=self.settings.require_recorder)

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
        findings: list[EvidenceFinding] = []
        for label, broken in (
            ("telemetry-chain", telemetry.chain_break(run_id)),
            ("audit-chain", audit.chain_break(run_id)),
        ):
            findings.append(
                EvidenceFinding(
                    check=label,
                    ok=broken is None,
                    detail="intact" if broken is None else f"{broken.kind}: {broken.message}",
                )
            )
        findings.extend(self._host_signed_findings(run_root, telemetry, audit, run_id))
        findings.append(self._time_order_finding(telemetry, audit, run_id))
        findings.extend(self._recorder_findings(telemetry, audit, run_id))
        return EvidenceReport(run_id=run_id, findings=findings)

    def _host_signed_findings(
        self, run_root: Path, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> list[EvidenceFinding]:
        verifier = self.verifier
        if verifier is None or self.sink is None:
            return [
                EvidenceFinding(
                    check="run-heads",
                    ok=True,
                    detail="no host-signed checkpoints are configured; the recorder's "
                    "attestation is the evidence",
                )
            ]
        report = check_run_heads(
            telemetry,
            audit,
            run_id,
            self.sink,
            verifier,
            pruned_lookup=pruned_heads_lookup(run_root, verifier),
            trusted_roots=self.roots or None,
        )
        checked = report.verification
        findings = [
            EvidenceFinding(
                check="run-heads",
                ok=checked.ok,
                detail=f"{checked.verdict.value}: {checked.detail}",
            ),
            self._tombstone_finding(checked),
            self._timestamp_finding(checked),
            EvidenceFinding(
                check="terminal-link",
                ok=report.terminal_link.state is not TerminalLinkState.BROKEN,
                detail=f"{report.terminal_link.state.value}: {report.terminal_link.detail}",
            ),
        ]
        results = verify_run_tree_heads(
            telemetry, audit, run_id, self.sink, verifier, trusted_roots=self.roots or None
        )
        findings.extend(
            EvidenceFinding(
                check=f"tree-heads:{store}",
                ok=item.ok,
                detail=f"{item.verdict.value}: {item.detail}",
            )
            for store, item in results.items()
        )
        return findings

    @staticmethod
    def _tombstone_finding(checked: Any) -> EvidenceFinding:
        if checked.verdict is not CheckpointVerdict.PRUNED:
            return EvidenceFinding(
                check="signed-tombstones", ok=True, detail="no prune to vouch for"
            )
        return EvidenceFinding(
            check="signed-tombstones",
            ok=bool(checked.tombstone_signed),
            detail="the prune is vouched for by a verified signature"
            if checked.tombstone_signed
            else "the run was pruned and no verified signature vouches for it",
        )

    def _timestamp_finding(self, checked: Any) -> EvidenceFinding:
        if not self.settings.require_timestamps:
            return EvidenceFinding(
                check="timestamps",
                ok=True,
                detail=f"{checked.verified_timestamps} of {checked.stamped_checkpoints} stamped "
                "checkpoints verified (not required)",
            )
        complete = (
            checked.checkpoints > 0
            and checked.stamped_checkpoints == checked.checkpoints
            and checked.verified_timestamps == checked.checkpoints
        )
        return EvidenceFinding(
            check="timestamps",
            ok=complete,
            detail=f"{checked.verified_timestamps} of {checked.checkpoints} checkpoints carry a "
            "verified trusted timestamp",
        )

    def _time_order_finding(
        self, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> EvidenceFinding:
        reports = {
            "telemetry": find_time_regressions(
                (event.sequence, event.occurred_at_utc) for event in telemetry.iter_events(run_id)
            ),
            "audit": find_time_regressions(
                (entry.sequence, entry.occurred_at_utc) for entry in audit.iter_entries(run_id)
            ),
        }
        problems = [
            f"{name}: {item.total_regressions} regression(s), first at sequence "
            f"{item.regressions[0].sequence} ({item.regressions[0].seconds_back:.1f}s back)"
            for name, item in reports.items()
            if item.regressions
        ] + [
            f"{name}: {item.unparsable} time(s) that do not parse"
            for name, item in reports.items()
            if item.unparsable
        ]
        required = self.settings.require_ordered_time
        return EvidenceFinding(
            check="time-order",
            ok=not problems or not required,
            detail="recorded times never run backwards"
            if not problems
            else "; ".join(problems) + ("" if required else " (reported, not required)"),
        )

    def _recorder_findings(
        self, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> list[EvidenceFinding]:
        if self._reader is None or self._recorder_verifier is None:
            return [
                EvidenceFinding(
                    check="recorder",
                    ok=not self.settings.require_recorder,
                    detail="no recorder read token and public key are configured, so the "
                    "recorder's copy cannot be compared"
                    + ("; the profile requires it" if self.settings.require_recorder else ""),
                )
            ]
        try:
            comparison = compare_with_recorder(
                telemetry, audit, run_id, self._reader, self._recorder_verifier
            )
            strict = self.settings.require_final_attestation
            unsent = [
                store
                for store, item in comparison.items()
                if strict and item.verdict is RecorderVerdict.ADVANCED
            ]
            findings = [
                EvidenceFinding(
                    check="recorder",
                    ok=all(item.ok for item in comparison.values()) and not unsent,
                    detail="; ".join(
                        f"{store}: {item.verdict.value}" for store, item in comparison.items()
                    )
                    + (
                        f"; the {', '.join(unsent)} ledger holds records the recorder never "
                        "received, which a profile that requires a final attestation does not allow"
                        if unsent
                        else ""
                    ),
                )
            ]
            attested = verify_attestations(
                telemetry,
                audit,
                run_id,
                self._reader.attestation_lines(run_id),
                self._recorder_verifier,
                require_final=self.settings.require_final_attestation,
            )
            findings.append(
                EvidenceFinding(
                    check="recorder-attestation",
                    ok=attested.ok
                    and not (strict and attested.verdict is AttestationVerdict.ADVANCED),
                    detail=f"{attested.verdict.value}: {attested.detail}",
                )
            )
            findings.append(self._receipt_finding(run_id))
        except AgentSdkError as error:
            return [
                EvidenceFinding(check="recorder", ok=False, detail=f"{error.code}: {error.message}")
            ]
        return findings

    def _receipt_finding(self, run_id: str) -> EvidenceFinding:
        assert self._reader is not None
        limit = self.settings.max_receipt_delay_seconds
        skew = self.settings.clock_skew_tolerance_seconds
        late = early = checked = 0
        worst = 0.0
        receipts: dict[str, list[tuple[int, str]]] = {}
        for store in ("telemetry", "audit"):
            entries = self._reader.entries(run_id, store, include_records=False)
            receipts[store] = [
                (item.sequence, item.received_at_utc) for item in entries if item.received_at_utc
            ]
            for item in entries:
                claimed = parse_utc(item.claimed_at_utc or "")
                received = parse_utc(item.received_at_utc or "")
                if claimed is None or received is None:
                    continue
                checked += 1
                delay = (received - claimed).total_seconds()
                worst = max(worst, delay)
                late += limit is not None and delay > limit
                early += delay < -skew
        backwards = sum(
            find_time_regressions(items, tolerance_seconds=skew).total_regressions
            for items in receipts.values()
        )
        problems = []
        if late:
            problems.append(
                f"{late} record(s) reached the recorder more than {limit:g}s after they claim"
            )
        if early:
            problems.append(
                f"{early} record(s) claim a time later than the recorder's clock by over {skew:g}s"
            )
        if backwards:
            problems.append(f"the recorder's own receipt times ran backwards {backwards} time(s)")
        return EvidenceFinding(
            check="receipt-times",
            ok=not problems,
            detail="; ".join(problems)
            if problems
            else f"{checked} record(s) checked against the recorder's clock; the longest delay "
            f"between a record's claimed time and its receipt was {worst:.1f}s",
        )
