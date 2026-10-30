# Copyright (c) 2026 David Michael Indraputra

"""Command-line interface for Agent SDK developer workflows; only ``prune-runs --apply`` writes."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import yaml

from ..agent.retention import (
    RetentionPolicy,
    export_tombstone_anchor,
    prune_run_root,
    pruned_heads_lookup,
    verify_tombstone_anchor,
)
from ..agent.retention_inventory import DEFAULT_MAX_ITEMS, inventory_run_root
from ..foundations.errors import AgentSdkError
from ..foundations.suffix_search import SuffixStrategy
from ..observability.audit_log import AuditTranscriptStore
from ..observability.otlp import (
    SIGNALS,
    OtlpExportLedger,
    OtlpHttpExporter,
    OtlpSignal,
    export_runs,
    preview_export,
)
from ..observability.run_checkpoints import (
    HmacSha256Signer,
    check_run_heads,
    checkpoint_path,
    export_run_head_checkpoint,
)
from ..observability.telemetry_store import TelemetryStore
from ..observability.trace_context import graph_run_trace_id
from ..state.run_state_store import RunStateStore
from .attribute_defect import attribute_defect, content_check
from .attribute_failure import attribute_failure
from .attribute_result import attribute_result_defect, result_check
from .attribute_root_cause import attribute_root_cause, text_checks
from .catalog import build_public_api_catalog, write_public_api_catalog
from .diagnose import diagnose_run
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
        "diagnose-run": lambda: _diagnose_run(arguments),
        "attribute-failure": lambda: _attribute_failure(arguments),
        "attribute-defect": lambda: _attribute_defect(arguments),
        "attribute-result": lambda: _attribute_result(arguments),
        "attribute-root-cause": lambda: _attribute_root_cause(arguments),
        "verify-evidence": lambda: _verify_evidence(arguments),
        "quality": lambda: _quality(arguments),
        "prune-runs": lambda: _prune_runs(arguments),
        "inventory": lambda: _inventory(arguments),
        "anchor-tombstones": lambda: _anchor_tombstones(arguments),
        "verify-tombstones": lambda: _verify_tombstones(arguments),
        "vacuum-telemetry": lambda: _vacuum_telemetry(arguments),
        "export-otlp": lambda: _export_otlp(arguments),
        "authority-matrix": lambda: _authority_matrix(),
        "checkpoint-run": lambda: _checkpoint_run(arguments),
        "verify-checkpoints": lambda: _verify_checkpoints(arguments),
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

    diagnose = subparsers.add_parser(
        "diagnose-run",
        help="Report where a run first went wrong, what ended it and what it blocked; reads only.",
    )
    diagnose.add_argument("run_root", type=Path)
    diagnose.add_argument("run_id")

    failure = subparsers.add_parser(
        "attribute-failure",
        help="Rank the nodes that stopped a run by responsibility, show what repairing each would "
        "release and name the model turn behind it; reads only.",
    )
    failure.add_argument("run_root", type=Path)
    failure.add_argument("run_id")
    failure.add_argument(
        "--node",
        help="Explain why this dependency-blocked node is blocked instead of listing the first "
        "blocked nodes.",
    )

    defect = subparsers.add_parser(
        "attribute-defect",
        help="Find which write of an artifact introduced a defect and which agent made it; "
        "reads only.",
    )
    defect.add_argument("run_root", type=Path)
    defect.add_argument("relative_path")
    test = defect.add_mutually_exclusive_group(required=True)
    test.add_argument("--contains", help="The artifact is defective when it contains this text.")
    test.add_argument("--lacks", help="The artifact is defective when it lacks this text.")
    test.add_argument("--matches", help="The artifact is defective when this regex matches.")
    test.add_argument("--sha256", help="The artifact is defective when it has this SHA-256.")
    defect.add_argument(
        "--strategy",
        choices=[item.value for item in SuffixStrategy],
        default=SuffixStrategy.GALLOP.value,
        help="gallop probes O(log d) versions and assumes the defect persists to the newest "
        "version, and the result reports whether its probes prove the boundary; confirmed gallops "
        "and then probes the rest of the bad run, exact for any history; scan probes every "
        "version back to the first clean one.",
    )

    outcome = subparsers.add_parser(
        "attribute-result",
        help="Find the first tool result of an agent run that a text check rejects and the model "
        "turn that asked for it; reads only.",
    )
    outcome.add_argument("run_root", type=Path)
    outcome.add_argument("run_id")
    rejected = outcome.add_mutually_exclusive_group(required=True)
    rejected.add_argument("--contains", help="A result is defective when its JSON has this text.")
    rejected.add_argument("--lacks", help="A result is defective when its JSON lacks this text.")
    rejected.add_argument("--matches", help="A result is defective when this regex matches it.")
    outcome.add_argument("--tool", help="Check only the results of this tool.")

    cause = subparsers.add_parser(
        "attribute-root-cause",
        help="Rank where a failed run began: what stopped it, the model turn behind it and, when "
        "you give checks, the first bad result, the write that introduced a bad artifact and the "
        "earliest origin of bad data; reads only.",
    )
    cause.add_argument("run_root", type=Path)
    cause.add_argument("run_id")
    cause.add_argument(
        "--workspace",
        type=Path,
        help="The directory that holds .agent-artifacts; needed for the artifact checks.",
    )
    cause.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="PATH",
        help="An artifact path whose write history to search; repeatable.",
    )
    for name in ("contains", "lacks", "matches", "sha256"):
        cause.add_argument(
            f"--artifact-{name}",
            help="The artifact check: an artifact is defective when this holds for its content "
            "(one of the four; it also drives the data trace).",
        )
    for name in ("contains", "lacks", "matches"):
        cause.add_argument(
            f"--result-{name}",
            help="The result check: a tool result is defective when this holds for its JSON "
            "(one of the three).",
        )
    cause.add_argument(
        "--strategy",
        choices=[item.value for item in SuffixStrategy],
        default=SuffixStrategy.CONFIRMED.value,
    )
    cause.add_argument("--max-runs", type=int, default=5)
    cause.add_argument(
        "--assume-fault",
        action="store_true",
        help="Analyse a run that completed but whose result your own check rejected.",
    )
    cause.add_argument(
        "--verify-records",
        action="store_true",
        help="Compare each journaled read and write with the artifact store and name a tool "
        "that returned or stored something other than what the store holds; needs --workspace.",
    )
    cause.add_argument(
        "--trace-unchecked",
        action="store_true",
        help="When no content check is given, list the runs that wrote the artifacts the failing "
        "run read as unchecked candidates, earliest first; needs --workspace.",
    )
    cause.add_argument(
        "--related-run",
        action="append",
        default=[],
        metavar="RUN_ID",
        help="Another run of the same pipeline to scan for bad results and record "
        "inconsistencies; repeatable.",
    )

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
        "--anchor",
        type=Path,
        help="Check the tombstone log against this anchor file before pruning and "
        "refresh it afterwards; keep it outside the run root.",
    )
    prune.add_argument(
        "--apply",
        action="store_true",
        help="Delete the runs; without it the command only reports what it would delete.",
    )

    inventory = subparsers.add_parser(
        "inventory",
        help="Report every persistent resource class under a run root and what a policy "
        "would do with it; reads only.",
    )
    inventory.add_argument("run_root", type=Path)
    inventory.add_argument("--older-than-seconds", type=float, required=True)
    inventory.add_argument("--keep-most-recent", type=int, default=0)
    inventory.add_argument("--max-runs", type=int)
    inventory.add_argument("--include-unfinished", action="store_true")
    inventory.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)

    anchor = subparsers.add_parser(
        "anchor-tombstones",
        help="Write the tombstone log's length and head hash to a file kept outside the run root.",
    )
    anchor.add_argument("run_root", type=Path)
    anchor.add_argument("destination", type=Path)

    verify = subparsers.add_parser(
        "verify-tombstones",
        help="Check the tombstone log against an anchor and report truncation or rewriting.",
    )
    verify.add_argument("run_root", type=Path)
    verify.add_argument("anchor", type=Path)

    vacuum = subparsers.add_parser(
        "vacuum-telemetry",
        help="Report the telemetry database's reclaimable space, or with --apply run VACUUM.",
    )
    vacuum.add_argument("run_root", type=Path)
    vacuum.add_argument(
        "--apply",
        action="store_true",
        help="Run VACUUM, which rewrites the database; without it the command only reports.",
    )

    export = subparsers.add_parser(
        "export-otlp",
        help="Send recorded runs to an OTLP/HTTP collector as JSON; reads the run root only.",
    )
    export.add_argument("run_root", type=Path)
    export.add_argument(
        "--endpoint",
        help="Collector base URL (default: the OTEL_EXPORTER_OTLP_ENDPOINT variable).",
    )
    export.add_argument("--run-id", action="append", default=[], dest="run_ids")
    export.add_argument(
        "--graph-run",
        action="append",
        default=[],
        dest="graph_runs",
        help="Export every run recorded under this graph run's trace, including its nodes.",
    )
    export.add_argument("--trace-id", action="append", default=[], dest="trace_ids")
    export.add_argument(
        "--signals",
        default=",".join(SIGNALS),
        help="Comma-separated subset of traces, logs and metrics.",
    )
    export.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Request header; also read from OTEL_EXPORTER_OTLP_HEADERS (NAME=VALUE,...).",
    )
    export.add_argument("--timeout-seconds", type=float, default=10.0)
    export.add_argument("--gzip", action="store_true")
    export.add_argument(
        "--ledger",
        type=Path,
        help="File that remembers what each endpoint was sent; default "
        "<run-root>/.otlp-export-ledger.json, so exporting a run again sends only what is new.",
    )
    export.add_argument(
        "--no-ledger",
        action="store_true",
        help="Send everything again; a collector counts delta metrics and logs twice.",
    )
    export.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the requests and report what would be sent without contacting the collector.",
    )

    subparsers.add_parser(
        "authority-matrix",
        help="Print who may perform each project-state transition and MCP operation, read "
        "from the code that enforces it.",
    )

    run_checkpoint = subparsers.add_parser(
        "checkpoint-run",
        help="Sign the head hashes of a run's telemetry and audit chains and append them to a "
        "file kept outside the run root; reads the run root only.",
    )
    run_checkpoint.add_argument("run_root", type=Path)
    run_checkpoint.add_argument("run_id")
    run_checkpoint.add_argument(
        "destination",
        type=Path,
        help="Directory outside the run root that holds the run's checkpoint file.",
    )
    run_checkpoint.add_argument(
        "--key-file",
        type=Path,
        required=True,
        help="File whose text is the HMAC key (at least 32 bytes); it is never printed.",
    )
    run_checkpoint.add_argument("--signer-id", default="hmac-sha256")

    verify_heads = subparsers.add_parser(
        "verify-checkpoints",
        help="Check a run against its signed checkpoints and report truncation or rewriting; "
        "also checks the cross-link between the run's terminal telemetry event and audit entry.",
    )
    verify_heads.add_argument("run_root", type=Path)
    verify_heads.add_argument("run_id")
    verify_heads.add_argument(
        "checkpoint_file",
        type=Path,
        help="The run's checkpoint file, or the directory that holds it.",
    )
    verify_heads.add_argument("--key-file", type=Path, required=True)
    verify_heads.add_argument("--signer-id", default="hmac-sha256")

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


def _diagnose_run(arguments: argparse.Namespace) -> object:
    return diagnose_run(arguments.run_root, arguments.run_id)


def _attribute_failure(arguments: argparse.Namespace) -> object:
    return attribute_failure(arguments.run_root, arguments.run_id, node_id=arguments.node)


def _attribute_defect(arguments: argparse.Namespace) -> object:
    is_defective, description = content_check(
        contains=arguments.contains,
        lacks=arguments.lacks,
        matches=arguments.matches,
        sha256=arguments.sha256,
    )
    return attribute_defect(
        arguments.run_root,
        arguments.relative_path,
        is_defective,
        strategy=arguments.strategy,
        check=description,
    )


def _attribute_result(arguments: argparse.Namespace) -> object:
    is_defective, description = result_check(
        contains=arguments.contains, lacks=arguments.lacks, matches=arguments.matches
    )
    return attribute_result_defect(
        arguments.run_root,
        arguments.run_id,
        is_defective,
        tool=arguments.tool,
        check=description,
    )


def _attribute_root_cause(arguments: argparse.Namespace) -> object:
    artifact_given = {
        name: getattr(arguments, f"artifact_{name}")
        for name in ("contains", "lacks", "matches", "sha256")
        if getattr(arguments, f"artifact_{name}") is not None
    }
    result_given = {
        name: getattr(arguments, f"result_{name}")
        for name in ("contains", "lacks", "matches")
        if getattr(arguments, f"result_{name}") is not None
    }
    artifact_check = content_check_for_lineage = None
    if artifact_given:
        artifact_check, content_check_for_lineage, _ = text_checks(**artifact_given)
    elif arguments.artifact:
        raise ValueError(
            "--artifact names a path to search but no artifact check was given: pass one of "
            "--artifact-contains, --artifact-lacks, --artifact-matches or --artifact-sha256."
        )
    result_check_function = result_check(**result_given)[0] if result_given else None
    return attribute_root_cause(
        arguments.run_root,
        arguments.run_id,
        workspace=arguments.workspace,
        artifact_checks=(
            dict.fromkeys(arguments.artifact, artifact_check) if artifact_check else None
        ),
        content_check=content_check_for_lineage,
        result_check=result_check_function,
        strategy=arguments.strategy,
        max_runs=arguments.max_runs,
        assume_fault=arguments.assume_fault,
        verify_records=arguments.verify_records,
        related_runs=arguments.related_run,
        trace_unchecked=arguments.trace_unchecked,
    )


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
    return prune_run_root(
        arguments.run_root,
        policy,
        dry_run=not arguments.apply,
        anchor_path=arguments.anchor,
    )


def _inventory(arguments: argparse.Namespace) -> object:
    policy = RetentionPolicy(
        older_than_seconds=arguments.older_than_seconds,
        keep_most_recent=arguments.keep_most_recent,
        max_runs=arguments.max_runs,
        include_unfinished=arguments.include_unfinished,
    )
    return inventory_run_root(arguments.run_root, policy, max_items=arguments.max_items)


def _anchor_tombstones(arguments: argparse.Namespace) -> object:
    return export_tombstone_anchor(arguments.run_root, arguments.destination)


def _verify_tombstones(arguments: argparse.Namespace) -> object:
    return verify_tombstone_anchor(arguments.run_root, arguments.anchor)


def _vacuum_telemetry(arguments: argparse.Namespace) -> object:
    database = arguments.run_root.resolve() / ".agent-telemetry" / "telemetry.sqlite3"
    if not database.is_file():
        raise ValueError(
            f'Run root "{arguments.run_root}" has no telemetry database at "{database}".'
        )
    store = TelemetryStore(arguments.run_root, read_only=not arguments.apply)
    try:
        return store.vacuum() if arguments.apply else store.maintenance_report()
    finally:
        store.close()


def _export_otlp(arguments: argparse.Namespace) -> object:
    signals = _signals(arguments.signals)
    headers = _headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").split(","))
    headers.update(_headers(arguments.header))
    store = TelemetryStore(arguments.run_root, read_only=True)
    try:
        run_ids = list(arguments.run_ids)
        trace_ids = [
            *arguments.trace_ids,
            *(graph_run_trace_id(run) for run in arguments.graph_runs),
        ]
        for trace_id in trace_ids:
            found = store.run_ids_for_trace(trace_id)
            if not found:
                raise ValueError(
                    f"No run in {arguments.run_root} recorded an event under {trace_id}."
                )
            run_ids.extend(run for run in found if run not in run_ids)
        if not run_ids:
            raise ValueError("Name what to export with --run-id, --graph-run or --trace-id.")
        dependencies = _graph_dependencies(arguments.run_root, arguments.graph_runs)
        if arguments.dry_run:
            return preview_export(store, run_ids, signals=signals, graph_dependencies=dependencies)
        endpoint = arguments.endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            raise ValueError("Give --endpoint or set OTEL_EXPORTER_OTLP_ENDPOINT.")
        exporter = OtlpHttpExporter(
            endpoint,
            headers=headers,
            timeout_seconds=arguments.timeout_seconds,
            compress=arguments.gzip,
        )
        ledger = (
            None
            if arguments.no_ledger
            else OtlpExportLedger(
                arguments.ledger or arguments.run_root / ".otlp-export-ledger.json"
            )
        )
        return export_runs(
            store,
            run_ids,
            exporter,
            signals=signals,
            graph_dependencies=dependencies,
            ledger=ledger,
        )
    finally:
        store.close()


def _graph_dependencies(
    run_root: Path, graph_run_ids: list[str]
) -> dict[str, dict[str, list[str]]]:
    if not graph_run_ids or not (run_root.resolve() / ".agent-runs").is_dir():
        return {}
    store = RunStateStore(run_root)
    dependencies: dict[str, dict[str, list[str]]] = {}
    for run_id in graph_run_ids:
        if not store.exists(run_id):
            continue
        graph = store.load(run_id).graph
        required: dict[str, list[str]] = {
            node["node_id"]: list(node.get("dependencies", [])) for node in graph.get("nodes", [])
        }
        for edge in graph.get("edges", []):
            if edge.get("enabled", True) and edge["child_node_id"] in required:
                if edge["parent_node_id"] not in required[edge["child_node_id"]]:
                    required[edge["child_node_id"]].append(edge["parent_node_id"])
        dependencies[run_id] = required
    return dependencies


def _signals(value: str) -> list[OtlpSignal]:
    chosen = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in chosen if item not in SIGNALS]
    if unknown or not chosen:
        raise ValueError(f"--signals takes a subset of {', '.join(SIGNALS)}; got {value!r}.")
    return [signal for signal in SIGNALS if signal in chosen]


def _headers(items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items:
        if not item.strip():
            continue
        name, separator, value = item.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"Header {item!r} must look like NAME=VALUE.")
        headers[name.strip()] = urllib.parse.unquote(value.strip())
    return headers


def _authority_matrix() -> object:
    from ..mcp.authority_matrix import authority_matrix

    return authority_matrix()


def _checkpoint_signer(arguments: argparse.Namespace) -> HmacSha256Signer:
    key = arguments.key_file.read_text(encoding="utf-8").strip()
    return HmacSha256Signer(key, signer_id=arguments.signer_id)


def _checkpoint_run(arguments: argparse.Namespace) -> object:
    signer = _checkpoint_signer(arguments)
    telemetry = TelemetryStore(arguments.run_root, read_only=True)
    audit = AuditTranscriptStore(arguments.run_root, read_only=True)
    try:
        return export_run_head_checkpoint(
            telemetry,
            audit,
            arguments.run_id,
            signer,
            arguments.destination,
            pruned_lookup=pruned_heads_lookup(arguments.run_root),
        )
    finally:
        telemetry.close()
        audit.close()


def _verify_checkpoints(arguments: argparse.Namespace) -> object:
    signer = _checkpoint_signer(arguments)
    source = arguments.checkpoint_file
    if source.is_dir():
        source = checkpoint_path(source, arguments.run_id)
    telemetry = TelemetryStore(arguments.run_root, read_only=True)
    audit = AuditTranscriptStore(arguments.run_root, read_only=True)
    try:
        return check_run_heads(
            telemetry,
            audit,
            arguments.run_id,
            source,
            signer,
            pruned_lookup=pruned_heads_lookup(arguments.run_root),
        )
    finally:
        telemetry.close()
        audit.close()


def _quality(arguments: argparse.Namespace) -> object:
    return check_source_quality(
        arguments.checkout_root,
        check_format=not arguments.no_format_check,
    )


_VERDICT_FIELDS = (
    "ok",
    "valid",
    "passed",
    "telemetry_chain_valid",
    "audit_chain_valid",
    "artifact_history_valid",
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
