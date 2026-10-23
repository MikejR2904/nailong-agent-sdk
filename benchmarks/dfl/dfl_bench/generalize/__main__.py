"""CLI: ``python -m dfl_bench.generalize run|report``. Offline, synthetic oracle, no model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .protocol import Config, fold_names, run_job
from .report import render


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.generalize")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--seeds", type=int, nargs="+", required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--folds", nargs="*", default=None)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--templates", type=int, default=4)
    run.add_argument("--per-template", type=int, default=4)
    run.add_argument("--no-boundary", action="store_true")
    run.add_argument("--plan", choices=["full", "shrink"], default="full")
    report = sub.add_parser("report")
    report.add_argument("--results", type=Path, required=True)
    report.add_argument("--protocol", type=Path, required=True)
    report.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.command == "run":
        config = Config(
            templates=args.templates,
            per_template=args.per_template,
            boundary=not args.no_boundary and args.plan == "full",
            plan=args.plan,
        )
        folds = args.folds or fold_names()
        args.out.mkdir(parents=True, exist_ok=True)
        jobs = [
            (seed, fold, config)
            for seed in args.seeds
            for fold in folds
            if not (args.out / f"seed{seed}__{fold.replace(':', '_')}.json").exists()
        ]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for job, result in zip(jobs, pool.map(run_job, jobs), strict=True):
                target = args.out / f"seed{job[0]}__{job[1].replace(':', '_')}.json"
                target.write_text(json.dumps(result, indent=1), encoding="utf-8")
                print(f"{target.name}: {result['seconds']:.0f}s failures={result['failures']}")
        return 0
    results = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(args.results.glob("seed*.json"))
    ]
    text = render(results, _file_hash(args.protocol), args.label or args.results.name)
    (args.results / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
