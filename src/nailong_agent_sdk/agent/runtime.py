# Copyright (c) 2026 David Michael Indraputra

"""Ergonomic composition root for a host-owned durable BaseAgent runtime.

The SDK deliberately requires an embedding application to choose its model and
its capability-governed tool executor.  ``AgentRuntimeServices`` removes only
repetitive persistence wiring: it owns local result, project-state, telemetry,
and audit stores beneath one declared run root.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..foundations.contracts import AgentDefinition
from ..memory.context_projection import ContextProjectionPolicy, FileToolResultJournal
from ..memory.episode_store import InMemoryEpisodeStore
from ..observability.audit_log import AuditTranscriptStore
from ..observability.profiler import AgentRunProfiler
from ..observability.telemetry_store import TelemetryStore
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..state.project_state_store import FileProjectStateStore
from ..tools.tools import ToolExecutor
from .base_agent import AgentWatchdogPolicy, BaseAgent, PostToolHook, PreToolHook
from .model import AgentModel
from .verification import VerificationGateRegistry


@dataclass(frozen=True)
class AgentRuntimeServices:
    """Durable SDK-owned services rooted beneath one host-selected directory.

    This helper does not select a model, create a tool executor, expand
    capabilities, approve a state-changing action, or choose a Sandbox/Desktop/
    SSH target. Those authority decisions stay with the embedding host.
    """

    run_root: Path
    result_journal: FileToolResultJournal
    project_state_store: FileProjectStateStore
    telemetry: TelemetryStore
    audit_logs: AuditTranscriptStore

    @classmethod
    def open(cls, run_root: Path) -> AgentRuntimeServices:
        """Create or reopen SDK-owned durable stores below ``run_root``."""

        resolved = run_root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=resolved,
            result_journal=FileToolResultJournal(resolved),
            project_state_store=FileProjectStateStore(resolved),
            telemetry=TelemetryStore(resolved),
            audit_logs=AuditTranscriptStore(resolved),
        )

    def create_agent(
        self,
        definition: AgentDefinition,
        model: AgentModel,
        *,
        tool_executor: ToolExecutor | None = None,
        verification_gates: VerificationGateRegistry | None = None,
        pre_tool_hooks: Sequence[PreToolHook] = (),
        post_tool_hooks: Sequence[PostToolHook] = (),
        now: Callable[[], datetime] | None = None,
        watchdog_policy: AgentWatchdogPolicy | None = None,
        context_projection_policy: ContextProjectionPolicy | None = None,
        project_state_projection_policy: ProjectStateProjectionPolicy | None = None,
        episode_store_factory: Callable[[], InMemoryEpisodeStore] | None = None,
    ) -> BaseAgent:
        """Construct a BaseAgent with durable local audit and evidence stores.

        A new profiler is intentionally allocated per agent invocation. Model
        adapters, tool executors, verification callbacks, and policy hooks are
        explicit injected dependencies rather than hidden defaults.
        """

        return BaseAgent(
            definition,
            model,
            tool_executor=tool_executor,
            verification_gates=verification_gates,
            pre_tool_hooks=pre_tool_hooks,
            post_tool_hooks=post_tool_hooks,
            now=now,
            watchdog_policy=watchdog_policy,
            context_projection_policy=context_projection_policy,
            project_state_projection_policy=project_state_projection_policy,
            result_journal=self.result_journal,
            project_state_store=self.project_state_store,
            episode_store_factory=episode_store_factory,
            telemetry=self.telemetry,
            audit_logs=self.audit_logs,
            profiler=AgentRunProfiler(),
        )
