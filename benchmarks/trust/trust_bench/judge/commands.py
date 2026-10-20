# Copyright (c) 2026 David Michael Indraputra

"""The judge commands: prepare questions, estimate their cost, ask Jev, score the answers."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..confirmatory import require_within_plan
from ..jobs import parse_seeds, write_results
from ..lock import LockError, judge_job_key, register_test_jobs, verify_lock
from ..splits import Split
from . import prepare, run, score
from .budget import dollars
from .cache import AnswerCache
from .client import JevJudge, build_provider

COMMANDS = ("judge-prepare", "judge-estimate", "judge-ask", "judge-score")
EXIT_OVER_BUDGET = 3


def register(sub: Any) -> None:
    command = sub.add_parser("judge-prepare", help="build the questions for Jev, offline")
    command.add_argument("--task", choices=prepare.TASKS, required=True)
    command.add_argument("--split", type=Split, required=True)
    command.add_argument("--families", default="doc,data")
    command.add_argument("--arms", default="sdk,langgraph")
    command.add_argument("--seeds", required=True)
    command.add_argument("--workers", type=int, default=4)
    command.add_argument("--out", type=Path, required=True)
    command = sub.add_parser("judge-estimate", help="count the calls, tokens and dollars")
    command.add_argument("--inputs", type=Path, required=True)
    command = sub.add_parser("judge-ask", help="put the questions to Jev inside a budget")
    command.add_argument("--inputs", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--split", type=Split, required=True)
    command.add_argument("--max-calls", type=int, required=True)
    command.add_argument("--max-input-tokens", type=int, required=True)
    command.add_argument("--workers", type=int, default=4)
    command.add_argument("--cache", type=Path)
    command.add_argument("--receipts", type=Path)
    command.add_argument("--rerun-reason")
    command = sub.add_parser("judge-score", help="score the answers and write rows for the report")
    command.add_argument("--inputs", type=Path, required=True)
    command.add_argument("--answers", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)


def _guard_prepare(args: argparse.Namespace, repo: Path, seeds: list[int]) -> None:
    lock = verify_lock(repo)
    require_within_plan(
        lock["test_plan"],
        kind="judge",
        families=args.families.split(","),
        arms=args.arms.split(","),
        seeds=seeds,
        extra={"tasks": [args.task]},
    )


def require_locked_budget(plan: dict[str, Any], max_calls: int, max_input_tokens: int) -> None:
    locked = plan["judge_budget"]
    if max_calls > locked["max_calls"] or max_input_tokens > locked["max_input_tokens"]:
        raise LockError(
            f"the ceiling of {max_calls} calls and {max_input_tokens:,} input tokens is above the "
            f"locked budget of {locked['max_calls']} calls and {locked['max_input_tokens']:,} "
            "input tokens."
        )


def _seed_of(item: dict[str, Any]) -> int:
    return int(item["meta"]["case_id"].rsplit("-", 1)[1])


def _prepare(args: argparse.Namespace, repo: Path) -> int:
    seeds = parse_seeds(args.seeds, args.split)
    if args.split is Split.TEST:
        _guard_prepare(args, repo, seeds)
    jobs = prepare.prepare_jobs(
        args.task, args.split, args.families.split(","), args.arms.split(","), seeds
    )
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        batches = pool.map(prepare.run_prepare_job, jobs, chunksize=1)
        count = write_results((item for batch in batches for item in batch), args.out)
    print(f"{count} questions for {len(jobs)} jobs written to {args.out}")
    return 0


def _estimate(args: argparse.Namespace) -> int:
    figures = run.estimate(run.read_inputs(args.inputs))
    print(f"{figures['calls']} calls, about {figures['input_tokens']:,} input tokens")
    for task, entry in figures["tasks"].items():
        print(
            f"  {task}: {entry['calls']} calls, {entry['input_tokens']:,} tokens, largest "
            f"{entry['largest']:,}"
        )
    print(f"about ${figures['dollars']:.2f} at ${run.dollars(1_000_000):.3f} per million tokens")
    return 0


def _ask(args: argparse.Namespace, repo: Path, command_text: str) -> int:
    items = run.read_inputs(args.inputs)
    if args.split is Split.TEST:
        lock = verify_lock(repo)
        require_locked_budget(lock["test_plan"], args.max_calls, args.max_input_tokens)
        seeds = sorted({_seed_of(item) for item in items})
        tasks = sorted({item["task"] for item in items})
        families = sorted({item["meta"].get("family", "ops") for item in items})
        arms = sorted({item["meta"].get("arm", "sdk") for item in items})
        require_within_plan(
            lock["test_plan"],
            kind="judge",
            families=[f for f in families if f != "ops"],
            arms=arms,
            seeds=seeds,
            extra={"tasks": tasks},
        )
        answered = {k for k, v in run.read_answers(args.out).items() if v.get("succeeded")}
        keys = [judge_job_key(item["id"]) for item in items if item["id"] not in answered]
        register_test_jobs(repo, keys, command_text, args.rerun_reason)
    cache = AnswerCache(args.cache or args.out.with_suffix(".cache.jsonl"))
    receipts = args.receipts or args.out.with_suffix(".receipts.jsonl")
    budget = run.make_budget(args.max_calls, args.max_input_tokens)
    judge = JevJudge(build_provider(receipts), budget=budget, cache=cache)
    counts = run.ask_items(items, judge, args.out, workers=args.workers)
    spent = budget.spent
    print(
        f"asked {counts['asked']}, cached {counts['cached']}, already answered "
        f"{counts['skipped']}, unavailable {counts['unavailable']}; spent {spent.calls} calls and "
        f"about {spent.input_tokens:,} input tokens (${dollars(spent.input_tokens):.3f})"
    )
    if counts["over_budget"]:
        print(
            f"stopped: {counts['over_budget']} questions were not sent because the ceiling of "
            f"{args.max_calls} calls or {args.max_input_tokens:,} tokens was reached; raise it and "
            "run the command again to continue."
        )
        return EXIT_OVER_BUDGET
    return 0


def _score(args: argparse.Namespace) -> int:
    items = run.read_inputs(args.inputs)
    answers = run.read_answers(args.answers)
    tasks = {item["task"] for item in items}
    if len(tasks) != 1:
        raise ValueError(f"the inputs mix the tasks {sorted(tasks)}; score one task at a time.")
    (task,) = tasks
    unanswered = sum(1 for item in items if item["id"] not in answers)
    if task == prepare.ALLOCATION:
        table, unavailable = score.allocation_table(items, answers)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(table, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        print(f"{len(table)} allocations written to {args.out} ({unavailable} unavailable)")
        return 0
    rows = (
        score.diagnosis_rows(items, answers)
        if task == prepare.DIAGNOSIS
        else score.acceptance_rows(items, answers)
    )
    count = write_results(rows, args.out)
    print(f"{count} rows written to {args.out} ({unanswered} questions had no answer)")
    return 0


def dispatch(args: argparse.Namespace, repo: Path, command_text: str) -> int | None:
    if args.command == "judge-prepare":
        return _prepare(args, repo)
    if args.command == "judge-estimate":
        return _estimate(args)
    if args.command == "judge-ask":
        return _ask(args, repo, command_text)
    if args.command == "judge-score":
        return _score(args)
    return None
