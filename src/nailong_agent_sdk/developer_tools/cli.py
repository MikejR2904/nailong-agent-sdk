# Copyright (c) 2026 David Michael Indraputra

"""Command-line interface for Agent SDK developer workflows; only ``prune-runs --apply`` writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from ..agent.retention import RetentionPolicy, prune_run_root
from ..foundations.errors import AgentSdkError
from .catalog import build_public_api_catalog, write_public_api_catalog
from .inspect import inspect_run, verify_project_evidence
from .quality import RuffUnavailableError, check_source_quality
from .validate import supported_contract_types, validate_contract_file

EXIT_OK = 0
EXIT_NEGATIVE_VERDICT = 1
EXIT_USAGE = 2
EXIT_OPERATIONAL_ERROR = 3
_OPERATIONAL_ERRORS = (
    OSError,
    ValueError,
    yaml.YAMLError,
    sqlite3.Error,
    RuffUnavailableError,
    AgentSdkError,
)


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic developer command and return a process status."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    commands: dict[str, Callable[[], object]] = {
        "catalog": lambda: _catalog(arguments),
        "validate": lambda: _validate(arguments),
        "inspect-run": lambda: _inspect_run(arguments),
        "verify-evidence": lambda: _verify_evidence(arguments),
        "quality": lambda: _quality(arguments),
        "prune-runs": lambda: _prune_runs(arguments),
    }
    try:
        result = commands[arguments.command]()
    except _OPERATIONAL_ERRORS as error:
        print(
            f"nailong-agent-sdk-dev {arguments.command}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL_ERROR
    print(json.dumps(_to_json(result), indent=2, sort_keys=True))
    return _exit_status(result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nailong-agent-sdk-dev",
        description=(
            "Catalog, contract validation, run-inspection and run-retention utilities; only "
            "prune-runs --apply changes a run root."
        ),
        epilog=(
            f"Exit status: {EXIT_OK} success; {EXIT_NEGATIVE_VERDICT} the artifact was inspected "
            f"and is invalid, failed or has a broken chain; {EXIT_USAGE} command-line usage "
            f"error; {EXIT_OPERATIONAL_ERROR} the input could not be read or the check could "
            "not run."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog", help="Generate the explicit public API catalogue.")
    catalog.add_argument("--output", type=Path, help="Optional JSON output path.")

    validate = subparsers.add_parser("validate", help="Validate a JSON/YAML SDK contract.")
    validate.add_argument("artifact_type", choices=supported_contract_types())
    validate.add_argument("path", type=Path)

    inspect = subparsers.add_parser("inspect-run", help="Verify and summarize a durable run.")
    inspect.add_argument("run_root", type=Path)
    inspect.add_argument("run_id")

    evidence = subparsers.add_parser(
        "verify-evidence",
        help="Check a project's recorded tool-result hashes against the result journal.",
    )
    evidence.add_argument("run_root", type=Path)
    evidence.add_argument("project_id")

    prune = subparsers.add_parser(
        "prune-runs",
        help="Report, or with --apply delete, finished runs idle for longer than a threshold.",
    )
    prune.add_argument("run_root", type=Path)
    prune.add_argument("--older-than-seconds", type=float, required=True)
    prune.add_argument("--keep-most-recent", type=int, default=0)
    prune.add_argument("--max-runs", type=int)
    prune.add_argument("--include-unfinished", action="store_true")
    prune.add_argument("--export-reports", action="store_true")
    prune.add_argument(
        "--apply",
        action="store_true",
        help="Delete the runs; without it the command only reports what it would delete.",
    )

    quality = subparsers.add_parser(
        "quality", help="Run the closed Ruff unused-import, lint, and formatting checks."
    )
    quality.add_argument("checkout_root", type=Path)
    quality.add_argument(
        "--no-format-check",
        action="store_true",
        help="Skip the Ruff formatting check while retaining import and lint checks.",
    )
    return parser


def _catalog(arguments: argparse.Namespace) -> object:
    if arguments.output is None:
        return build_public_api_catalog()
    return write_public_api_catalog(arguments.output)


def _validate(arguments: argparse.Namespace) -> object:
    return validate_contract_file(arguments.path, arguments.artifact_type)


def _inspect_run(arguments: argparse.Namespace) -> object:
    return inspect_run(arguments.run_root, arguments.run_id)


def _verify_evidence(arguments: argparse.Namespace) -> object:
    return verify_project_evidence(arguments.run_root, arguments.project_id)


def _prune_runs(arguments: argparse.Namespace) -> object:
    policy = RetentionPolicy(
        older_than_seconds=arguments.older_than_seconds,
        keep_most_recent=arguments.keep_most_recent,
        max_runs=arguments.max_runs,
        include_unfinished=arguments.include_unfinished,
        export_reports=arguments.export_reports,
    )
    return prune_run_root(arguments.run_root, policy, dry_run=not arguments.apply)


def _quality(arguments: argparse.Namespace) -> object:
    return check_source_quality(
        arguments.checkout_root,
        check_format=not arguments.no_format_check,
    )


_VERDICT_FIELDS = (
    "valid",
    "passed",
    "telemetry_chain_valid",
    "audit_chain_valid",
    "verified",
    "clean",
)


def _exit_status(result: object) -> int:
    negative = any(getattr(result, name, True) is False for name in _VERDICT_FIELDS)
    return EXIT_NEGATIVE_VERDICT if negative else EXIT_OK


def _to_json(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    return model_dump(mode="json") if callable(model_dump) else value


if __name__ == "__main__":
    raise SystemExit(main())
