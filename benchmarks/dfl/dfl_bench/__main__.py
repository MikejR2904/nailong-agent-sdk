"""``python -m dfl_bench`` entry point; run it from ``benchmarks/dfl``."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from .experiment import ExperimentConfig, run_replicate
from .report import BANNER, primary_hypotheses, render, render_primary, summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench", description=BANNER)
    parser.add_argument("--regimes", nargs="+", default=["aligned", "shifted"])
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--base-seed", type=int, default=100)
    parser.add_argument("--templates", type=int, default=14)
    parser.add_argument("--per-template", type=int, default=6)
    parser.add_argument("--train-trials", type=int, default=8)
    parser.add_argument("--eval-trials", type=int, default=40)
    parser.add_argument("--no-mlp", action="store_true")
    parser.add_argument("--no-ablations", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    document: dict = {"evidence_kind": "synthetic", "banner": BANNER, "regimes": {}}
    sections: list[str] = []
    started = time.perf_counter()
    for regime in args.regimes:
        replicates = []
        for offset in range(args.replicates):
            config = ExperimentConfig(
                regime=regime,
                seed=args.base_seed + offset,
                templates=args.templates,
                per_template=args.per_template,
                train_trials=args.train_trials,
                eval_trials=args.eval_trials,
                run_mlp=not args.no_mlp,
                run_ablations=not args.no_ablations,
            )
            replicates.append(run_replicate(config))
            print(f"{regime} replicate {offset + 1}/{args.replicates} done", flush=True)
        summary = summarize(replicates)
        document["regimes"][regime] = {"replicates": replicates, "summary": summary}
        sections.append(render(regime, replicates, summary))
    document["primary_hypotheses"] = primary_hypotheses(document["regimes"])
    if document["primary_hypotheses"]:
        sections.append(render_primary(document["primary_hypotheses"]))
    registration = Path(__file__).resolve().parents[1] / "PREREGISTRATION.md"
    document["preregistration_sha256"] = (
        hashlib.sha256(registration.read_bytes()).hexdigest() if registration.exists() else None
    )
    document["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": _git_commit(),
        "wall_seconds": time.perf_counter() - started,
        "command": " ".join(sys.argv),
    }
    (args.out / "synthetic_results.json").write_text(
        json.dumps(document, indent=1, default=str), encoding="utf-8"
    )
    (args.out / "synthetic_tables.md").write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote {args.out / 'synthetic_tables.md'}")
    return 0


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


if __name__ == "__main__":
    sys.exit(main())
