"""Sending recorded runs to an OpenTelemetry collector you named, without slowing the session."""

from __future__ import annotations

import os
import threading
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from nailong_agent_sdk.agent.run_checkpointer import LOCAL_ANCHOR, resolve_checkpointer
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.otlp import (
    OtlpExportLedger,
    OtlpHttpExporter,
    export_runs,
)
from nailong_agent_sdk.observability.otlp_evidence import export_evidence
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

from .console import Console

LEDGER_NAME = ".otlp-export-ledger.json"
HEADERS_VARIABLE = "OTEL_EXPORTER_OTLP_HEADERS"
MAX_RUNS = 200
FINISH_WAIT_SECONDS = 8.0
LIVE_DEBOUNCE_SECONDS = 1.0
LIVE_IDLE_SECONDS = 0.5
ALL_PARTS = ("telemetry", "audit", "integrity")


@dataclass(frozen=True)
class ExportStatus:
    at: str
    ok: bool
    detail: str


def parse_headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in text.split(","):
        if not item.strip():
            continue
        name, separator, value = item.partition("=")
        if not separator or not name.strip():
            raise ValueError(f'{HEADERS_VARIABLE} entry "{item}" must look like NAME=VALUE.')
        headers[name.strip()] = urllib.parse.unquote(value.strip())
    return headers


class TelemetryExport:
    def __init__(
        self,
        console: Console,
        run_root: Path,
        endpoint: str | None,
        environ: Mapping[str, str] | None = None,
        exporter: OtlpHttpExporter | None = None,
        include: tuple[str, ...] = ALL_PARTS,
    ) -> None:
        self._console = console
        self.include = include
        self._run_root = run_root
        self.endpoint = endpoint
        self.last: ExportStatus | None = None
        self._thread: threading.Thread | None = None
        self._warned = False
        self._watched: list[object] = []
        self._changed = threading.Event()
        self._closed = threading.Event()
        self._live: threading.Thread | None = None
        self._exporting = threading.Lock()
        self._exporter = exporter
        if endpoint and exporter is None:
            headers = parse_headers((environ or os.environ).get(HEADERS_VARIABLE, ""))
            self._exporter = OtlpHttpExporter(endpoint, headers=headers, timeout_seconds=5.0)

    @property
    def enabled(self) -> bool:
        return self._exporter is not None

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.endpoint or "").netloc

    def watch(self, services: Any) -> None:
        if not self.enabled or any(item is services for item in self._watched):
            return
        self._watched.append(services)
        services.telemetry.add_append_listener(self._record_written)
        services.audit_logs.add_append_listener(self._record_written)

    def _record_written(self, record: object) -> None:
        self._changed.set()
        if self._live is None and not self._closed.is_set():
            self._live = threading.Thread(target=self._send_as_recorded, daemon=True)
            self._live.start()

    def _send_as_recorded(self) -> None:
        while not self._closed.is_set():
            if not self._changed.wait(LIVE_IDLE_SECONDS):
                continue
            self._closed.wait(LIVE_DEBOUNCE_SECONDS)
            self._changed.clear()
            self._background(final=False)

    def after_turn(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._background, daemon=True)
        self._thread.start()

    def finish(self) -> None:
        if not self.enabled:
            return
        self._closed.set()
        for thread in (self._live, self._thread):
            if thread is not None:
                thread.join(timeout=FINISH_WAIT_SECONDS)
        self._background()

    def export_now(self, *, final: bool = True) -> ExportStatus:
        with self._exporting:
            status = self._export(final)
            self.last = status
        return status

    def _background(self, final: bool = True) -> None:
        status = self.export_now(final=final)
        if not status.ok and not self._warned:
            self._warned = True
            self._console.line(
                self._console.style(
                    f"  telemetry export to {self.host} failed: {status.detail} (see /telemetry)",
                    "dim",
                )
            )

    def _export(self, final: bool) -> ExportStatus:
        assert self._exporter is not None
        moment = datetime.now().astimezone().isoformat(timespec="seconds")
        if not (self._run_root / ".agent-telemetry" / "telemetry.sqlite3").is_file():
            return ExportStatus(moment, True, "nothing recorded yet")
        store = TelemetryStore(self._run_root, read_only=True)
        audit = AuditTranscriptStore(self._run_root, read_only=True)
        ledger = OtlpExportLedger(self._run_root / LEDGER_NAME)
        pieces: list[str] = []
        try:
            run_ids = [item.run_id for item in store.list_runs(limit=MAX_RUNS)]
            if "telemetry" in self.include:
                report = export_runs(store, run_ids, self._exporter, ledger=ledger)
                pieces.append(
                    f"{report.spans} span(s), {report.log_records} log record(s), "
                    f"{report.metric_points} metric point(s)"
                )
                if report.rejected:
                    pieces.append(f"rejected {report.rejected}")
            parts = [name for name in ("audit", "integrity") if name in self.include]
            if parts:
                evidence = export_evidence(
                    store,
                    audit,
                    run_ids,
                    self._exporter,
                    cast(Any, resolve_checkpointer(LOCAL_ANCHOR, self._run_root))
                    if "integrity" in parts
                    else None,
                    self._run_root,
                    ledger=ledger,
                    audit_entries="audit" in parts,
                    judge_missing_checkpoints=final,
                )
                pieces.append(
                    f"{evidence.audit_records} audit entr(ies), "
                    f"{evidence.checkpoint_records} checkpoint(s), "
                    f"{evidence.verdict_records} integrity verdict(s)"
                )
        except (AgentSdkError, OSError, ValueError) as error:
            return ExportStatus(moment, False, f"{type(error).__name__}: {error}")
        finally:
            store.close()
            audit.close()
        return ExportStatus(moment, True, "; ".join(pieces) or "nothing selected to send")
