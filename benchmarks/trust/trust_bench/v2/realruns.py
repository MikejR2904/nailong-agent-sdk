# Copyright (c) 2026 David Michael Indraputra

"""Verify the committed real-agent ledgers with the current verifiers (V2.5, real-agent tier).

Each scenario directory is copied first: opening a telemetry database in a repository checkout
would create its ``-wal`` and ``-shm`` files and change the tree. Nothing here calls a model.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from nailong_agent_sdk.developer_tools.diagnose import open_ledgers
from nailong_agent_sdk.observability.run_checkpoints import TerminalLinkState, verify_terminal_link
from nailong_agent_sdk.tools.artifacts import ArtifactStore

REPO = Path(__file__).resolve().parents[4]
EXAMPLES = REPO / "example_runs"


def scenarios(root: Path = EXAMPLES) -> list[Path]:
    return sorted(
        path for path in root.iterdir() if path.is_dir() and (path / ".agent-telemetry").is_dir()
    )


def verify_scenario(scenario: Path) -> list[dict[str, Any]]:
    scratch = Path(tempfile.mkdtemp(prefix="realruns-"))
    rows: list[dict[str, Any]] = []
    try:
        copy = scratch / scenario.name
        shutil.copytree(scenario, copy, ignore=shutil.ignore_patterns("__pycache__"))
        with open_ledgers(copy, "__none__") as probe:
            telemetry = probe.telemetry
            runs = [item.run_id for item in telemetry.list_runs(limit=1_000)] if telemetry else []
        for run_id in runs:
            row: dict[str, Any] = {"scenario": scenario.name, "run_id": run_id}
            try:
                with open_ledgers(copy, run_id) as ledgers:
                    row["events"] = sum(1 for _ in ledgers.telemetry.iter_events(run_id))
                    telemetry_break = ledgers.telemetry.chain_break(run_id)
                    row["telemetry_chain"] = (
                        "intact" if telemetry_break is None else telemetry_break.kind
                    )
                    row["telemetry_detail"] = (
                        None if telemetry_break is None else telemetry_break.message
                    )
                    if ledgers.audit is not None:
                        audit_break = ledgers.audit.chain_break(run_id)
                        row["audit_entries"] = ledgers.audit.entry_count(run_id)
                        row["audit_chain"] = "intact" if audit_break is None else audit_break.kind
                        row["audit_detail"] = None if audit_break is None else audit_break.message
                        link = verify_terminal_link(ledgers.telemetry, ledgers.audit, run_id)
                        row["terminal_link"] = link.state.value
                        row["link_broken"] = link.state is TerminalLinkState.BROKEN
            except Exception as error:
                row["error"] = f"{type(error).__name__}: {error}"
            rows.append(row)
        artifacts = copy / ".agent-artifacts"
        if artifacts.is_dir():
            history = ArtifactStore(copy, read_only=True).verify_history()
            rows.append(
                {
                    "scenario": scenario.name,
                    "run_id": "(artifact write log)",
                    "artifact_history": "intact" if history.ok else "broken",
                    "records": history.records,
                    "chained": history.chained,
                    "detail": history.reason,
                }
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return rows


def realruns_job(name: str) -> list[dict[str, Any]]:
    return verify_scenario(EXAMPLES / name)
