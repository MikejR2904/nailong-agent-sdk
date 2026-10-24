# Copyright (c) 2026 David Michael Indraputra

"""Tamper the SDK's three stores in a copy of one run and ask its own verifiers."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from nailong_agent_sdk.developer_tools.diagnose import open_ledgers
from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence
from nailong_agent_sdk.foundations.hashing import canonical_hash, canonical_json
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.audit_log import AuditLogEntry
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    TerminalLinkState,
    check_run_heads,
    verify_terminal_link,
)
from nailong_agent_sdk.observability.telemetry_models import TelemetryEvent
from nailong_agent_sdk.tools.artifact_anchor import artifact_anchor_name, verify_artifact_anchor
from nailong_agent_sdk.tools.artifacts import ArtifactStore

from ..tamper import CLASSES, TamperReport, TamperTrial, positions, tamper_lines
from .nailong import CHECKPOINT_KEY, SdkRun

STORES = ("audit", "telemetry", "artifacts")
SYSTEM = "nailong-agent-sdk"


def _copy(run: SdkRun, target: Path) -> tuple[Path, Path, Path | None]:
    root = target / "run"
    shutil.copytree(run.run_root / ".agent-audit-logs", root / ".agent-audit-logs")
    shutil.copytree(run.run_root / ".agent-telemetry", root / ".agent-telemetry")
    workspace = root / "workspaces" / file_safe_name(run.run_id)
    workspace.mkdir(parents=True)
    shutil.copytree(run.workspace / ".agent-artifacts", workspace / ".agent-artifacts")
    for name in (".agent-tool-results", ".agent-project-state"):
        if (run.run_root / name).is_dir():
            shutil.copytree(run.run_root / name, root / name)
    anchor = None
    if run.anchor_dir is not None:
        anchor = target / "anchor"
        shutil.copytree(run.anchor_dir, anchor)
    return root, workspace, anchor


def verify(run_id: str, root: Path, workspace: Path, anchor: Path | None) -> tuple[str, ...]:
    failures: list[str] = []
    try:
        with open_ledgers(root, run_id) as ledgers:
            assert ledgers.telemetry is not None and ledgers.audit is not None
            if ledgers.telemetry.chain_break(run_id) is not None:
                failures.append("telemetry-chain")
            if ledgers.audit.chain_break(run_id) is not None:
                failures.append("audit-chain")
            link = verify_terminal_link(ledgers.telemetry, ledgers.audit, run_id)
            if link.state is not TerminalLinkState.LINKED:
                failures.append(f"terminal-link:{link.state.value}")
            if anchor is not None:
                signer = HmacSha256Signer(CHECKPOINT_KEY, signer_id="trust-bench")
                report = check_run_heads(
                    ledgers.telemetry, ledgers.audit, run_id, FileCheckpointSink(anchor), signer
                )
                if report.verification.verdict is not CheckpointVerdict.MATCHES:
                    failures.append(f"checkpoint:{report.verification.verdict.value}")
    except Exception as error:
        failures.append(f"unreadable:{type(error).__name__}")
    try:
        store = ArtifactStore(workspace, read_only=True)
        for path in store.paths():
            for version in store.history(path):
                store.read_bytes(version.sha256)
        history = store.verify_history()
        if not history.ok:
            failures.append(f"artifact-history:line-{history.broken_at}")
        if anchor is not None:
            signer = HmacSha256Signer(CHECKPOINT_KEY, signer_id="trust-bench")
            anchored = verify_artifact_anchor(
                store, FileCheckpointSink(anchor), artifact_anchor_name(run_id), signer
            )
            if not anchored.ok:
                failures.append(f"artifact-anchor:{anchored.verdict.value}")
    except Exception as error:
        failures.append(f"artifact-store:{type(error).__name__}")
    try:
        evidence = verify_project_evidence(root, run_id)
        if not evidence.verified:
            failures.append(f"journal:{len(evidence.mismatches)}-mismatches")
    except Exception as error:
        failures.append(f"journal-unreadable:{type(error).__name__}")
    return tuple(failures)


def native_failures(run: SdkRun) -> list[str]:
    failures: list[str] = []
    for run_id in run.audit_run_ids:
        found = verify(run_id, run.run_root, run.workspace, run.anchor_dir)
        failures += [f"{run_id}:{item}" for item in found]
    return failures


def _audit_path(root: Path, run_id: str) -> Path:
    return root / ".agent-audit-logs" / f"{file_safe_name(run_id)}.jsonl"


def _audit_mutate(line: str) -> str:
    body = json.loads(line)
    body["payload"] = {**(body.get("payload") or {}), "tampered": True}
    return json.dumps(body, sort_keys=True)


def _audit_reseal(lines: list[str], start: int) -> list[str]:
    sealed = list(lines)
    previous = (
        AuditLogEntry.model_validate_json(sealed[start - 1]).integrity_hash if start > 0 else None
    )
    for index in range(start, len(sealed)):
        entry = AuditLogEntry.model_validate_json(sealed[index])
        draft = entry.model_copy(update={"previous_hash": previous, "integrity_hash": ""})
        done = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        sealed[index] = done.model_dump_json()
        previous = done.integrity_hash
    return sealed


def _lines_of(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _telemetry_lines(database: Path, run_id: str) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT event_json FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            )
        ]
    finally:
        connection.close()


def _telemetry_store(database: Path, run_id: str, lines: list[str]) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        for sequence, raw in enumerate(lines, start=1):
            event = TelemetryEvent.parse_stored(raw)
            connection.execute(
                "INSERT INTO events (event_id, run_id, sequence, occurred_at_utc, event_type, "
                "status, severity, integrity_hash, previous_event_hash, payload_json, event_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id
                    if sequence == event.sequence
                    else f"{event.event_id}-{sequence}",
                    run_id,
                    sequence,
                    event.occurred_at_utc,
                    event.event_type,
                    event.status,
                    event.severity.value,
                    event.integrity_hash,
                    event.previous_event_hash,
                    canonical_json(event.payload),
                    raw,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _telemetry_mutate(line: str) -> str:
    body = json.loads(line)
    body["status"] = "tampered"
    return json.dumps(body, sort_keys=True)


def _telemetry_reseal(lines: list[str], start: int) -> list[str]:
    sealed = list(lines)
    previous = TelemetryEvent.parse_stored(sealed[start - 1]).integrity_hash if start > 0 else None
    for index in range(start, len(sealed)):
        event = TelemetryEvent.parse_stored(sealed[index])
        draft = event.model_copy(
            update={"sequence": index + 1, "previous_event_hash": previous, "integrity_hash": ""}
        )
        done = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        sealed[index] = done.model_dump_json()
        previous = done.integrity_hash
    return sealed


def _artifact_reseal(lines: list[str], start: int) -> list[str]:
    sealed = list(lines)
    previous = json.loads(sealed[start - 1]).get("integrity_hash") if start > 0 else None
    for index in range(start, len(sealed)):
        body = json.loads(sealed[index])
        body.pop("integrity_hash", None)
        body["previous_hash"] = previous
        previous = canonical_hash(body)
        sealed[index] = json.dumps(
            {**body, "integrity_hash": previous}, separators=(",", ":"), ensure_ascii=False
        )
    return sealed


def _artifact_mutate(line: str) -> str:
    body = json.loads(line)
    manifest = body.get("manifest")
    if isinstance(manifest, dict):
        manifest["tool_call_id"] = "forged-call"
        manifest["agent_identity"] = "forged-agent"
    else:
        body["tampered"] = True
    return json.dumps(body, sort_keys=True)


def _tamper_blob(path: Path, klass: str) -> None:
    if klass == "delete":
        path.unlink()
        return
    data = bytearray(path.read_bytes())
    if data:
        data[0] ^= 0x01
    else:
        data.append(0x01)
    path.write_bytes(bytes(data))


def _blob_trials(run: SdkRun, workdir: Path, report: TamperReport, wanted: int) -> None:
    names = sorted(
        item.name
        for item in (run.workspace / ".agent-artifacts" / "content").iterdir()
        if item.is_file()
    )
    for klass in ("edit", "delete"):
        for position in positions(len(names), wanted):
            root, workspace, anchor = _copy(run, workdir / f"content-{klass}-{position}")
            _tamper_blob(workspace / ".agent-artifacts" / "content" / names[position], klass)
            failures = verify(run.run_id, root, workspace, anchor)
            report.trials.append(
                TamperTrial(SYSTEM, "artifact-content", klass, position, bool(failures), failures)
            )
            shutil.rmtree(root.parent, ignore_errors=True)


def _tamper_journal(path: Path, klass: str) -> None:
    if klass == "delete":
        path.unlink()
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    record["result"]["tampered"] = True
    path.write_text(json.dumps(record), encoding="utf-8")


def _journal_trials(run: SdkRun, workdir: Path, report: TamperReport, wanted: int) -> None:
    names = sorted(
        item.name for item in (run.run_root / ".agent-tool-results").glob("result-*.json")
    )
    for klass in ("edit", "delete"):
        for position in positions(len(names), wanted):
            root, workspace, anchor = _copy(run, workdir / f"journal-{klass}-{position}")
            _tamper_journal(root / ".agent-tool-results" / names[position], klass)
            failures = verify(run.run_id, root, workspace, anchor)
            report.trials.append(
                TamperTrial(SYSTEM, "journal", klass, position, bool(failures), failures)
            )
            shutil.rmtree(root.parent, ignore_errors=True)


def tamper_sdk(run: SdkRun, workdir: Path, wanted: int = 10) -> TamperReport:
    report = TamperReport()
    pristine_root, pristine_ws, pristine_anchor = _copy(run, workdir / "pristine")
    report.pristine_checks += 1
    clean = verify(run.run_id, pristine_root, pristine_ws, pristine_anchor)
    if clean:
        report.false_alarms.append(f"{run.run_id}: {', '.join(clean)}")
    sources = {
        "audit": (_lines_of(_audit_path(pristine_root, run.run_id)), _audit_mutate, _audit_reseal),
        "telemetry": (
            _telemetry_lines(pristine_root / ".agent-telemetry" / "telemetry.sqlite3", run.run_id),
            _telemetry_mutate,
            _telemetry_reseal,
        ),
        "artifacts": (
            _lines_of(pristine_ws / ".agent-artifacts" / "history" / "writes.jsonl"),
            _artifact_mutate,
            _artifact_reseal,
        ),
    }
    for store, (lines, mutate, reseal) in sources.items():
        for klass in CLASSES:
            for position in positions(len(lines), wanted):
                tampered = tamper_lines(lines, klass, position, mutate, reseal)
                if tampered is None:
                    continue
                root, workspace, anchor = _copy(run, workdir / f"{store}-{klass}-{position}")
                if store == "audit":
                    _write_lines(_audit_path(root, run.run_id), tampered)
                elif store == "telemetry":
                    _telemetry_store(
                        root / ".agent-telemetry" / "telemetry.sqlite3", run.run_id, tampered
                    )
                else:
                    _write_lines(
                        workspace / ".agent-artifacts" / "history" / "writes.jsonl", tampered
                    )
                failures = verify(run.run_id, root, workspace, anchor)
                report.trials.append(
                    TamperTrial(SYSTEM, store, klass, position, bool(failures), failures)
                )
                shutil.rmtree(root.parent, ignore_errors=True)
    _blob_trials(run, workdir, report, wanted)
    _journal_trials(run, workdir, report, wanted)
    shutil.rmtree(pristine_root.parent, ignore_errors=True)
    return report
