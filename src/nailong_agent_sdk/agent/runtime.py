# Copyright (c) 2026 David Michael Indraputra

"""Ergonomic composition root for a host-owned durable BaseAgent runtime.

The SDK deliberately requires an embedding application to choose its model and
its capability-governed tool executor.  ``AgentRuntimeServices`` removes only
repetitive persistence wiring: it owns local result, project-state, telemetry,
and audit stores beneath one declared run root.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..foundations.contracts import AgentDefinition
from ..foundations.errors import AgentSdkError
from ..foundations.logging import get_logger
from ..memory.context_projection import ContextProjectionPolicy, FileToolResultJournal
from ..memory.episode_store import InMemoryEpisodeStore
from ..observability.audit_log import AuditTranscriptStore
from ..observability.profiler import AgentRunProfiler
from ..observability.run_checkpoints import RunHeadCheckpoint
from ..observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetrySeverity,
)
from ..observability.telemetry_store import TelemetryStore
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..state.project_state_store import FileProjectStateStore
from ..tools.artifact_anchor import ArtifactLogAnchor, anchor_artifact_log, artifact_anchor_name
from ..tools.artifacts import ArtifactStore
from ..tools.tools import ToolAuthorizer, ToolExecutor
from .base_agent import AgentWatchdogPolicy, BaseAgent, PostToolHook, PreToolHook
from .model import AgentModel
from .retention import RunRetention
from .run_checkpointer import LOCAL_ANCHOR, RunHeadCheckpointer, resolve_checkpointer
from .verification import VerificationGateRegistry

_logger = get_logger("runtime")
MAX_RECORDED_CHECKPOINT_ERROR_CHARS = 500


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
    checkpointer: RunHeadCheckpointer | None = None

    @classmethod
    def open(
        cls,
        run_root: Path,
        *,
        audit_max_open_handles: int = 32,
        audit_max_payload_chars: int | None = None,
        checkpointer: RunHeadCheckpointer | None = LOCAL_ANCHOR,  # type: ignore[assignment]
    ) -> AgentRuntimeServices:
        """Create or reopen SDK-owned durable stores below ``run_root``."""

        resolved = run_root.resolve()
        checkpointer = resolve_checkpointer(checkpointer, resolved)
        if checkpointer is not None:
            checkpointer.require_outside(resolved)
        resolved.mkdir(parents=True, exist_ok=True)
        payload_limit = (
            {}
            if audit_max_payload_chars is None
            else {"max_payload_chars": audit_max_payload_chars}
        )
        return cls(
            run_root=resolved,
            result_journal=FileToolResultJournal(resolved),
            project_state_store=FileProjectStateStore(resolved),
            telemetry=TelemetryStore(resolved),
            audit_logs=AuditTranscriptStore(
                resolved, max_open_handles=audit_max_open_handles, **payload_limit
            ),
            checkpointer=checkpointer,
        )

    def checkpoint_run(self, run_id: str) -> RunHeadCheckpoint | None:
        if self.checkpointer is None:
            return None
        return self.checkpointer.export(self.run_root, self.telemetry, self.audit_logs, run_id)

    async def checkpoint_after_run(
        self, run_id: str, context: TelemetryContext | None = None
    ) -> RunHeadCheckpoint | None:
        if self.checkpointer is None:
            return None
        try:
            return await asyncio.to_thread(self.checkpoint_run, run_id)
        except Exception as error:
            self.record_checkpoint_failure(run_id, context, error)
            return None

    async def anchor_artifacts_after_run(
        self,
        run_id: str,
        store: ArtifactStore,
        context: TelemetryContext | None = None,
    ) -> ArtifactLogAnchor | None:
        if self.checkpointer is None:
            return None
        checkpointer = self.checkpointer
        try:
            return await asyncio.to_thread(
                anchor_artifact_log,
                store,
                checkpointer.sink,
                artifact_anchor_name(run_id),
                checkpointer.signer,
            )
        except Exception as error:
            self.record_checkpoint_failure(run_id, context, error, "artifact-log")
            return None

    def record_checkpoint_failure(
        self,
        run_id: str,
        context: TelemetryContext | None,
        error: Exception,
        subject: str = "run-head",
    ) -> None:
        code = error.code if isinstance(error, AgentSdkError) else None
        message = str(error)[:MAX_RECORDED_CHECKPOINT_ERROR_CHARS]
        _logger.warning(
            'The run-head checkpoint of run "%s" failed: %s: %s',
            run_id,
            type(error).__name__,
            message,
        )
        try:
            self.telemetry.emit(
                "run.checkpoint-failed",
                context or TelemetryContext(run_id=run_id, task_id=run_id),
                actor=TelemetryActor(kind="system", identifier="run-checkpointer", role="runtime"),
                authority=TelemetryAuthority.SYSTEM,
                status="failed",
                severity=TelemetrySeverity.WARNING,
                payload={
                    "run_id": run_id,
                    "subject": subject,
                    "error_type": type(error).__name__,
                    "error_code": code,
                    "error": message,
                },
            )
        except Exception as recording_error:
            _logger.warning(
                'The failed run-head checkpoint of run "%s" could not be recorded in telemetry: '
                "%s: %s",
                run_id,
                type(recording_error).__name__,
                recording_error,
            )

    def retention(self) -> RunRetention:
        return RunRetention(
            self.run_root,
            telemetry=self.telemetry,
            audit_logs=self.audit_logs,
            result_journal=self.result_journal,
        )

    def create_agent(
        self,
        definition: AgentDefinition,
        model: AgentModel,
        *,
        tool_executor: ToolExecutor | None = None,
        tool_authorizer: ToolAuthorizer | None = None,
        require_tool_authorization: bool | None = None,
        verification_gates: VerificationGateRegistry | None = None,
        pre_tool_hooks: Sequence[PreToolHook] = (),
        post_tool_hooks: Sequence[PostToolHook] = (),
        now: Callable[[], datetime] | None = None,
        watchdog_policy: AgentWatchdogPolicy | None = None,
        context_projection_policy: ContextProjectionPolicy | None = None,
        project_state_projection_policy: ProjectStateProjectionPolicy | None = None,
        episode_store_factory: Callable[[], InMemoryEpisodeStore] | None = None,
        telemetry_context: TelemetryContext | None = None,
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
            tool_authorizer=tool_authorizer,
            require_tool_authorization=require_tool_authorization,
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
            telemetry_context=telemetry_context,
            audit_logs=self.audit_logs,
            profiler=AgentRunProfiler(),
        )
