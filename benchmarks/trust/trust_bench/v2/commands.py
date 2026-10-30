# Copyright (c) 2026 David Michael Indraputra

"""Command line of the v2 batteries: boundary, crash, staterun, lineage, realruns, source-tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..jobs import parse_seeds, read_results, write_results
from ..splits import Split
from . import live as live_module
from . import realruns
from .boundary import boundary_job, enumerate_boundary_jobs
from .crash import crash_job, enumerate_crash_jobs
from .lineage import enumerate_lineage_jobs, lineage_job
from .report import render_v2
from .staterun import enumerate_staterun_jobs, staterun_job

ROOT = Path(__file__).resolve().parents[2]

BATTERIES: dict[str, tuple[str, Callable[[list[int]], list[Any]], Callable[[Any], Any]]] = {
    "boundary": (
        "authority boundary under adversarial conditions",
        enumerate_boundary_jobs,
        boundary_job,
    ),
    "crash": ("kill a run and check recovery", enumerate_crash_jobs, crash_job),
    "staterun": ("attack an anchored graph-run record", enumerate_staterun_jobs, staterun_job),
    "lineage": (
        "lineage on synthetic chains with known ground truth",
        enumerate_lineage_jobs,
        lineage_job,
    ),
}
COMMANDS = frozenset({*BATTERIES, "live", "realruns", "source-tests", "report-v2"})

SOURCE_PROPERTIES: dict[str, list[str]] = {
    "approval consumption is atomic across threads and processes": [
        "tests/tools/test_authority_hardening.py::"
        "test_a_single_use_approval_runs_exactly_one_call_even_under_concurrency",
    ],
    "a revoked approval authorizes nothing": [
        "tests/tools/test_authority_hardening.py::"
        "test_a_revoked_approval_stops_the_next_call_and_names_the_revocation",
    ],
    "an approval of another run or node is refused": [
        "tests/tools/test_authority_hardening.py::"
        "test_an_approval_of_another_run_or_node_never_authorizes_a_call",
    ],
    "an authorizer on the agent decides before a custom executor; none is refused by default": [
        "tests/tools/test_authorization_boundary.py",
        "tests/agent/test_graph_authority.py",
    ],
    "every grant, consumption, revocation, expiry and denial is an event": [
        "tests/tools/test_approval_audit.py",
        "tests/agent/test_task_authority.py",
    ],
    "approver-only revocation over MCP, bound to the request hash": [
        "tests/mcp/test_approval_revocation.py",
    ],
    "an agent cannot grant or revoke its own approvals": [
        "tests/agent/test_agent_cannot_self_authorize.py",
    ],
    "the artifact log is anchored by a signed record outside the workspace": [
        "tests/tools/test_artifact_anchor.py",
        "tests/tools/test_artifact_durability.py",
    ],
    "a service opened with no checkpointer anchors by default, outside the run root": [
        "tests/agent/test_default_anchor.py",
    ],
    "the graph run record is anchored": ["tests/state/test_run_anchor.py"],
    "periodic checkpoints and graph-node anchoring": [
        "tests/agent/test_periodic_checkpoints.py",
        "tests/agent/test_graph_anchors.py",
    ],
    "torn audit and checkpoint tails are read as ending before them and repaired": [
        "tests/links/test_crash_recovery.py",
    ],
    "lineage separates observed relations from inferred ones": [
        "tests/developer_tools/test_attribute_lineage.py",
        "tests/developer_tools/test_attribute_lineage_inputs.py",
    ],
    "an OTLP export with a ledger sends each record once; bounds follow the unit": [
        "tests/observability/test_otlp_idempotence.py",
        "tests/developer_tools/test_export_otlp.py",
    ],
    "the capped search seeds from the adaptive fill, equal to the greedy selection": [
        "tests/foundations/test_optimization_solvers.py",
    ],
    "graph edges cannot be changed in place and the index follows a switch": [
        "tests/state/test_graph_edge_mutation.py",
    ],
}


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name, (help_text, _, _) in BATTERIES.items():
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--seeds", required=True)
        command.add_argument("--workers", type=int, default=4)
        command.add_argument("--out", type=Path, required=True)
    real = sub.add_parser(
        "realruns", help="verify the committed real-agent ledgers (read-only copy)"
    )
    real.add_argument("--out", type=Path, required=True)
    live = sub.add_parser(
        "live", help="drive three small scenarios with a real model (calls the model)"
    )
    live.add_argument("--reps", type=int, default=3)
    live.add_argument("--model", default=live_module.MODEL)
    live.add_argument("--scenarios", default="")
    live.add_argument("--out", type=Path, required=True)
    live.add_argument(
        "--confirm-model-calls",
        action="store_true",
        help="required: the run sends requests to the provider and costs money",
    )
    source = sub.add_parser(
        "source-tests", help="run the source-level property tests and record them"
    )
    source.add_argument("--out", type=Path, required=True)
    report = sub.add_parser("report-v2", help="render the v2 report")
    for item in (
        "tamper",
        "crash",
        "staterun",
        "boundary",
        "lineage",
        "realruns",
        "live",
        "source",
        "v1",
    ):
        report.add_argument(f"--{item}", type=Path)
    report.add_argument("--commit", default="unknown")
    report.add_argument("--out", type=Path, required=True)


def _run(name: str, args: argparse.Namespace) -> int:
    _, enumerate_jobs, worker = BATTERIES[name]
    seeds = parse_seeds(args.seeds, Split.DEV)
    jobs = enumerate_jobs(seeds)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        count = write_results(pool.map(worker, jobs, chunksize=1), args.out)
    print(f"{count} {name} results written to {args.out}")
    return 0


def _realruns(args: argparse.Namespace) -> int:
    rows: list[dict[str, Any]] = []
    for scenario in realruns.scenarios():
        rows.extend(realruns.verify_scenario(scenario))
    count = write_results(rows, args.out)
    print(f"{count} real-agent verification rows written to {args.out}")
    return 0


def _source_tests(args: argparse.Namespace) -> int:
    files = sorted(
        {entry.split("::")[0] for tests in SOURCE_PROPERTIES.values() for entry in tests}
    )
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "junit.xml"
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *files,
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={report}",
            ],
            cwd=str(ROOT.parents[1]),
            capture_output=True,
            text=True,
        )
        outcomes: dict[str, str] = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            module = case.get("classname", "").replace(".", "/")
            key = f"{module}.py::{case.get('name')}"
            failed = case.find("failure") is not None or case.find("error") is not None
            skipped = case.find("skipped") is not None
            outcomes[key] = "failed" if failed else "skipped" if skipped else "passed"
    properties = []
    for statement, tests in SOURCE_PROPERTIES.items():
        selected = [
            (name, outcome)
            for name, outcome in outcomes.items()
            if any(
                name == entry if "::" in entry else name.startswith(entry + "::") for entry in tests
            )
        ]
        properties.append(
            {
                "property": statement,
                "tests": len(selected),
                "passed": sum(1 for _, outcome in selected if outcome == "passed"),
                "failed": [name for name, outcome in selected if outcome == "failed"],
                "files": tests,
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"returncode": done.returncode, "properties": properties}, indent=2), "utf-8"
    )
    print(f"source-level properties written to {args.out}; pytest exit status {done.returncode}")
    return 0 if done.returncode == 0 else 1


def _load(path: Path | None) -> list[dict[str, Any]]:
    return read_results(path) if path is not None and path.exists() else []


def dispatch(args: argparse.Namespace) -> int:
    if args.command in BATTERIES:
        return _run(args.command, args)
    if args.command == "realruns":
        return _realruns(args)
    if args.command == "live":
        if not args.confirm_model_calls:
            print("live calls a paid model; pass --confirm-model-calls to run it.")
            return 2
        names = [item for item in args.scenarios.split(",") if item] or None
        live_module.live_run(args.reps, args.model, args.out, names)
        return 0
    if args.command == "source-tests":
        return _source_tests(args)
    source = (
        json.loads(args.source.read_text("utf-8")) if args.source and args.source.exists() else None
    )
    text = render_v2(
        commit=args.commit,
        tamper=_load(args.tamper),
        crash=_load(args.crash),
        staterun=_load(args.staterun),
        boundary=_load(args.boundary),
        lineage=_load(args.lineage),
        realruns=_load(args.realruns),
        live=_load(args.live),
        source=source,
        v1=str(args.v1) if args.v1 else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"report written to {args.out}")
    return 0
