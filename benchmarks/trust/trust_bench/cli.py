# Copyright (c) 2026 David Michael Indraputra

"""Command line: gates, run, tamper, verify, attack, authority, report, lock and verify-lock, and
the v2 batteries (boundary, crash, staterun, lineage, realruns, source-tests, report-v2)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .authority.jobs import authority_job, chain_job, enumerate_authority_jobs, enumerate_chain_jobs
from .confirmatory import AUTHORITY_FAMILIES, confirmatory_plan, require_within_plan
from .gates import run_gates
from .jobs import enumerate_jobs, parse_seeds, read_results, run_jobs, write_results
from .judge import commands as judge_commands
from .judge.score import merge_acceptance, merge_diagnosis
from .lock import (
    LOCK_FILE,
    LockError,
    attack_job_key,
    authority_job_key,
    chain_job_key,
    create_lock,
    eval_job_key,
    register_test_jobs,
    tamper_job_key,
    verify_job_key,
    verify_lock,
)
from .report import render
from .splits import Split
from .tamper_jobs import ARMS as TAMPER_ARMS
from .tamper_jobs import tamper_job
from .v2 import commands as v2_commands
from .verify_jobs import (
    ATTACK_FAMILIES,
    VERIFY_ARMS,
    attack_job,
    enumerate_attack_jobs,
    enumerate_verify_jobs,
    verify_job,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


@dataclass(frozen=True)
class JobKind:
    name: str
    help: str
    families: str
    arms: str
    enumerate: Callable[[argparse.Namespace, list[str], list[str], list[int]], list[Any]]
    key: Callable[[Any], str]
    worker: Callable[[Any], dict[str, Any]] | None
    noun: str


KINDS = {
    kind.name: kind
    for kind in (
        JobKind(
            "run",
            "run cases and write per-run results",
            "doc,data",
            "sdk,langgraph",
            lambda args, families, arms, seeds: enumerate_jobs(args.split, families, arms, seeds),
            eval_job_key,
            None,
            "results",
        ),
        JobKind(
            "tamper",
            "run the tamper battery",
            "doc,data",
            ",".join(TAMPER_ARMS),
            lambda args, families, arms, seeds: [
                (family, seed, arm) for family in families for seed in seeds for arm in arms
            ],
            tamper_job_key,
            tamper_job,
            "tamper batteries",
        ),
        JobKind(
            "verify",
            "run every deliverable through the held-out gate and the reference mechanisms",
            "doc,data",
            ",".join(VERIFY_ARMS),
            lambda args, families, arms, seeds: enumerate_verify_jobs(
                args.split, families, arms, seeds
            ),
            verify_job_key,
            verify_job,
            "verifiability results",
        ),
        JobKind(
            "attack",
            "let an agent try to change the reference a gate reads",
            ",".join(ATTACK_FAMILIES),
            ",".join(VERIFY_ARMS),
            lambda args, families, arms, seeds: enumerate_attack_jobs(families, arms, seeds),
            attack_job_key,
            attack_job,
            "attack results",
        ),
        JobKind(
            "chain",
            "run a benign and then a destructive action under one standing approval",
            ",".join(AUTHORITY_FAMILIES),
            "sdk,langgraph",
            lambda args, families, arms, seeds: enumerate_chain_jobs(args.split, arms, seeds),
            chain_job_key,
            chain_job,
            "approval-scope results",
        ),
        JobKind(
            "authority",
            "send tickets to deciders under each allocation arm and read back the records",
            ",".join(AUTHORITY_FAMILIES),
            "sdk,langgraph",
            lambda args, families, arms, seeds: enumerate_authority_jobs(
                args.split, arms, args.allocations.split(","), seeds, args.model_table
            ),
            authority_job_key,
            authority_job,
            "authority results",
        ),
    )
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit() -> str:
    try:
        head = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head}+uncommitted" if dirty else head


def _split(value: str) -> Split:
    return Split(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trust_bench")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gates", help="run the reproduction gates")
    sub.add_parser("lock", help="pin the committed pre-registration and code for the test split")
    sub.add_parser("verify-lock", help="check the working tree still matches the lock")
    for kind in KINDS.values():
        command = sub.add_parser(kind.name, help=kind.help)
        command.add_argument("--split", type=_split, required=True)
        command.add_argument("--families", default=kind.families)
        command.add_argument("--seeds", required=True)
        command.add_argument("--workers", type=int, default=4)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--arms", default=kind.arms)
        command.add_argument(
            "--rerun-reason",
            help="test split only: record why jobs already run are run again (a deviation)",
        )
        if kind.name == "authority":
            command.add_argument("--allocations", default="rule,all-human,none,random")
            command.add_argument(
                "--model-table", help="the allocations the judge wrote, needed by the model arm"
            )
    judge_commands.register(sub)
    v2_commands.register(sub)
    report = sub.add_parser("report", help="render a markdown report")
    for name in (
        "results",
        "tamper",
        "verify",
        "attacks",
        "authority",
        "chains",
        "judge-diagnosis",
        "judge-acceptance",
    ):
        report.add_argument(f"--{name}", type=Path)
    report.add_argument("--out", type=Path, required=True)
    report.add_argument("--title", default="Trust Profile report")
    return parser


def _guard_test_split(
    repo: Path,
    *,
    kind: str,
    families: list[str],
    arms: list[str],
    seeds: list[int],
    extra: dict[str, list[str]],
    keys: list[str],
    rerun_reason: str | None,
    command: str,
) -> None:
    lock = verify_lock(repo)
    require_within_plan(
        lock["test_plan"], kind=kind, families=families, arms=arms, seeds=seeds, extra=extra
    )
    register_test_jobs(repo, keys, command, rerun_reason)


def _run_kind(args: argparse.Namespace, repo: Path, command_text: str) -> int:
    kind = KINDS[args.command]
    families = args.families.split(",")
    arms = args.arms.split(",")
    seeds = parse_seeds(args.seeds, args.split)
    jobs = kind.enumerate(args, families, arms, seeds)
    if args.split is Split.TEST:
        extra = {"allocations": args.allocations.split(",")} if kind.name == "authority" else {}
        _guard_test_split(
            repo,
            kind=kind.name,
            families=families,
            arms=arms,
            seeds=seeds,
            extra=extra,
            keys=[kind.key(job) for job in jobs],
            rerun_reason=args.rerun_reason,
            command=command_text,
        )
    if kind.worker is None:
        count = write_results(run_jobs(jobs, args.workers), args.out)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            count = write_results(pool.map(kind.worker, jobs, chunksize=1), args.out)
    print(f"{count} {kind.noun} written to {args.out}")
    return 0


def main(argv: Sequence[str] | None = None, *, repo: Path | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = REPO if repo is None else repo
    command_text = "trust_bench " + " ".join(argv if argv is not None else sys.argv[1:])

    if args.command == "gates":
        gates = run_gates()
        for gate in gates:
            print(("PASS" if gate.passed else "FAIL"), gate.name, "|", gate.source)
        return 0 if all(g.passed for g in gates) else 1
    if args.command == "lock":
        lock = create_lock(repo, confirmatory_plan())
        print(f"locked at commit {lock['commit']}")
        print(f"preregistration {lock['preregistration_sha256']}")
        print(f"harness         {lock['harness_sha256']}")
        print(f"system          {lock['system_sha256']}")
        print(f"{LOCK_FILE} written; commit it, then record the hash above somewhere outside git.")
        return 0
    if args.command == "verify-lock":
        lock = verify_lock(repo)
        print(f"lock holds: locked at {lock['commit'][:12]}, tree unchanged since")
        return 0
    if args.command in KINDS:
        return _run_kind(args, repo, command_text)
    if args.command in v2_commands.COMMANDS:
        return v2_commands.dispatch(args)
    if args.command in judge_commands.COMMANDS:
        return judge_commands.dispatch(args, repo, command_text) or 0
    loaded = {
        name: read_results(path) if path else []
        for name, path in (
            ("results", args.results),
            ("tamper", args.tamper),
            ("verify", args.verify),
            ("attacks", args.attacks),
            ("authority", args.authority),
            ("chains", args.chains),
            ("judge-diagnosis", args.judge_diagnosis),
            ("judge-acceptance", args.judge_acceptance),
        )
    }
    if loaded["judge-diagnosis"]:
        loaded["results"] = merge_diagnosis(loaded["results"], loaded["judge-diagnosis"])
    if loaded["judge-acceptance"]:
        loaded["verify"] = merge_acceptance(loaded["verify"], loaded["judge-acceptance"])
    header = [f"Commit `{_commit()}`; {len(loaded['results'])} runs."]
    text = render(
        args.title,
        loaded["results"],
        run_gates(),
        loaded["tamper"],
        header,
        loaded["verify"],
        loaded["attacks"],
        loaded["authority"],
        loaded["chains"],
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"report written to {args.out}")
    return 0


def entry() -> int:
    try:
        return main()
    except LockError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(entry())
