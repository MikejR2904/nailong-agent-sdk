# Copyright (c) 2026 David Michael Indraputra

"""Capability-bound harness tools and their BaseAgent executor adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..foundations.contracts import AgentFailure, ToolDefinition, ToolExecutionResult
from ..foundations.text import split_lines
from ..memory.context_projection import ToolResultJournal
from ..state.elastic import ELASTIC_REQUEST_TOOL_NAME
from ..state.planning import PlanTask
from .approvals import ApprovalRegistry
from .artifacts import ArtifactStore
from .core import CoreToolDispatcher, CoreToolServices, HumanQuestionResponder, WebSearchClient
from .policy import CapabilityPolicy, SideEffectClass
from .supervisor import ProcessExecutionRecord, ProcessExitKind, ProcessSupervisor
from .tools import ToolExecutor, ToolInvocationContext


@dataclass(frozen=True)
class HarnessExecutionContext:
    """Non-prompt state available to a single scheduled node only."""

    run_id: str
    node_id: str
    role: str
    plan_task: PlanTask
    run_root: Path
    artifacts: ArtifactStore
    policy: CapabilityPolicy
    approvals: ApprovalRegistry
    supervisor: ProcessSupervisor
    spec_snapshots: dict[str, str] = field(default_factory=dict)
    declared_output_paths: tuple[str, ...] = ()
    approval_ids_by_capability: dict[str, str] = field(default_factory=dict)
    result_journal: ToolResultJournal | None = None
    search_client: WebSearchClient | None = None
    ask_human: HumanQuestionResponder | None = None
    pdf_image_cache: dict[str, tuple[str, bytes]] | None = None


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    capability: str
    side_effect: SideEffectClass
    command_template: str | None = None


HarnessToolHandler = Callable[[HarnessExecutionContext, dict[str, Any]], Awaitable[Any]]


class HarnessToolRegistry:
    """A closed registry: no generic shell or dynamically named tool is accepted."""

    def __init__(
        self,
        tools: list[RegisteredTool] | None = None,
        *,
        custom_handlers: dict[str, HarnessToolHandler] | None = None,
    ) -> None:
        values = list(tools) if tools is not None else self.default_tools()
        self._tools = {tool.name: tool for tool in values}
        if len(self._tools) != len(values):
            raise ValueError("Harness tool names must be unique.")
        self._custom_handlers = dict(custom_handlers or {})
        unknown = set(self._custom_handlers) - set(self._tools)
        if unknown:
            raise ValueError(f"Custom handlers require registered tools: {sorted(unknown)}")

    @staticmethod
    def default_tools() -> list[RegisteredTool]:
        """Return a fresh list of the policy-governed standard tool declarations."""

        return [
            RegisteredTool("read_spec", "spec.read", SideEffectClass.READ_ONLY),
            RegisteredTool("read_file", "filesystem.read", SideEffectClass.READ_ONLY),
            RegisteredTool("glob", "filesystem.read", SideEffectClass.READ_ONLY),
            RegisteredTool("grep", "filesystem.read", SideEffectClass.READ_ONLY),
            RegisteredTool("read_artifact", "artifact.read", SideEffectClass.READ_ONLY),
            RegisteredTool("grep_artifact", "artifact.read", SideEffectClass.READ_ONLY),
            RegisteredTool("get_tool_result", "evidence.read", SideEffectClass.READ_ONLY),
            RegisteredTool("write_draft", "draft.write", SideEffectClass.MUTATING),
            RegisteredTool("edit_draft", "draft.write", SideEffectClass.MUTATING),
            RegisteredTool("notebook_edit", "draft.write", SideEffectClass.MUTATING),
            RegisteredTool("diff_declared_artifacts", "artifact.diff", SideEffectClass.READ_ONLY),
            RegisteredTool("web_fetch", "web.research", SideEffectClass.READ_ONLY),
            RegisteredTool("render_pdf_page", "web.research", SideEffectClass.READ_ONLY),
            RegisteredTool("web_search", "web.research", SideEffectClass.READ_ONLY),
            RegisteredTool("sleep", "utility.wait", SideEffectClass.READ_ONLY),
            RegisteredTool("brief", "utility.brief", SideEffectClass.READ_ONLY),
            RegisteredTool("ask_human_question", "human.question", SideEffectClass.READ_ONLY),
            RegisteredTool(
                ELASTIC_REQUEST_TOOL_NAME, "graph.elastic.request", SideEffectClass.READ_ONLY
            ),
            RegisteredTool("run_registered_command", "process.execute", SideEffectClass.PROCESS),
            RegisteredTool("run_verilator", "rtl.verilator", SideEffectClass.PROCESS, "verilator"),
            RegisteredTool("run_yosys", "rtl.yosys", SideEffectClass.PROCESS, "yosys"),
            RegisteredTool(
                "run_openroad", "physical.openroad", SideEffectClass.PROCESS, "openroad"
            ),
            RegisteredTool("run_opensta", "physical.opensta", SideEffectClass.PROCESS, "opensta"),
        ]

    @classmethod
    def with_extensions(
        cls,
        extensions: list[RegisteredTool],
        *,
        handlers: dict[str, HarnessToolHandler],
    ) -> HarnessToolRegistry:
        """Build a closed registry containing standard tools plus named extensions."""

        return cls([*cls.default_tools(), *extensions], custom_handlers=handlers)

    def resolve(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def handler_for(self, name: str) -> HarnessToolHandler | None:
        return self._custom_handlers.get(name)


class HarnessToolExecutor(ToolExecutor):
    """Bridge BaseAgent tool calls into policy-governed typed harness actions."""

    def __init__(self, registry: HarnessToolRegistry, context: HarnessExecutionContext) -> None:
        self._registry = registry
        self._context = context
        self._core = CoreToolDispatcher(
            CoreToolServices(
                root=context.run_root,
                artifacts=context.artifacts,
                declared_output_paths=context.declared_output_paths,
                write_manifest={
                    "run_id": context.run_id,
                    "node_id": context.node_id,
                    "task_id": context.plan_task.task_id,
                },
                read_scope=context.policy.read_scope(context.role),
                result_journal=context.result_journal,
                search_client=context.search_client,
                ask_human=context.ask_human,
                pdf_image_cache=(
                    context.pdf_image_cache if context.pdf_image_cache is not None else {}
                ),
            )
        )

    async def execute(
        self,
        tool: ToolDefinition,
        invocation: ToolInvocationContext,
    ) -> ToolExecutionResult:
        registered = self._registry.resolve(tool.name)
        if registered is None:
            return ToolExecutionResult(
                status="failed", error=f'No harness tool named "{tool.name}" exists.'
            )
        requested_paths = self._requested_paths(tool.name, invocation.call.arguments)
        approval = self._approval_for(registered.capability)
        decision = self._context.policy.evaluate(
            role=self._context.role,
            capability=registered.capability,
            side_effect=registered.side_effect,
            run_root=self._context.run_root,
            requested_paths=requested_paths,
            approval=approval,
        )
        if not decision.allowed:
            if decision.approval_required:
                request = approval or self._context.approvals.request(
                    self._context.run_id,
                    self._context.node_id,
                    registered.capability,
                    decision.reason,
                )
                return ToolExecutionResult(
                    status="blocked",
                    output={
                        "approval_id": request.approval_id,
                        "capability": registered.capability,
                    },
                    error=decision.reason,
                )
            return ToolExecutionResult(status="blocked", error=decision.reason)

        try:
            output = await self._execute_registered(registered, invocation.call.arguments)
            if isinstance(output, ToolExecutionResult):
                return output
            return ToolExecutionResult(status="succeeded", output=output)
        except Exception as error:
            failure = AgentFailure(
                code="HARNESS_TOOL_EXECUTION_FAILED",
                message=f'Harness tool "{tool.name}" raised {type(error).__name__}: {error}',
                details={"tool": tool.name, "error_type": type(error).__name__},
            )
            return ToolExecutionResult(status="failed", error=failure.message, failure=failure)

    def _approval_for(self, capability: str):
        approval_id = self._context.approval_ids_by_capability.get(capability)
        if approval_id:
            return self._context.approvals.get(approval_id)
        return self._context.approvals.find(self._context.run_id, self._context.node_id, capability)

    async def _execute_registered(self, tool: RegisteredTool, arguments: dict[str, Any]) -> Any:
        if handler := self._registry.handler_for(tool.name):
            return await handler(self._context, arguments)

        if tool.name == "read_spec":
            pointer = _string_argument(arguments, "pointer")
            if pointer != self._context.plan_task.scope:
                raise ValueError("Requested specification pointer is outside the scoped PlanTask.")
            if pointer not in self._context.spec_snapshots:
                raise ValueError("Scoped specification snapshot is unavailable.")
            return {"pointer": pointer, "content": self._context.spec_snapshots[pointer]}

        if tool.name == ELASTIC_REQUEST_TOOL_NAME:
            raise ValueError(
                f'Tool "{ELASTIC_REQUEST_TOOL_NAME}" only works for an agent run as a graph '
                "node through GraphAgentExecutor, which queues the request for the controller; "
                "this agent was not started that way."
            )

        if tool.name == "run_registered_command":
            template_name = _string_argument(arguments, "template_name")
            process = await self._context.supervisor.execute(
                template_name, cwd=self._context.run_root
            )
            payload = process.model_dump(mode="json")
            if not process.successful:
                return ToolExecutionResult(
                    status="failed",
                    output=payload,
                    error=_process_failure_message(template_name, process),
                )
            return payload

        if tool.name in {
            "read_file",
            "glob",
            "grep",
            "get_tool_result",
            "write_draft",
            "edit_draft",
            "notebook_edit",
            "web_fetch",
            "render_pdf_page",
            "web_search",
            "sleep",
            "brief",
            "ask_human_question",
        }:
            return await self._core.execute(tool.name, arguments)

        if tool.name == "read_artifact":
            artifact_id = _string_argument(arguments, "artifact_id")
            if artifact_id not in self._context.plan_task.authorized_artifact_ids:
                raise ValueError("Artifact is not authorized for this PlanTask.")
            return {
                "artifact_id": artifact_id,
                "content": self._context.artifacts.read_text(artifact_id),
            }

        if tool.name == "grep_artifact":
            artifact_id = _string_argument(arguments, "artifact_id")
            needle = _string_argument(arguments, "needle")
            if artifact_id not in self._context.plan_task.authorized_artifact_ids:
                raise ValueError("Artifact is not authorized for this PlanTask.")
            lines = split_lines(self._context.artifacts.read_text(artifact_id))
            return {
                "artifact_id": artifact_id,
                "matches": [index + 1 for index, line in enumerate(lines) if needle in line],
            }

        if tool.name == "diff_declared_artifacts":
            base = _string_argument(arguments, "base_artifact_id")
            draft = _string_argument(arguments, "draft_artifact_id")
            allowed = set(self._context.plan_task.authorized_artifact_ids)
            if base not in allowed:
                raise ValueError("Base artifact is not authorized for this PlanTask.")
            if draft not in allowed:
                occurrence_id = arguments.get("draft_occurrence_id")
                if not isinstance(occurrence_id, str) or not occurrence_id:
                    raise ValueError(
                        "Draft artifact is not authorized and lacks current-task occurrence proof."
                    )
                if not self._context.artifacts.occurrence_matches_task_draft(
                    occurrence_id,
                    draft,
                    run_id=self._context.run_id,
                    node_id=self._context.node_id,
                    task_id=self._context.plan_task.task_id,
                    declared_output_paths=self._context.declared_output_paths,
                ):
                    raise ValueError(
                        "Draft artifact is not authorized and lacks current-task occurrence proof."
                    )
            return self._context.artifacts.diff(base, draft)

        if tool.command_template:
            process = await self._context.supervisor.execute(
                tool.command_template, cwd=self._context.run_root
            )
            payload = process.model_dump(mode="json")
            if not process.successful:
                return ToolExecutionResult(
                    status="failed",
                    output=payload,
                    error=_process_failure_message(tool.name, process),
                )
            return payload

        raise ValueError(f'No implementation is registered for "{tool.name}".')

    @staticmethod
    def _requested_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
        if tool_name in {"write_draft", "edit_draft", "notebook_edit"}:
            path = arguments.get("path")
            return [path] if isinstance(path, str) else []
        return []


def _process_failure_message(name: str, process: ProcessExecutionRecord) -> str:
    detail = {
        ProcessExitKind.EXIT_NONZERO: f"exited with code {process.return_code}",
        ProcessExitKind.TIMED_OUT: "was terminated after exceeding its timeout",
        ProcessExitKind.CANCELLED: "was cancelled",
        ProcessExitKind.RESOURCE_LIMIT: (
            f"was stopped by a resource limit ({process.exit_signal or process.return_code})"
        ),
    }.get(process.exit_kind, f"ended as {process.exit_kind.value}")
    return f'{process.error_code or process.exit_kind.value}: registered command "{name}" {detail}.'


def _string_argument(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f'Argument "{key}" must be a non-empty string.')
    return value
