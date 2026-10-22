"""``python -m dfl_bench.evolve``: retrain, gate, promote and roll back a model offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy
from nailong_agent_sdk.memory.retention_outcomes import RetentionOutcomeLog

from .dataset import load_tasks, split_of
from .gate import GatePolicy, evaluate_gate, model_scoring, static_scoring
from .registry import ModelRegistry
from .retrain import retrain, write_candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.evolve", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("retrain", help="train a candidate, gate it, register it")
    train.add_argument("--log", type=Path, required=True)
    train.add_argument("--registry", type=Path, required=True)
    train.add_argument("--model-type", choices=["linear", "mlp"], default="linear")
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--min-tasks", type=int, default=8)
    train.add_argument("--min-pairs", type=int, default=20)
    train.add_argument(
        "--quantization-floor",
        type=int,
        default=1,
        help="the QuantizationPolicy.floor the model will be deployed with",
    )
    promote = commands.add_parser(
        "promote", help="promote a registered version that passed the gate"
    )
    promote.add_argument("--registry", type=Path, required=True)
    promote.add_argument("--version", required=True)
    promote.add_argument("--approver", required=True)
    promote.add_argument("--reason", required=True)
    rollback = commands.add_parser("rollback", help="return to the previously promoted version")
    rollback.add_argument("--registry", type=Path, required=True)
    rollback.add_argument("--approver", required=True)
    rollback.add_argument("--reason", required=True)
    status = commands.add_parser("status", help="show the current version and history")
    status.add_argument("--registry", type=Path, required=True)
    args = parser.parse_args(argv)

    registry = ModelRegistry(args.registry)
    if args.command == "retrain":
        tasks, kind = load_tasks(RetentionOutcomeLog(args.log))
        candidate = retrain(tasks, model_type=args.model_type, seed=args.seed)
        artifact = args.registry / "candidate.json"
        write_candidate(candidate, artifact)
        current = registry.current()
        incumbent = (
            model_scoring(current.model, f"model-{current.digest[:8]}")
            if current is not None
            else static_scoring()
        )
        gate_tasks = [t for t in tasks if split_of(t.task_digest) == "gate"]
        decision = evaluate_gate(
            model_scoring(candidate.model, "candidate"),
            incumbent,
            gate_tasks,
            policy=GatePolicy(min_tasks=args.min_tasks, min_pairs=args.min_pairs),
            quantization=QuantizationPolicy(floor=args.quantization_floor),
        )
        version = registry.register(artifact, decision)
        print(
            json.dumps({"version": version, "evidence_kind": kind, **decision.to_json()}, indent=1)
        )
        return 0 if decision.promote else 2
    if args.command == "promote":
        registry.promote(args.version, approver=args.approver, reason=args.reason)
    elif args.command == "rollback":
        print(
            "now serving:",
            registry.rollback(approver=args.approver, reason=args.reason) or "static default",
        )
    else:
        print(
            json.dumps(
                {"current": registry.current_version(), "history": registry.history()}, indent=1
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
