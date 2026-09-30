# Copyright (c) 2026 David Michael Indraputra

"""Command-line interface for read-only Agent SDK developer workflows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from .catalog import build_public_api_catalog, write_public_api_catalog
from .inspect import inspect_run
from .quality import check_source_quality
from .validate import supported_contract_types, validate_contract_file


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic developer command and return a process status."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    commands: dict[str, Callable[[], object]] = {
        "catalog": lambda: _catalog(arguments),
        "validate": lambda: _validate(arguments),
        "inspect-run": lambda: _inspect_run(arguments),
        "quality": lambda: _quality(arguments),
    }
    result = commands[arguments.command]()
    print(json.dumps(_to_json(result), indent=2, sort_keys=True))
    return 0 if getattr(result, "valid", True) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nailong-agent-sdk-dev",
        description="Read-only catalog, contract validation, and run-inspection utilities.",
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


def _quality(arguments: argparse.Namespace) -> object:
    return check_source_quality(
        arguments.checkout_root,
        check_format=not arguments.no_format_check,
    )


def _to_json(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    return model_dump(mode="json") if callable(model_dump) else value


if __name__ == "__main__":
    raise SystemExit(main())
