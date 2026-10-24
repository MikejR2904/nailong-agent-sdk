# Copyright (c) 2026 David Michael Indraputra

"""Anchored graph-run records (V2.2f/g): attack a finished record, ask the store and the anchor."""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.state.coordination_records import _hash_run
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import (
    DependencyProof,
    DependencyRule,
    ModelTier,
    Plan,
    PlanTask,
    SignalRole,
    TaskSignalUse,
)
from nailong_agent_sdk.state.run_anchor import (
    RunAnchorVerdict,
    run_anchor_name,
    verify_run_anchor,
)
from nailong_agent_sdk.state.run_state_store import RunStateStore

from ..systems.nailong import CHECKPOINT_KEY

CLASSES = ("edit", "reseal", "truncate", "result", "delete")
SIGNER = HmacSha256Signer(CHECKPOINT_KEY, signer_id="trust-bench")


def _task(task_id: str, signals: list[str], uses: list[tuple[str, SignalRole]], deps: list[str]):
    return PlanTask(
        task_id=task_id,
        scope=f"scope-{task_id}",
        locked_interface={"signals": signals},
        instructions="do it",
        dependencies=deps,
        acceptance_criteria="passes",
        model_tier=ModelTier.CHEAP,
        signal_uses=[
            TaskSignalUse(
                task_id=task_id,
                signal_id=signal,
                role=role,
                source_spans=["span"],
                scope_pointer="p",
            )
            for signal, role in uses
        ],
    )


def chain_plan(rng: random.Random) -> Plan:
    length = rng.randint(3, 6)
    tasks = [_task("T0000", ["s0"], [("s0", SignalRole.PRODUCE)], [])]
    proofs = []
    for index in range(1, length):
        tasks.append(
            _task(
                f"T{index:04d}",
                [f"s{index - 1}", f"s{index}"],
                [(f"s{index - 1}", SignalRole.CONSUME), (f"s{index}", SignalRole.PRODUCE)],
                [f"T{index - 1:04d}"],
            )
        )
        proofs.append(
            DependencyProof(
                parent_task_id=f"T{index - 1:04d}",
                child_task_id=f"T{index:04d}",
                shared_signal_ids=[f"s{index - 1}"],
                applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
                source_spans=["s"],
            )
        )
    return Plan(plan_id=f"plan-{rng.randrange(10**6)}", tasks=tasks, dependency_proofs=proofs)


async def run_ok(node: Any, context: Any) -> GraphNodeResult:
    return GraphNodeResult(status=GraphNodeStatus.COMPLETED, output={"node": node.node_id})


def rewrite_fresh(root: Path, scratch: Path, run_id: str, change: Any) -> None:
    source = RunStateStore(root, read_only=True)
    record = source.load(run_id)
    changed = change(record)
    changed = changed.model_copy(
        update={
            "run_hash": _hash_run(
                changed.run_id,
                changed.plan_id,
                changed.graph,
                changed.plan_validation,
                changed.cancelled,
            )
        }
    )
    shutil.rmtree(scratch, ignore_errors=True)
    RunStateStore(scratch).save(changed)
    for suffix in (".json", ".history.jsonl"):
        shutil.copy(
            scratch / ".agent-runs" / f"{run_id}{suffix}",
            root / ".agent-runs" / f"{run_id}{suffix}",
        )


def attack(klass: str, rng: random.Random, root: Path, scratch: Path, run_id: str) -> None:
    runs = root / ".agent-runs"
    if klass == "edit":
        history = runs / f"{run_id}.history.jsonl"
        lines = history.read_text("utf-8").splitlines()
        index = rng.randrange(len(lines))
        entry = json.loads(lines[index])
        entry["value"]["reason"] = "edited in place"
        lines[index] = json.dumps(entry, separators=(",", ":"))
        history.write_text("".join(f"{line}\n" for line in lines), "utf-8")
    elif klass == "reseal":

        def change(record: Any) -> Any:
            graph = json.loads(json.dumps(record.graph))
            index = rng.randrange(len(graph["events"]))
            graph["events"][index]["reason"] = "rewritten after the fact"
            return record.model_copy(update={"graph": graph})

        rewrite_fresh(root, scratch, run_id, change)
    elif klass == "truncate":

        def change(record: Any) -> Any:
            graph = json.loads(json.dumps(record.graph))
            graph["events"] = graph["events"][: rng.randint(1, len(graph["events"]) - 1)]
            return record.model_copy(update={"graph": graph})

        rewrite_fresh(root, scratch, run_id, change)
    elif klass == "result":

        def change(record: Any) -> Any:
            graph = json.loads(json.dumps(record.graph))
            node = rng.choice(sorted(graph["results"]))
            graph["results"][node]["reason"] = "rewritten after the fact"
            return record.model_copy(update={"graph": graph})

        rewrite_fresh(root, scratch, run_id, change)
    else:
        (runs / f"{run_id}.json").unlink()


def staterun_job(job: tuple[int, str]) -> dict[str, Any]:
    seed, klass = job
    rng = random.Random(f"staterun:{seed}:{klass}")
    base = Path(tempfile.mkdtemp(prefix="staterun-"))
    try:
        root = base / "root"
        sink = FileCheckpointSink(base / "elsewhere")
        coordinator = HarnessCoordinator(root, anchorer=RunHeadCheckpointer(SIGNER, sink))
        started = coordinator.start_run(chain_plan(rng))
        run_id = started.run_id
        asyncio.run(coordinator.execute_run(run_id, {GraphNodeKind.AGENT: run_ok}))
        baseline = verify_run_anchor(
            RunStateStore(root, read_only=True), sink, run_anchor_name(run_id), run_id, SIGNER
        )
        attack(klass, rng, root, base / "scratch", run_id)
        own = True
        try:
            RunStateStore(root, read_only=True).load(run_id)
            own = False
        except ValueError:
            own = True
        anchored = verify_run_anchor(
            RunStateStore(root, read_only=True), sink, run_anchor_name(run_id), run_id, SIGNER
        )
        return {
            "seed": seed,
            "klass": klass,
            "untouched_verdict": baseline.verdict.value,
            "false_alarm": baseline.verdict is not RunAnchorVerdict.MATCHES,
            "own_check_detects": own,
            "anchor_detects": not anchored.ok,
            "anchor_verdict": anchored.verdict.value,
        }
    except Exception as error:
        return {"seed": seed, "klass": klass, "error": f"{type(error).__name__}: {error}"}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def staterun_key(job: tuple[int, str]) -> str:
    return f"staterun:{job[0]}:{job[1]}"


def enumerate_staterun_jobs(seeds: list[int]) -> list[tuple[int, str]]:
    return [(seed, klass) for seed in seeds for klass in CLASSES]
