# Copyright (c) 2026 David Michael Indraputra

"""Shared runtime context and response helpers for every registered MCP tool group."""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent.orchestrator import Orchestrator
from ..agent.retention import RunRetention
from ..agent.task_runner import AgentTaskRunner
from ..observability.audit_log import AuditTranscriptStore
from ..observability.telemetry_store import TelemetryStore
from ..specifications.preprocessing import SpecificationPreprocessor
from ..state.controller_runtime import ControllerRuntime
from ..state.harness_coordinator import HarnessCoordinator
from ..state.planning import PlanValidator
from ..state.project_state_store import FileProjectStateStore
from .authority import (
    DecisionGate,
    McpAuthorityPolicy,
    McpAuthorizationError,
    McpOperation,
    authenticated_as,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "location": list(issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors()
    ]


def _refusal(error: McpAuthorizationError) -> dict[str, Any]:
    return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}


@dataclass
class McpContext:
    """Durable services and helpers shared by every tool group registered on the server."""

    run_root: Path
    coordinator: HarnessCoordinator
    telemetry: TelemetryStore
    audit_logs: AuditTranscriptStore
    project_states: FileProjectStateStore
    controller_runtime: ControllerRuntime
    specification_root: Path
    preprocessor: SpecificationPreprocessor
    plan_validator: PlanValidator
    task_runner: AgentTaskRunner
    retention: RunRetention
    orchestrators: dict[str, Orchestrator] = field(default_factory=dict)
    operations: dict[str, McpOperation] = field(default_factory=dict)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    authority: McpAuthorityPolicy = field(default_factory=McpAuthorityPolicy)
    gate: DecisionGate = field(init=False)

    def __post_init__(self) -> None:
        self.gate = DecisionGate(self.authority, self.telemetry)

    def tool(
        self,
        server: MCPServer,
        name: str,
        *,
        exclusive: bool = True,
        operation: McpOperation = McpOperation.HOST,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            self.operations[name] = operation
            return server.tool(name=name, structured_output=True)(
                self._offloaded(function, name, exclusive, operation)
            )

        return decorate

    def _offloaded(
        self,
        function: Callable[..., Any],
        name: str,
        exclusive: bool,
        operation: McpOperation,
    ) -> Callable[..., Any]:
        awaitable = inspect.iscoroutinefunction(function)

        def call(*args: Any, **kwargs: Any) -> Any:
            if awaitable:
                return asyncio.run(function(*args, **kwargs))
            return function(*args, **kwargs)

        async def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.to_thread(call, *args, **kwargs)
            except ValidationError as error:
                return {"ok": False, "errors": _validation_errors(error)}
            except Exception as error:
                return {
                    "ok": False,
                    "errors": [
                        {
                            "message": f'MCP tool "{name}" failed: {error}',
                            "type": type(error).__name__,
                        }
                    ],
                }

        @functools.wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                principal = self.gate.resolve(name)
            except McpAuthorizationError as error:
                return _refusal(error)
            try:
                self.authority.check(principal, operation, name)
            except McpAuthorizationError as error:
                await asyncio.to_thread(
                    self.gate.refuse_operation, name, operation, principal, error
                )
                return _refusal(error)
            with authenticated_as(principal):
                if not exclusive:
                    return await run(*args, **kwargs)
                async with self.state_lock:
                    return await run(*args, **kwargs)

        setattr(wrapper, "__signature__", inspect.signature(function, eval_str=True))
        return wrapper

    def orchestration_for(self, orchestration_id: str) -> Orchestrator:
        """Recover a policy shell; executable worker bindings remain host-local."""

        if orchestration_id not in self.orchestrators:
            self.orchestrators[orchestration_id] = Orchestrator.resume(
                self.run_root,
                orchestration_id,
                controller_runtime=self.controller_runtime,
                telemetry=self.telemetry,
            )
        return self.orchestrators[orchestration_id]
