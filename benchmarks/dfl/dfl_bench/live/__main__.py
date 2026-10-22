"""``python -m dfl_bench.live`` entry point; run it from ``benchmarks/dfl``."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from nailong_agent_sdk.foundations.errors import AgentSdkError

from ..ledger import OutcomeLedger
from ..report import MEASURED_BANNER, primary_hypotheses, render, render_primary, summarize
from .executor import (
    LiveExecutor,
    LiveSettings,
    Throttle,
    UsageTotals,
    api_key_from_environment,
)
from .experiment import LiveConfig, build_live_world, run_live_replicate
from .scenarios import FAMILIES

DEFAULT_MODEL = "accounts/fireworks/models/glm-5p3-flash"
PRIMARY_CELLS = (("live", "linear-dfl"), ("live", "mlp-dfl"))
ROOT = Path(__file__).resolve().parents[4]


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.live", description=MEASURED_BANNER)
    parser.add_argument("--seeds", type=int, nargs="*", default=[4000, 4001, 4002, 4003, 4004])
    parser.add_argument("--heldout-families", nargs="*", default=[], choices=FAMILIES)
    parser.add_argument("--heldout-seed", type=int, default=4100)
    parser.add_argument("--variants-per-family", type=int, default=6)
    parser.add_argument("--per-template", type=int, default=6)
    parser.add_argument("--train-trials", type=int, default=3)
    parser.add_argument("--eval-trials", type=int, default=3)
    parser.add_argument("--pair-trials", type=int, default=3)
    parser.add_argument("--no-mlp", action="store_true")
    parser.add_argument("--cross-models", nargs="*", default=[])
    parser.add_argument("--quantization-floor", type=int, default=0)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--input-token-cap", type=int, default=120_000_000)
    parser.add_argument("--key-env", default="FIREWORKS_API_KEY")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, default=Path("results/live"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    api_key = api_key_from_environment(args.key_env, args.env_file)
    settings = LiveSettings(
        model=args.model,
        concurrency=args.concurrency,
        input_token_cap=args.input_token_cap,
        parameters={
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            **(
                {"reasoning_effort": args.reasoning_effort}
                if args.reasoning_effort != "none"
                else {}
            ),
        },
    )
    digest = hashlib.sha256(
        json.dumps(
            [settings.model, settings.provider, dict(settings.parameters)], sort_keys=True
        ).encode()
    ).hexdigest()[:10]
    ledger = OutcomeLedger(args.out / f"outcomes-{digest}.jsonl", create=True)
    usage = UsageTotals()
    throttle = Throttle(args.concurrency)
    executor = LiveExecutor({}, settings, ledger, api_key, usage, throttle)
    relaxed = LiveExecutor(
        {}, dataclasses.replace(settings, enforce_budget=False), ledger, api_key, usage, throttle
    )

    cross_executors = {}
    for cross_model in args.cross_models:
        cross_settings = dataclasses.replace(settings, model=cross_model)
        cross_digest = hashlib.sha256(
            json.dumps(
                [cross_settings.model, cross_settings.provider, dict(cross_settings.parameters)],
                sort_keys=True,
            ).encode()
        ).hexdigest()[:10]
        cross_executors[cross_model] = LiveExecutor(
            {},
            cross_settings,
            OutcomeLedger(args.out / f"outcomes-{cross_digest}.jsonl", create=True),
            api_key,
            UsageTotals(),
            Throttle(args.concurrency),
        )

    runs = [(f"seed {seed}", "live", seed, None) for seed in args.seeds] + [
        (f"held-out {family}", family, args.heldout_seed + n, family)
        for n, family in enumerate(args.heldout_families)
    ]
    groups: dict[str, list[dict]] = {}
    started = time.perf_counter()
    status = 0
    for label, group, seed, heldout in runs:
        config = LiveConfig(
            seed=seed,
            variants_per_family=args.variants_per_family,
            per_template=args.per_template,
            train_trials=args.train_trials,
            eval_trials=args.eval_trials,
            pair_trials=args.pair_trials,
            run_mlp=not args.no_mlp,
            heldout_family=heldout,
            quantization_floor=args.quantization_floor,
        )
        tasks = build_live_world(config)
        by_id = {t.instance.instance_id: t for t in tasks}
        executor._tasks.update(by_id)
        relaxed._tasks.update(by_id)
        for other in cross_executors.values():
            other._tasks.update(by_id)
        print(f"{label}: {len(tasks)} tasks", flush=True)
        try:
            groups.setdefault(group, []).append(
                run_live_replicate(
                    config, executor, relaxed, tasks, cross_executors=cross_executors
                )
            )
        except AgentSdkError as error:
            print(f"stopped: {error.code}: {error.message}", flush=True)
            status = 3
            break
        _write(args, groups, settings, usage, started)
    _write(args, groups, settings, usage, started)
    return status


def _write(args, groups, settings, usage, started) -> None:
    regimes = {}
    sections = []
    for group, replicates in groups.items():
        if not replicates:
            continue
        summary = summarize(replicates)
        regimes[group] = {"replicates": replicates, "summary": summary}
        sections.append(
            render("live" if group == "live" else f"held-out family `{group}`", replicates, summary)
        )
    primary = primary_hypotheses(regimes, PRIMARY_CELLS)
    if primary:
        sections.append(render_primary(primary))
    registration = Path(__file__).resolve().parents[2] / "PREREGISTRATION_LIVE.md"
    document = {
        "evidence_kind": "measured",
        "banner": MEASURED_BANNER,
        "regimes": regimes,
        "primary_hypotheses": primary,
        "preregistration_sha256": (
            hashlib.sha256(registration.read_bytes()).hexdigest() if registration.exists() else None
        ),
        "usage": dataclasses.asdict(usage),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "git_commit": _git_commit(),
            "model": settings.model,
            "provider": settings.provider,
            "parameters": dict(settings.parameters),
            "wall_seconds": time.perf_counter() - started,
            "command": " ".join(sys.argv),
        },
    }
    (args.out / "live_results.json").write_text(
        json.dumps(document, indent=1, default=str), encoding="utf-8"
    )
    (args.out / "live_tables.md").write_text("\n".join(sections), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
