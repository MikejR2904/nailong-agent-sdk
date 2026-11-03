# Copyright (c) 2026 David Michael Indraputra

"""Tools an agent proposes, a reviewer approves, tests prove and the host can roll back."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.contracts import (
    EpisodeKind,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
)
from ..foundations.errors import AgentSdkError
from ..tools.sandbox import DockerSandbox, NativeSandbox, SandboxBackend
from .chain_log import ChainedLog
from .forge_execution import (
    DEFAULT_IMAGE,
    DEFAULT_MEMORY_BYTES,
    container_options,
    container_target,
    native_target,
    run_source,
)
from .forge_report import code_report, source_diff

TOOL_PREFIX = "forged_"
MAX_SOURCE_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 500
_NAME = re.compile(r"[a-z][a-z0-9_]{2,40}")
_PROPOSAL_ID = re.compile(r"p-\d{4,}")
PROPOSE_TOOL = "propose_tool"
USE_TOOL = "use_forged_tool"
LIST_TOOLS = "list_forged_tools"


@dataclass(frozen=True)
class ToolProposal:
    id: str
    name: str
    version: int
    parent_version: int | None
    description: str
    input_schema: dict[str, Any]
    source: str
    tests: list[dict[str, Any]]
    reason: str
    source_sha256: str
    diff: str
    report: dict[str, Any]


Reviewer = Callable[[ToolProposal], bool | str | Awaitable[bool | str]]


class Forge:
    def __init__(
        self,
        directory: Path,
        *,
        reviewer: Reviewer | None = None,
        approved_by: str = "reviewer-callback",
        isolation: Literal["docker", "native"] = "docker",
        docker_image: str = DEFAULT_IMAGE,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        cpu_limit: float = 1.0,
        backend: SandboxBackend | None = None,
        timeout_seconds: float = 10.0,
        max_tools: int = 20,
        max_pending: int = 5,
        min_tests: int = 1,
    ) -> None:
        if timeout_seconds <= 0 or max_tools < 1 or max_pending < 1 or min_tests < 0:
            raise ValueError(
                "timeout_seconds must be positive, max_tools and max_pending at least 1, and "
                "min_tests not negative."
            )
        self._directory = directory.resolve()
        self._reviewer = reviewer
        self._approved_by = approved_by
        if isolation not in ("docker", "native"):
            raise ValueError(f'isolation must be "docker" or "native", not "{isolation}".')
        self.isolation = isolation
        if isolation == "docker":
            self._target = container_target(self._directory)
            self._sandbox = backend or DockerSandbox(
                container_options(self._directory, docker_image, memory_bytes, cpu_limit)
            )
        else:
            self._target = native_target()
            self._sandbox = backend or NativeSandbox()
        self._timeout = timeout_seconds
        self._max_tools = max_tools
        self._max_pending = max_pending
        self._min_tests = min_tests
        self._events = ChainedLog(self._directory / "events.jsonl", "The forge event log")
        self._lock = asyncio.Lock()

    @property
    def definitions(self) -> list[ToolDefinition]:
        found = [_meta_definition(name) for name in (PROPOSE_TOOL, USE_TOOL, LIST_TOOLS)]
        for name, entry in sorted(self._active().items()):
            found.append(
                ToolDefinition(
                    name=name,
                    description=f"{entry['description']} (forged tool, version {entry['version']})",
                    input_schema=entry["input_schema"],
                    episode_kind=EpisodeKind.ACTION,
                    concurrency=ToolConcurrency.SERIAL,
                )
            )
        return found

    async def run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult:
        try:
            if name == PROPOSE_TOOL:
                output = await self.propose(**call_arguments)
            elif name == USE_TOOL:
                output = await self.call(
                    call_arguments["name"], call_arguments.get("arguments", {})
                )
            elif name == LIST_TOOLS:
                output = self.listing()
            else:
                output = await self.call(name, call_arguments)
        except Exception as error:
            return ToolExecutionResult(
                status="failed", error=f'Forge tool "{name}" raised {type(error).__name__}: {error}'
            )
        return ToolExecutionResult(status="succeeded", output=output)

    async def propose(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        source: str,
        tests: list[dict[str, Any]],
        reason: str = "",
    ) -> dict[str, Any]:
        self._check(name, description, input_schema, source, tests)
        async with self._lock:
            if len(self._pending()) >= self._max_pending:
                raise ValueError(
                    f"{self._max_pending} proposals are already waiting for review; wait for a "
                    "decision before proposing another."
                )
            proposal = self._record(name, description, input_schema, source, tests, reason)
        if self._reviewer is None:
            return _answer(proposal, "pending", "Waiting for a human reviewer.")
        verdict = self._reviewer(proposal)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if verdict is True:
            return await self.approve(proposal.id, by=self._approved_by)
        return self.reject(
            proposal.id, "" if verdict is False else str(verdict), by=self._approved_by
        )

    async def approve(self, proposal_id: str, by: str = "host") -> dict[str, Any]:
        async with self._lock:
            proposal = self._proposal(proposal_id)
            self._require_pending(proposal)
            if proposal.name not in self._active() and len(self._active()) >= self._max_tools:
                raise ValueError(f"The forge already holds {self._max_tools} active tools.")
            failures = await self._run_tests(proposal)
            if failures:
                self._events.append(
                    {"event": "tests-failed", "proposal": proposal.id, "failures": failures}
                )
                return _answer(proposal, "failed-tests", "A test did not pass.", failures=failures)
            self._activate(proposal, by)
            return _answer(
                proposal,
                "active",
                f"Callable now as {USE_TOOL}(name={proposal.name!r}); listed natively from the "
                "next run.",
            )

    def reject(self, proposal_id: str, reason: str, by: str = "host") -> dict[str, Any]:
        proposal = self._proposal(proposal_id)
        self._require_pending(proposal)
        self._events.append(
            {"event": "rejected", "proposal": proposal.id, "by": by, "reason": reason}
        )
        return _answer(proposal, "rejected", reason or "The reviewer declined it.")

    def rollback(self, name: str, version: int, by: str = "host") -> None:
        active = self._active()
        activated = self._activated_versions(name)
        if version not in activated:
            raise ValueError(
                f'"{name}" was never active at version {version}; versions that were: '
                f"{sorted(activated) or 'none'}."
            )
        proposal = self._proposal(activated[version])
        self._events.append(
            {
                "event": "rolled-back",
                "tool": name,
                "from": active.get(name, {}).get("version"),
                "to": version,
                "by": by,
            }
        )
        self._write_active({**active, name: self._entry(proposal)})

    def retire(self, name: str, by: str = "host") -> None:
        active = self._active()
        if name not in active:
            raise ValueError(f'"{name}" is not an active forged tool.')
        self._events.append(
            {"event": "retired", "tool": name, "version": active[name]["version"], "by": by}
        )
        self._write_active({key: value for key, value in active.items() if key != name})

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        entry = self._active().get(name)
        if entry is None:
            raise ValueError(
                f'No active forged tool is named "{name}"; active: {sorted(self._active())}.'
            )
        path = self._directory / entry["source_file"]
        if _sha256(path.read_text(encoding="utf-8")) != entry["source_sha256"]:
            raise AgentSdkError(
                "FORGE_SOURCE_CHANGED",
                f'The stored source of "{name}" version {entry["version"]} no longer matches '
                "the hash recorded when it was approved, so it was not run.",
            )
        try:
            Draft202012Validator(entry["input_schema"]).validate(arguments)
        except ValidationError as error:
            raise ValueError(
                f'Arguments for "{name}" do not match its schema: {error.message}'
            ) from error
        succeeded, value = await run_source(
            self._sandbox, self._directory / "scratch", self._target, path, arguments, self._timeout
        )
        if not succeeded:
            raise RuntimeError(f'"{name}" version {entry["version"]} failed: {value}')
        return value

    def listing(self) -> dict[str, Any]:
        return {
            "active": [
                {"name": name, "version": e["version"], "description": e["description"]}
                for name, e in sorted(self._active().items())
            ],
            "pending": [p.id for p in self._pending()],
        }

    def pending(self) -> list[ToolProposal]:
        return self._pending()

    def verify(self) -> dict[str, int]:
        events = self._events.read()
        for name, entry in self._active().items():
            text = (self._directory / entry["source_file"]).read_text(encoding="utf-8")
            if _sha256(text) != entry["source_sha256"]:
                raise AgentSdkError(
                    "FORGE_SOURCE_CHANGED",
                    f'The source of active tool "{name}" no longer matches its approved hash.',
                )
        return {"events": len(events), "active_tools": len(self._active())}

    def _check(
        self, name: str, description: str, schema: dict[str, Any], source: str, tests: Any
    ) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError(
                f'Tool name "{name}" must be 3 to 41 characters: lowercase letters, digits and '
                "underscores, starting with a letter."
            )
        if not description.strip() or len(description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(f"description must be 1 to {MAX_DESCRIPTION_CHARS} characters.")
        if len(source) > MAX_SOURCE_CHARS:
            raise ValueError(
                f"source is {len(source)} characters; the limit is {MAX_SOURCE_CHARS}."
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(f"input_schema is not a valid JSON Schema: {error.message}") from error
        if schema.get("type") != "object":
            raise ValueError('input_schema must describe an object ("type": "object").')
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            raise ValueError(f"source does not parse: line {error.lineno}: {error.msg}") from error
        if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
            raise ValueError("source must define a top-level function run(**arguments).")
        if not isinstance(tests, list) or len(tests) < self._min_tests:
            raise ValueError(f"Give at least {self._min_tests} test case(s) as a list.")
        for index, case in enumerate(tests):
            if not isinstance(case, dict) or "arguments" not in case or "expect" not in case:
                raise ValueError(f'Test {index} must be an object with "arguments" and "expect".')
            try:
                Draft202012Validator(schema).validate(case["arguments"])
            except ValidationError as error:
                raise ValueError(
                    f"Test {index} arguments break input_schema: {error.message}"
                ) from error

    def _record(
        self,
        name: str,
        description: str,
        schema: dict[str, Any],
        source: str,
        tests: list[dict[str, Any]],
        reason: str,
    ) -> ToolProposal:
        full_name = TOOL_PREFIX + name
        existing = [p for p in self._all_proposals() if p.name == full_name]
        version = max((p.version for p in existing), default=0) + 1
        current = self._active().get(full_name)
        old_source = (
            (self._directory / current["source_file"]).read_text(encoding="utf-8")
            if current
            else None
        )
        proposal = ToolProposal(
            id=f"p-{len(self._all_proposals()) + 1:04d}",
            name=full_name,
            version=version,
            parent_version=current["version"] if current else None,
            description=description.strip(),
            input_schema=schema,
            source=source,
            tests=tests,
            reason=reason,
            source_sha256=_sha256(source),
            diff=source_diff(
                old_source,
                source,
                f"{full_name} v{current['version']}" if current else "(new)",
                f"{full_name} v{version}",
            ),
            report=code_report(source),
        )
        self._write_json(self._directory / "proposals" / f"{proposal.id}.json", asdict(proposal))
        self._events.append(
            {
                "event": "proposed",
                "proposal": proposal.id,
                "tool": full_name,
                "version": version,
                "source_sha256": proposal.source_sha256,
            }
        )
        return proposal

    async def _run_tests(self, proposal: ToolProposal) -> list[dict[str, Any]]:
        path = self._directory / "proposals" / f"{proposal.id}.py"
        path.write_text(proposal.source, encoding="utf-8")
        failures: list[dict[str, Any]] = []
        for index, case in enumerate(proposal.tests):
            succeeded, value = await run_source(
                self._sandbox,
                self._directory / "scratch",
                self._target,
                path,
                case["arguments"],
                self._timeout,
            )
            if not succeeded:
                failures.append({"test": index, "error": value})
            elif value != case["expect"]:
                failures.append({"test": index, "expected": case["expect"], "got": value})
        return failures

    def _activate(self, proposal: ToolProposal, by: str) -> None:
        file_name = f"active/{proposal.name}_v{proposal.version}.py"
        target = self._directory / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposal.source, encoding="utf-8")
        self._events.append(
            {
                "event": "activated",
                "proposal": proposal.id,
                "tool": proposal.name,
                "version": proposal.version,
                "by": by,
            }
        )
        self._write_active({**self._active(), proposal.name: self._entry(proposal)})

    def _entry(self, proposal: ToolProposal) -> dict[str, Any]:
        return {
            "version": proposal.version,
            "proposal": proposal.id,
            "description": proposal.description,
            "input_schema": proposal.input_schema,
            "source_file": f"active/{proposal.name}_v{proposal.version}.py",
            "source_sha256": proposal.source_sha256,
        }

    def _activated_versions(self, name: str) -> dict[int, str]:
        return {
            event["version"]: event["proposal"]
            for event in self._events.read()
            if event.get("event") == "activated" and event.get("tool") == name
        }

    def _pending(self) -> list[ToolProposal]:
        decided = {
            event["proposal"]
            for event in self._events.read()
            if event.get("event") in {"rejected", "activated", "tests-failed"}
        }
        return [p for p in self._all_proposals() if p.id not in decided]

    def _require_pending(self, proposal: ToolProposal) -> None:
        if proposal.id not in {p.id for p in self._pending()}:
            raise ValueError(f'Proposal "{proposal.id}" has already been decided.')

    def _all_proposals(self) -> list[ToolProposal]:
        folder = self._directory / "proposals"
        if not folder.is_dir():
            return []
        return [
            ToolProposal(**json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(folder.glob("p-*.json"))
        ]

    def _proposal(self, proposal_id: str) -> ToolProposal:
        if not _PROPOSAL_ID.fullmatch(proposal_id):
            raise ValueError(f'"{proposal_id}" is not a proposal id such as p-0001.')
        path = self._directory / "proposals" / f"{proposal_id}.json"
        if not path.is_file():
            raise ValueError(f'There is no proposal "{proposal_id}".')
        return ToolProposal(**json.loads(path.read_text(encoding="utf-8")))

    def _active(self) -> dict[str, Any]:
        path = self._directory / "active.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _write_active(self, active: dict[str, Any]) -> None:
        self._write_json(self._directory / "active.json", active)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_path(path)
        try:
            temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
            replace_atomic(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _answer(proposal: ToolProposal, status: str, note: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "proposal": proposal.id,
        "tool": proposal.name,
        "version": proposal.version,
        "note": note,
        **extra,
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _meta_definition(name: str) -> ToolDefinition:
    if name == PROPOSE_TOOL:
        return ToolDefinition(
            name=PROPOSE_TOOL,
            description=(
                "Propose a new Python tool, or a new version of one you proposed before. It does "
                "not run until a reviewer approves it, and your tests then run before it goes "
                "live. The source must define run(**arguments) returning JSON-serializable data "
                "and use only the standard library."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "input_schema": {"type": "object"},
                    "source": {"type": "string"},
                    "tests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"arguments": {"type": "object"}, "expect": {}},
                            "required": ["arguments", "expect"],
                        },
                    },
                    "reason": {"type": "string"},
                },
                "required": ["name", "description", "input_schema", "source", "tests"],
                "additionalProperties": False,
            },
            episode_kind=EpisodeKind.ACTION,
            concurrency=ToolConcurrency.SERIAL,
        )
    if name == USE_TOOL:
        return ToolDefinition(
            name=USE_TOOL,
            description="Call an approved forged tool by its full name, with its arguments.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            episode_kind=EpisodeKind.ACTION,
            concurrency=ToolConcurrency.SERIAL,
        )
    return ToolDefinition(
        name=LIST_TOOLS,
        description="List the active forged tools and the proposals waiting for review.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        episode_kind=EpisodeKind.EXPLORATORY,
        concurrency=ToolConcurrency.PARALLEL_SAFE,
    )
