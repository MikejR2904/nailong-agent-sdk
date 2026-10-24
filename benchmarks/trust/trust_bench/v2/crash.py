# Copyright (c) 2026 David Michael Indraputra

"""Crash recovery (V2.2): kill a run, then check the stores, the checkpoints and the resume."""

from __future__ import annotations

import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.diagnose import open_ledgers
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    check_run_heads,
    export_run_head_checkpoint,
    read_checkpoints,
)
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    artifact_anchor_name,
    verify_artifact_anchor,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore

from ..evaluate import FAMILIES
from ..systems.nailong import CHECKPOINT_KEY, run_id_for
from .crash_child import CRASH_STATUS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT.parents[1] / "src"
MODES = ("clean", "torn")
OK_HEADS = {CheckpointVerdict.MATCHES, CheckpointVerdict.ADVANCED}
OK_ANCHORS = {ArtifactAnchorVerdict.MATCHES, ArtifactAnchorVerdict.ADVANCED}
EARLY_CRASH_POINTS = 6
EARLY_SHARE = 0.7
MAX_CRASH_POINT = 27


def signer() -> HmacSha256Signer:
    return HmacSha256Signer(CHECKPOINT_KEY, signer_id="trust-bench")


def child_environment() -> dict[str, str]:
    import os

    paths = [str(SRC), str(ROOT)]
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    return {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}


class Layout:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = run_dir
        self.run_id = run_id
        self.run_root = run_dir / "run"
        self.workspace = self.run_root / "workspaces" / file_safe_name(run_id)
        self.anchor = run_dir / "anchor"
        self.audit = self.run_root / ".agent-audit-logs" / f"{file_safe_name(run_id)}.jsonl"
        self.artifact_log = self.workspace / ".agent-artifacts" / "history" / "writes.jsonl"
        self.database = self.run_root / ".agent-telemetry" / "telemetry.sqlite3"

    def heads(self) -> Any:
        with open_ledgers(self.run_root, self.run_id) as ledgers:
            return check_run_heads(
                ledgers.telemetry,
                ledgers.audit,
                self.run_id,
                FileCheckpointSink(self.anchor),
                signer(),
            )

    def chains(self) -> tuple[Any, Any]:
        with open_ledgers(self.run_root, self.run_id) as ledgers:
            return ledgers.telemetry.chain_break(self.run_id), ledgers.audit.chain_break(
                self.run_id
            )

    def anchors(self) -> Any:
        store = ArtifactStore(self.workspace, read_only=True)
        return verify_artifact_anchor(
            store,
            FileCheckpointSink(self.anchor),
            artifact_anchor_name(self.run_id),
            signer(),
        )

    def copy_to(self, target: Path) -> Layout:
        shutil.copytree(self.run_dir, target)
        return Layout(target, self.run_id)


def verify_after_crash(layout: Layout) -> dict[str, Any]:
    outcome: dict[str, Any] = {"false_alarms": [], "checkpointed": False}
    try:
        telemetry_break, audit_break = layout.chains()
        if telemetry_break is not None:
            outcome["false_alarms"].append(f"telemetry:{telemetry_break.kind}")
        if audit_break is not None:
            outcome["false_alarms"].append(f"audit:{audit_break.kind}")
        history = ArtifactStore(layout.workspace, read_only=True).verify_history()
        if not history.ok:
            outcome["false_alarms"].append("artifact-history")
        report = layout.heads()
        verdict = report.verification.verdict
        outcome["heads"] = verdict.value
        outcome["checkpointed"] = verdict is not CheckpointVerdict.NO_CHECKPOINT
        if verdict not in OK_HEADS | {CheckpointVerdict.NO_CHECKPOINT}:
            outcome["false_alarms"].append(f"checkpoint:{verdict.value}")
        anchors = layout.anchors()
        outcome["anchor"] = anchors.verdict.value
        if anchors.verdict not in OK_ANCHORS | {ArtifactAnchorVerdict.NO_ANCHOR}:
            outcome["false_alarms"].append(f"anchor:{anchors.verdict.value}")
    except Exception as error:
        outcome["false_alarms"].append(f"unreadable:{type(error).__name__}:{error}")
    return outcome


def resume(layout: Layout) -> dict[str, Any]:
    outcome: dict[str, Any] = {"resume_failures": []}
    sink = FileCheckpointSink(layout.anchor)
    try:
        services = AgentRuntimeServices.open(
            layout.run_root, checkpointer=RunHeadCheckpointer(signer(), sink)
        )
        try:
            services.audit_logs.append(layout.run_id, "resumed", {"by": "bench"})
        finally:
            services.audit_logs.close()
            services.telemetry.close()
    except Exception as error:
        outcome["resume_failures"].append(f"audit:{type(error).__name__}:{error}")
    try:
        ArtifactStore(layout.workspace, durable=True).write_text("resumed.txt", "after the crash\n")
    except Exception as error:
        outcome["resume_failures"].append(f"artifact:{type(error).__name__}:{error}")
    try:
        with open_ledgers(layout.run_root, layout.run_id) as ledgers:
            export_run_head_checkpoint(
                ledgers.telemetry, ledgers.audit, layout.run_id, signer(), sink
            )
    except Exception as error:
        outcome["resume_failures"].append(f"checkpoint:{type(error).__name__}:{error}")
    try:
        telemetry_break, audit_break = layout.chains()
        if telemetry_break is not None or audit_break is not None:
            outcome["resume_failures"].append("chain-after-resume")
        if not ArtifactStore(layout.workspace, read_only=True).verify_history().ok:
            outcome["resume_failures"].append("artifact-history-after-resume")
    except Exception as error:
        outcome["resume_failures"].append(f"verify:{type(error).__name__}:{error}")
    return outcome


def newest_checkpoint(layout: Layout) -> Any:
    path = layout.anchor / f"{file_safe_name(layout.run_id)}.checkpoints.jsonl"
    if not path.exists():
        return None
    records = read_checkpoints(path)
    return records[-1] if records else None


def newest_artifact_anchor(layout: Layout) -> int | None:
    path = (
        layout.anchor / f"{file_safe_name(artifact_anchor_name(layout.run_id))}.checkpoints.jsonl"
    )
    if not path.exists():
        return None
    import json

    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    return int(json.loads(lines[-1])["records"]) if lines else None


def rejoin(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def cut_one(layout: Layout, store: str, keep_extra: int) -> bool | None:
    """Cut one store to ``keep_extra`` records beyond its newest checkpoint (negative: back past
    it) and say whether that was detected; None when the store has nothing to cut there."""

    checkpoint = newest_checkpoint(layout)
    if checkpoint is None:
        return None
    if store == "audit":
        keep = checkpoint.audit_sequence + keep_extra
        lines = layout.audit.read_text("utf-8").splitlines()
        if keep < 1 or keep >= len(lines):
            return None
        layout.audit.write_text(rejoin(lines[:keep]), "utf-8")
        return layout.heads().verification.verdict is CheckpointVerdict.TRUNCATED
    if store == "telemetry":
        keep = checkpoint.telemetry_sequence + keep_extra
        connection = sqlite3.connect(layout.database)
        try:
            top = connection.execute(
                "SELECT MAX(sequence) FROM events WHERE run_id = ?", (layout.run_id,)
            ).fetchone()[0]
            if keep < 1 or top is None or keep >= top:
                return None
            connection.execute(
                "DELETE FROM events WHERE run_id = ? AND sequence > ?", (layout.run_id, keep)
            )
            connection.commit()
        finally:
            connection.close()
        return layout.heads().verification.verdict is CheckpointVerdict.TRUNCATED
    anchored = newest_artifact_anchor(layout)
    if anchored is None:
        return None
    keep = anchored + keep_extra
    if not layout.artifact_log.exists():
        return None
    lines = layout.artifact_log.read_text("utf-8").splitlines()
    if keep < 1 or keep >= len(lines):
        return None
    layout.artifact_log.write_text(rejoin(lines[:keep]), "utf-8")
    return layout.anchors().verdict is ArtifactAnchorVerdict.TRUNCATED


STORES = ("audit", "telemetry", "artifacts")


def cut_all(layout: Layout, base: Path, label: str, keep_extra: int) -> dict[str, bool]:
    found: dict[str, bool] = {}
    for store in STORES:
        scratch = layout.copy_to(base / f"{label}-{store}")
        outcome = cut_one(scratch, store, keep_extra)
        if outcome is not None:
            found[store] = outcome
        shutil.rmtree(scratch.run_dir, ignore_errors=True)
    return found


def crash_job(job: tuple[int, str]) -> dict[str, Any]:
    try:
        return _crash_trial(job)
    except Exception as error:
        return {
            "seed": job[0],
            "mode": job[1],
            "harness_error": f"{type(error).__name__}: {error}",
            "false_alarms": [],
        }


def _crash_trial(job: tuple[int, str]) -> dict[str, Any]:
    seed, mode = job
    rng = random.Random(f"crash:{seed}:{mode}")
    family = ("doc", "data")[seed % 2]
    case = FAMILIES[family](seed)
    run_id = run_id_for(case, None)
    crash_at = (
        rng.randint(1, EARLY_CRASH_POINTS)
        if rng.random() < EARLY_SHARE
        else rng.randint(EARLY_CRASH_POINTS + 1, MAX_CRASH_POINT)
    )
    base = Path(tempfile.mkdtemp(prefix="crash-"))
    try:
        run_dir = base / "run-dir"
        run_dir.mkdir()
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "trust_bench.v2.crash_child",
                str(run_dir),
                family,
                str(seed),
                str(crash_at),
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=child_environment(),
            cwd=str(ROOT),
        )
        crashed = done.returncode == CRASH_STATUS
        result: dict[str, Any] = {
            "seed": seed,
            "mode": mode,
            "family": family,
            "crash_at": crash_at,
            "crashed": crashed,
            "child_status": done.returncode,
        }
        if done.returncode not in (0, CRASH_STATUS):
            result["child_error"] = done.stderr[-600:]
            result["false_alarms"] = ["child-failed"]
            return result
        layout = Layout(run_dir, run_id)
        result.update(verify_after_crash(layout))
        result["cut_past"] = cut_all(layout, base, "past", -1)
        result["cut_exact"] = {
            store: not detected for store, detected in cut_all(layout, base, "exact", 0).items()
        }
        result.update(resume(layout))
        return result
    finally:
        shutil.rmtree(base, ignore_errors=True)


def crash_key(job: tuple[int, str]) -> str:
    return f"crash:{job[0]}:{job[1]}"


def enumerate_crash_jobs(seeds: list[int]) -> list[tuple[int, str]]:
    return [(seed, mode) for seed in seeds for mode in MODES]
