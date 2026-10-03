# Copyright (c) 2026 David Michael Indraputra

"""One-task Python BaseAgent execution runtime with bounded projected context."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ...foundations.canonical import estimate_tokens
from ...foundations.contracts import (
    AgentDefinition,
    AgentEscalation,
    AgentFailure,
    AgentLifecycleEvent,
    AgentResult,
    AgentRunStatus,
    AgentTurn,
    ContextProjectionMetadata,
    MemoryScope,
    ModelObservation,
    ScopedAgentTask,
    ToolBatchTurn,
    ToolCall,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
    validate_candidate_output,
    validate_task_input,
    validate_tool_arguments,
)
from ...foundations.errors import AgentSdkError, TransientProviderError
from ...memory.context import assemble_initial_context
from ...memory.context_projection import (
    ContextProjectionPolicy,
    ContextProjector,
    InMemoryToolResultJournal,
    ToolResultJournal,
)
from ...memory.episode_models import CompactionStatus, CompactionStrategy
from ...memory.episode_store import InMemoryEpisodeStore
from ...memory.episodes import InMemoryEpisodeGraph
from ...observability.audit_log import AuditTranscriptStore
from ...observability.metrics import (
    record_metric_unavailable,
    record_metric_value,
    record_terminal_agent_metrics,
    register_standard_metric_definitions,
)
from ...observability.profiler import (
    AgentRunProfiler,
    ProfileSpanHandle,
    ProfileSpanKind,
    ProfileSpanStatus,
)
from ...observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetrySeverity,
)
from ...observability.telemetry_store import TelemetryStore
from ...state.project_state_engine import ProjectStateProjector, ProjectStateReducer
from ...state.project_state_models import (
    ProjectState,
    ProjectStateProjectionPolicy,
    ProjectStateRepository,
    StageStateSchema,
)
from ...state.project_state_store import InMemoryProjectStateStore
from ...tools.tools import ToolExecutor, ToolInvocationContext
from ..model import (
    AgentModel,
    ModelContext,
    ModelStreamListener,
    ModelTurnResponse,
    ProviderContinuation,
    ProviderToolResult,
    ProviderToolResultConsumer,
    ProviderUsage,
    StreamingAgentModel,
)
from ..verification import VerificationGateRegistry
from .types import (
    AgentWatchdogPolicy,
    CancellationToken,
    PostToolHook,
    PreToolHook,
    ToolBatchExecution,
    ToolCallOutcome,
)

# AgentTurn is a fixed discriminated union, so one module-level adapter safely
# reuses identical validation state for every untrusted model response.
_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


class BaseAgent:
    """Run exactly one scoped task through a bounded, observable tool loop.

    The runtime treats model output as untrusted. It executes only declared tools,
    projects full results through opaque journal handles, and performs deterministic
    episode compaction before each model call.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        model: AgentModel,
        tool_executor: ToolExecutor | None = None,
        verification_gates: VerificationGateRegistry | None = None,
        pre_tool_hooks: Sequence[PreToolHook] = (),
        post_tool_hooks: Sequence[PostToolHook] = (),
        now: Callable[[], datetime] | None = None,
        watchdog_policy: AgentWatchdogPolicy | None = None,
        context_projection_policy: ContextProjectionPolicy | None = None,
        project_state_projection_policy: ProjectStateProjectionPolicy | None = None,
        result_journal: ToolResultJournal | None = None,
        project_state_store: ProjectStateRepository | None = None,
        episode_store_factory: Callable[[], InMemoryEpisodeStore] | None = None,
        telemetry: TelemetryStore | None = None,
        telemetry_context: TelemetryContext | None = None,
        audit_logs: AuditTranscriptStore | None = None,
        profiler: AgentRunProfiler | None = None,
        on_model_stream: ModelStreamListener | None = None,
    ) -> None:
        if definition.memory_scope is MemoryScope.CROSS_SESSION:
            raise AgentSdkError(
                "CROSS_SESSION_STORE_REQUIRED",
                "Cross-session memory is not supported by BaseAgent yet: it keeps one episode "
                "graph and store per run and has no path for attaching a persistent episode "
                "store, so injecting an episode_store_factory does not lift this restriction; "
                "declare the definition's memory_scope as task-scoped.",
            )
        self.definition = definition
        self.model = model
        self.tool_executor = tool_executor
        self.verification_gates = verification_gates or VerificationGateRegistry()
        self.pre_tool_hooks = tuple(pre_tool_hooks)
        self.post_tool_hooks = tuple(post_tool_hooks)
        self._now = now or (lambda: datetime.now(UTC))
        self.watchdog_policy = watchdog_policy or AgentWatchdogPolicy()
        self.context_projector = ContextProjector(
            context_projection_policy or ContextProjectionPolicy()
        )
        self.project_state_projector = ProjectStateProjector(project_state_projection_policy)
        self.result_journal = result_journal or InMemoryToolResultJournal()
        self.project_state_store = project_state_store or InMemoryProjectStateStore()
        self._episode_store_factory = episode_store_factory or InMemoryEpisodeStore
        self._active_project_id: str | None = None
        self._active_project_state: ProjectState | None = None
        self.telemetry = telemetry
        self.telemetry_context = telemetry_context
        self.audit_logs = audit_logs
        self.profiler = profiler or AgentRunProfiler()
        self._on_model_stream = on_model_stream
        self._profile_root_span: ProfileSpanHandle | None = None
        self._provider_usage_reported = False
        self._active_audit_run_id: str | None = None

    async def run(
        self,
        task: ScopedAgentTask,
        cancellation: CancellationToken | None = None,
    ) -> AgentResult:
        """Run one task; raise only for malformed caller input and programming errors."""

        if self._active_project_id is not None:
            raise RuntimeError("A BaseAgent instance may execute only one task at a time.")
        validate_task_input(self.definition, task)
        prompt = assemble_initial_context(self.definition, task)
        episodes = InMemoryEpisodeGraph(self._now)
        memory = self._episode_store_factory()
        events: list[AgentLifecycleEvent] = []
        observations: list[ModelObservation] = []
        projection_history: list[ContextProjectionMetadata] = []
        continuation: ProviderContinuation | None = None
        self._provider_usage_reported = False
        protected_episode_ids: frozenset[str] = frozenset()
        project_id = self._project_id_for(task)
        project_state = self.project_state_store.ensure(project_id, self._stage_schema_for(task))
        self._active_project_id = project_id
        self._active_project_state = project_state
        deadline = (
            asyncio.get_running_loop().time() + self.watchdog_policy.run_deadline_seconds
            if self.watchdog_policy.run_deadline_seconds is not None
            else None
        )
        telemetry_context = self.telemetry_context or TelemetryContext(
            run_id=task.id,
            task_id=task.id,
            agent_id=self.definition.identity,
        )
        self._active_audit_run_id = telemetry_context.run_id
        self._profile_root_span = self.profiler.begin_run(
            telemetry_context.run_id,
            task.id,
            self.definition.identity,
        )
        if self.telemetry is not None:
            register_standard_metric_definitions(self.telemetry)

        def emit(event_type: str, iteration: int, **details: Any) -> None:
            event = AgentLifecycleEvent(
                type=event_type,
                task_id=task.id,
                iteration=iteration,
                at=self._now().isoformat(),
                details=details,
            )
            events.append(event)
            telemetry_event = None
            if self.telemetry is not None:
                telemetry_event = self.telemetry.emit(
                    f"agent.{event_type}",
                    telemetry_context,
                    actor=TelemetryActor(
                        kind="agent", identifier=self.definition.identity, role="base-agent"
                    ),
                    authority=TelemetryAuthority.DETERMINISTIC,
                    status=event_type,
                    severity=TelemetrySeverity.ERROR
                    if event_type in {"terminated", "escalated"}
                    and details.get("status") != "completed"
                    else TelemetrySeverity.INFO,
                    payload={"iteration": iteration, "details": details},
                )
            if self.audit_logs is not None:
                self.audit_logs.append(
                    telemetry_context.run_id,
                    event_type,
                    {
                        "details": details,
                        "telemetry_event_id": telemetry_event.event_id if telemetry_event else None,
                    },
                    task_id=task.id,
                    iteration=iteration,
                )

        emit("run-started", 0, model_binding=self.definition.model_binding.model_dump(mode="json"))
        if self.audit_logs is not None:
            self.audit_logs.append(
                telemetry_context.run_id,
                "task-received",
                {
                    "identity": self.definition.identity,
                    "task_instructions": task.instructions,
                    "acceptance_criteria": task.acceptance_criteria,
                    "scope": task.scope.model_dump(mode="json"),
                },
                task_id=task.id,
                iteration=0,
            )
        emit("context-assembled", 0, section_count=len(prompt.sections))
        emit(
            "state-loaded",
            0,
            project_id=project_state.project_id,
            revision=project_state.revision,
            state_hash=project_state.state_hash,
        )

        for iteration in range(1, self.definition.termination_policy.max_iterations + 1):
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                run_deadline_seconds = self.watchdog_policy.run_deadline_seconds
                failure = AgentFailure(
                    code="WATCHDOG_RUN_DEADLINE",
                    message=f"Agent run exceeded its {run_deadline_seconds}s overall run "
                    f"deadline after {iteration - 1} completed iteration(s); the task may need "
                    "a longer deadline, a smaller scope, or fewer iterations per unit of work.",
                    details={
                        "run_deadline_seconds": run_deadline_seconds,
                        "completed_iterations": iteration - 1,
                    },
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration - 1,
                    failure.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=failure,
                )
            if cancellation and cancellation.is_cancelled():
                return self._terminate(
                    AgentRunStatus.CANCELLED,
                    task,
                    iteration - 1,
                    "Execution was cancelled.",
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                )

            projection_span = self.profiler.start_span(
                ProfileSpanKind.CONTEXT_PROJECTION,
                "context-projection",
                parent_span_id=self._profile_root_span.span_id,
                attributes={"iteration": iteration},
            )
            try:
                projection = self.context_projector.project(
                    prompt,
                    observations,
                    episodes.list(),
                    memory,
                    protected_episode_ids,
                    relevance_query=self._pask_relevance_query(task),
                )
            except Exception:
                self.profiler.finish_span(projection_span, ProfileSpanStatus.FAILED)
                raise
            else:
                self.profiler.finish_span(
                    projection_span,
                    ProfileSpanStatus.COMPLETED,
                    attributes={
                        "estimated_tokens": projection.metadata.estimated_tokens,
                        "compacted_episode_count": len(projection.compaction.compacted_episode_ids),
                    },
                )
            projection_history.append(projection.metadata)
            emit(
                "context-projected",
                iteration,
                estimated_tokens=projection.metadata.estimated_tokens,
                context_token_budget=projection.metadata.context_token_budget,
                omitted_observation_count=projection.metadata.omitted_observation_count,
                compacted_reference_count=len(projection.compacted_episodes),
                omitted_compacted_count=projection.metadata.omitted_compacted_count,
            )
            if self.telemetry is not None:
                metric_details = {"iteration": iteration, "kind": "sdk-estimate"}
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.estimated_input_tokens",
                    float(projection.metadata.estimated_tokens),
                    "tokens",
                    details=metric_details,
                )
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.working_budget_tokens",
                    float(projection.metadata.context_token_budget),
                    "tokens",
                    details={"iteration": iteration},
                )
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.remaining_working_budget_tokens",
                    float(
                        max(
                            projection.metadata.context_token_budget
                            - projection.metadata.estimated_tokens,
                            0,
                        )
                    ),
                    "tokens",
                    details=metric_details,
                )
            if projection.compaction.compacted_episode_ids:
                emit(
                    "context-compacted",
                    iteration,
                    compacted_episode_ids=projection.compaction.compacted_episode_ids,
                    before_tokens=projection.compaction.before_tokens,
                    after_tokens=projection.compaction.after_tokens,
                    strategy=projection.compaction.strategy.value,
                )
            if (
                self.telemetry is not None
                and projection.compaction.strategy is CompactionStrategy.PASK
            ):
                dossier = projection.compaction.dossier
                retained_ids = dossier.get("retained_episode_ids", [])
                mandatory_ids = dossier.get("mandatory_episode_ids", [])
                metric_details = {
                    "iteration": iteration,
                    "strategy": projection.compaction.strategy.value,
                }
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.pask_compaction_count",
                    1.0,
                    "count",
                    details=metric_details,
                )
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.pask_retained_episode_count",
                    float(len(retained_ids)),
                    "count",
                    details=metric_details,
                )
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.pask_compacted_episode_count",
                    float(len(projection.compaction.compacted_episode_ids)),
                    "count",
                    details=metric_details,
                )
                if mandatory_ids:
                    retained_mandatory = len(set(mandatory_ids) & set(retained_ids))
                    record_metric_value(
                        self.telemetry,
                        telemetry_context,
                        "context.pask_mandatory_retention_rate",
                        retained_mandatory / len(mandatory_ids),
                        "ratio",
                        details=metric_details,
                    )
                else:
                    record_metric_unavailable(
                        self.telemetry,
                        telemetry_context,
                        "context.pask_mandatory_retention_rate",
                        "ratio",
                        "No mandatory episode IDs were present in this PASK decision.",
                        details=metric_details,
                    )
            if (
                self.telemetry is not None
                and projection.compaction.strategy is CompactionStrategy.EXACT_PCKP
                and isinstance(projection.compaction.dossier.get("solver"), dict)
            ):
                dossier = projection.compaction.dossier
                solver = dossier.get("solver", {})
                retained_ids = dossier.get("retained_episode_ids", [])
                metric_details = {
                    "iteration": iteration,
                    "strategy": projection.compaction.strategy.value,
                    "problem_hash": solver.get("problem_hash")
                    if isinstance(solver, dict)
                    else None,
                    "solver_status": solver.get("status") if isinstance(solver, dict) else None,
                }
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.exact_pckp_compaction_count",
                    1.0,
                    "count",
                    details=metric_details,
                )
                record_metric_value(
                    self.telemetry,
                    telemetry_context,
                    "context.exact_pckp_retained_episode_count",
                    float(len(retained_ids)),
                    "count",
                    details=metric_details,
                )
                if isinstance(solver, dict):
                    branch_nodes = solver.get("branch_nodes")
                    if isinstance(branch_nodes, int):
                        record_metric_value(
                            self.telemetry,
                            telemetry_context,
                            "context.exact_pckp_branch_node_count",
                            float(branch_nodes),
                            "count",
                            details=metric_details,
                        )
                    gap = solver.get("optimality_gap")
                    try:
                        numeric_gap = float(Fraction(str(gap)))
                    except (ValueError, ZeroDivisionError):
                        numeric_gap = None
                    if numeric_gap is not None:
                        record_metric_value(
                            self.telemetry,
                            telemetry_context,
                            "context.exact_pckp_optimality_gap",
                            numeric_gap,
                            "utility",
                            details=metric_details,
                        )
                    else:
                        record_metric_unavailable(
                            self.telemetry,
                            telemetry_context,
                            "context.exact_pckp_optimality_gap",
                            "utility",
                            "Exact compaction solver did not provide a numeric gap certificate.",
                            details=metric_details,
                        )
            if projection.compaction.status is CompactionStatus.CONTEXT_DEADLOCK:
                return self._terminate(
                    AgentRunStatus.BLOCKED,
                    task,
                    iteration - 1,
                    "CONTEXT_DEADLOCK: no closed, dependency-free, manifest-complete episode "
                    "can be compacted within the configured episode budget.",
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                )
            emit("turn-started", iteration)
            try:
                project_state = self._active_project_state or project_state
                project_state_view = self.project_state_projector.project(project_state)
                if project_state_view.over_budget:
                    return self._terminate(
                        AgentRunStatus.BLOCKED,
                        task,
                        iteration - 1,
                        "PROJECT_STATE_BUDGET_EXCEEDED: mandatory current-state fields exceed "
                        "the configured state token budget.",
                        prompt,
                        episodes,
                        events,
                        emit,
                        projection_history,
                    )
                model_context_tokens = self._estimate_model_context_tokens(
                    prompt, project_state_view
                )
                if model_context_tokens > projection.metadata.context_token_budget:
                    return self._terminate(
                        AgentRunStatus.BLOCKED,
                        task,
                        iteration - 1,
                        "CONTEXT_BUDGET_EXCEEDED: immutable prompt plus bounded project state "
                        "exceed the configured context token budget.",
                        prompt,
                        episodes,
                        events,
                        emit,
                        projection_history,
                    )
                model_span = self.profiler.start_span(
                    ProfileSpanKind.MODEL_TURN,
                    "model-turn",
                    parent_span_id=self._profile_root_span.span_id,
                    attributes={"iteration": iteration},
                )
                model_context = ModelContext(
                    task=task,
                    prompt=prompt,
                    iteration=iteration,
                    project_state=project_state_view,
                    observations=projection.observations,
                    episodes=projection.episodes,
                    continuation=continuation,
                    projection=projection.metadata,
                    model_binding=self.definition.model_binding,
                    output_schema=self.definition.output_schema,
                    compacted_episodes=projection.compacted_episodes,
                )
                streaming_ready = self._on_model_stream is not None and isinstance(
                    self.model, StreamingAgentModel
                )
                if streaming_ready:
                    model_call = self.model.stream_turn(model_context, self._on_model_stream)
                else:
                    model_call = self.model.next_turn(model_context)
                try:
                    response = await self._await_with_watchdog(
                        model_call,
                        self.watchdog_policy.model_turn_timeout_seconds,
                        deadline,
                    )
                except TimeoutError:
                    self.profiler.finish_span(model_span, ProfileSpanStatus.TIMED_OUT)
                    raise
                except Exception:
                    self.profiler.finish_span(model_span, ProfileSpanStatus.FAILED)
                    raise
                else:
                    self.profiler.finish_span(model_span, ProfileSpanStatus.COMPLETED)
            except TimeoutError:
                turn_timeout = self.watchdog_policy.model_turn_timeout_seconds
                timeout_detail = (
                    f"its {turn_timeout}s per-turn watchdog timeout"
                    if turn_timeout is not None
                    else "the run's configured deadline"
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    f"WATCHDOG_MODEL_TIMEOUT: model turn exceeded {timeout_detail}; "
                    "the provider may be overloaded, the request may be too large for its "
                    "current response time, or the network path may be degraded.",
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                )
            except TransientProviderError as error:
                observations.append(
                    ModelObservation(
                        kind="agent-error",
                        iteration=iteration,
                        message=f"Model turn failed, retrying ({error.code}): {error.message}",
                    )
                )
                continue
            except AgentSdkError as error:
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    error.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=AgentFailure.from_sdk_error(error),
                )
            except Exception as error:
                failure = AgentFailure(
                    code="MODEL_TURN_UNEXPECTED_ERROR",
                    message=str(error) or type(error).__name__,
                    details={"error_type": type(error).__name__},
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    failure.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=failure,
                )

            try:
                turn, continuation, usage = self._normalize_model_response(response, continuation)
                if usage is not None:
                    self._provider_usage_reported = True
                    self._record_provider_usage(telemetry_context, usage, iteration)
                if self.audit_logs is not None:
                    self.audit_logs.append(
                        telemetry_context.run_id,
                        "model-turn",
                        {
                            "turn": turn.model_dump(mode="json"),
                            "provider_usage_reported": usage is not None,
                        },
                        task_id=task.id,
                        iteration=iteration,
                    )
            except AgentSdkError as error:
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    error.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=AgentFailure.from_sdk_error(error),
                )
            protected_episode_ids = frozenset()
            if turn.type == "blocked":
                return self._terminate(
                    AgentRunStatus.BLOCKED,
                    task,
                    iteration,
                    turn.reason,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                )
            if turn.type == "final":
                accepted = await self._accept_final_turn(
                    turn.output,
                    task,
                    iteration,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    deadline,
                )
                if isinstance(accepted, AgentResult):
                    return accepted
                observations.append(accepted)
                continue

            calls = [turn.call] if turn.type == "tool-call" else turn.calls
            outcome = await self._execute_tool_batch(
                task,
                iteration,
                ToolBatchTurn(calls=calls),
                prompt,
                episodes,
                memory,
                events,
                emit,
                deadline,
            )
            if isinstance(outcome, AgentResult):
                return self._with_projection_history(outcome, projection_history)
            observations.extend(outcome.observations)
            protected_episode_ids = frozenset(
                observation.episode_id
                for observation in outcome.observations
                if observation.episode_id is not None
            )
            if isinstance(self.model, ProviderToolResultConsumer) and continuation is not None:
                try:
                    continuation = await self._await_with_watchdog(
                        self.model.accept_tool_results(continuation, outcome.provider_results),
                        self.watchdog_policy.model_turn_timeout_seconds,
                        deadline,
                    )
                except TimeoutError:
                    turn_timeout = self.watchdog_policy.model_turn_timeout_seconds
                    timeout_detail = (
                        f"its {turn_timeout}s per-turn watchdog timeout"
                        if turn_timeout is not None
                        else "the run's configured deadline"
                    )
                    failure = AgentFailure(
                        code="WATCHDOG_MODEL_TIMEOUT",
                        message=f"Provider tool-result continuation exceeded {timeout_detail}.",
                        details={"watchdog_seconds": turn_timeout},
                    )
                    return self._terminate(
                        AgentRunStatus.FAILED,
                        task,
                        iteration,
                        f"WATCHDOG_MODEL_TIMEOUT: provider tool-result continuation exceeded "
                        f"{timeout_detail}; the provider may be overloaded or the forwarded tool "
                        "results may be too large for its current response time.",
                        prompt,
                        episodes,
                        events,
                        emit,
                        projection_history,
                        failure=failure,
                    )
                except AgentSdkError as error:
                    return self._terminate(
                        AgentRunStatus.FAILED,
                        task,
                        iteration,
                        f"MODEL_TOOL_CONTINUATION_FAILED: {error.message}",
                        prompt,
                        episodes,
                        events,
                        emit,
                        projection_history,
                        failure=AgentFailure.from_sdk_error(error),
                    )
                except Exception as error:
                    failure = AgentFailure(
                        code="MODEL_TOOL_CONTINUATION_FAILED",
                        message=str(error) or type(error).__name__,
                        details={"error_type": type(error).__name__},
                    )
                    return self._terminate(
                        AgentRunStatus.FAILED,
                        task,
                        iteration,
                        f"MODEL_TOOL_CONTINUATION_FAILED: {failure.message}",
                        prompt,
                        episodes,
                        events,
                        emit,
                        projection_history,
                        failure=failure,
                    )
                emit(
                    "provider-tool-results-forwarded",
                    iteration,
                    outcomes=[
                        {
                            "tool_call_id": result.call_id,
                            "tool": result.name,
                            "status": result.status,
                            "truncated": result.truncated,
                        }
                        for result in outcome.provider_results
                    ],
                )

        return self._terminate(
            AgentRunStatus.FAILED,
            task,
            self.definition.termination_policy.max_iterations,
            "Maximum iteration limit "
            f"({self.definition.termination_policy.max_iterations}) "
            "reached without accepted output.",
            prompt,
            episodes,
            events,
            emit,
            projection_history,
        )

    def _normalize_model_response(
        self,
        response: Any,
        continuation: ProviderContinuation | None,
    ) -> tuple[Any, ProviderContinuation | None, ProviderUsage | None]:
        if isinstance(response, ModelTurnResponse):
            raw_turn = response.turn
            next_continuation = response.continuation
            usage = response.usage
        else:
            raw_turn = response
            next_continuation = continuation
            usage = None
        try:
            return _AGENT_TURN_ADAPTER.validate_python(raw_turn), next_continuation, usage
        except ValidationError as error:
            field_errors = [
                f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
                for issue in error.errors()
            ]
            raise AgentSdkError(
                "MODEL_TURN_INVALID",
                "Model response does not match the declared agent-turn contract: "
                + "; ".join(field_errors[:5])
                + ".",
                {"field_errors": field_errors, "validation_error": str(error)},
            ) from error

    async def _accept_final_turn(
        self,
        output: Any,
        task: ScopedAgentTask,
        iteration: int,
        prompt: Any,
        episodes: InMemoryEpisodeGraph,
        events: list[AgentLifecycleEvent],
        emit: Callable[..., None],
        projection_history: Sequence[ContextProjectionMetadata],
        deadline: float | None,
    ) -> AgentResult | ModelObservation:
        try:
            validate_candidate_output(self.definition, output)
        except AgentSdkError as error:
            emit("output-rejected", iteration, reason=error.message)
            return ModelObservation(kind="agent-error", iteration=iteration, message=error.message)
        if self.definition.verification_gate_id:
            verification_span = self.profiler.start_span(
                ProfileSpanKind.VERIFICATION,
                self.definition.verification_gate_id,
                parent_span_id=self._profile_root_span.span_id if self._profile_root_span else None,
                attributes={"iteration": iteration},
            )
            try:
                decision = await self._await_with_watchdog(
                    self.verification_gates.evaluate(
                        self.definition.verification_gate_id,
                        output,
                        self.definition,
                        task,
                    ),
                    self.watchdog_policy.verification_timeout_seconds,
                    deadline,
                )
            except TimeoutError:
                self.profiler.finish_span(verification_span, ProfileSpanStatus.TIMED_OUT)
                verification_timeout = self.watchdog_policy.verification_timeout_seconds
                timeout_detail = (
                    f"its {verification_timeout}s verification watchdog timeout"
                    if verification_timeout is not None
                    else "the run's configured deadline"
                )
                reason = (
                    f"WATCHDOG_VERIFICATION_TIMEOUT: verification gate "
                    f"{self.definition.verification_gate_id!r} exceeded {timeout_detail}; the "
                    "gate implementation may be overloaded or the candidate output may be too "
                    "large for its current response time."
                )
                failure = AgentFailure(
                    code="WATCHDOG_VERIFICATION_TIMEOUT",
                    message=reason,
                    details={
                        "verification_gate_id": self.definition.verification_gate_id,
                        "watchdog_seconds": verification_timeout,
                    },
                )
                emit(
                    "verification-completed",
                    iteration,
                    passed=False,
                    reason=reason,
                    failure_code=failure.code,
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    reason,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=failure,
                )
            except AgentSdkError as error:
                self.profiler.finish_span(verification_span, ProfileSpanStatus.FAILED)
                emit(
                    "verification-completed",
                    iteration,
                    passed=False,
                    reason=error.message,
                    failure_code=error.code,
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    error.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=AgentFailure.from_sdk_error(error),
                )
            except Exception as error:
                self.profiler.finish_span(verification_span, ProfileSpanStatus.FAILED)
                failure = AgentFailure(
                    code="VERIFICATION_GATE_UNEXPECTED_ERROR",
                    message=f"Verification gate {self.definition.verification_gate_id!r} raised "
                    f"{type(error).__name__}: {error}",
                    details={"error_type": type(error).__name__},
                )
                emit(
                    "verification-completed",
                    iteration,
                    passed=False,
                    reason=failure.message,
                    failure_code=failure.code,
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    failure.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=failure,
                )
            if decision is None:
                self.profiler.finish_span(verification_span, ProfileSpanStatus.FAILED)
                failure = AgentFailure(
                    code="VERIFICATION_GATE_NO_DECISION",
                    message=f"Verification gate registry returned no decision for gate "
                    f"{self.definition.verification_gate_id!r}; the gate ID may not be "
                    "registered with this agent's verification_gates registry.",
                    details={"verification_gate_id": self.definition.verification_gate_id},
                )
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    failure.message,
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                    failure=failure,
                )
            self.profiler.finish_span(
                verification_span,
                ProfileSpanStatus.COMPLETED if decision.passed else ProfileSpanStatus.FAILED,
                attributes={"passed": decision.passed},
            )
            emit(
                "verification-completed",
                iteration,
                passed=decision.passed,
                reason=decision.reason,
            )
            if not decision.passed:
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    decision.reason or "Deterministic verification gate rejected output.",
                    prompt,
                    episodes,
                    events,
                    emit,
                    projection_history,
                )
        return self._terminate(
            AgentRunStatus.COMPLETED,
            task,
            iteration,
            None,
            prompt,
            episodes,
            events,
            emit,
            projection_history,
            output=output,
        )

    async def _execute_tool_batch(
        self,
        task: ScopedAgentTask,
        iteration: int,
        batch: ToolBatchTurn,
        prompt: Any,
        episodes: InMemoryEpisodeGraph,
        memory: InMemoryEpisodeStore,
        events: list[AgentLifecycleEvent],
        emit: Callable[..., None],
        deadline: float | None,
    ) -> ToolBatchExecution | AgentResult:
        emit("tool-batch-requested", iteration, call_ids=[call.id for call in batch.calls])
        pending = {call.id: call for call in batch.calls}
        outcomes: dict[str, ToolCallOutcome] = {}
        ordered_ids = [call.id for call in batch.calls]
        blocked_reason: str | None = None

        while pending:
            ready = [
                call
                for call in batch.calls
                if call.id in pending
                and all(dependency in outcomes for dependency in call.depends_on_call_ids)
            ]
            if not ready:
                return self._terminate(
                    AgentRunStatus.FAILED,
                    task,
                    iteration,
                    "Tool batch scheduler reached an unresolved dependency state.",
                    prompt,
                    episodes,
                    events,
                    emit,
                )

            dependency_blocked = [
                call
                for call in ready
                if any(
                    outcomes[dependency].result.status != "succeeded"
                    for dependency in call.depends_on_call_ids
                )
            ]
            for call in dependency_blocked:
                outcomes[call.id] = self._record_unexecuted_result(
                    call,
                    ToolExecutionResult(
                        status="blocked",
                        error="A declared tool-call dependency did not succeed.",
                    ),
                    iteration,
                )
                pending.pop(call.id)
                blocked_reason = blocked_reason or outcomes[call.id].result.error
                terminal = self._record_tool_outcome(
                    outcomes[call.id], task, iteration, prompt, episodes, events, emit
                )
                if terminal is not None:
                    return terminal

            executable = [call for call in ready if call not in dependency_blocked]
            if not executable:
                continue
            serial = next(
                (
                    call
                    for call in executable
                    if self._tool_definition(call) is None
                    or self._tool_definition(call).concurrency is ToolConcurrency.SERIAL
                ),
                None,
            )
            selected = [serial] if serial is not None else executable
            selected_outcomes = await asyncio.gather(
                *(
                    self._execute_profiled_tool_call(
                        task,
                        iteration,
                        call,
                        outcomes,
                        episodes,
                        memory,
                        emit,
                        deadline,
                    )
                    for call in selected
                )
            )
            for item in selected_outcomes:
                outcomes[item.call.id] = item
                pending.pop(item.call.id)
                terminal = self._record_tool_outcome(
                    item, task, iteration, prompt, episodes, events, emit
                )
                if terminal is not None:
                    return terminal
                if item.result.status == "blocked":
                    blocked_reason = blocked_reason or item.result.error
                if item.terminal_status is not None:
                    return self._terminate(
                        item.terminal_status,
                        task,
                        iteration,
                        item.terminal_reason or "Tool contract execution failed.",
                        prompt,
                        episodes,
                        events,
                        emit,
                        failure=item.result.failure,
                    )

            if blocked_reason is not None:
                for call in [candidate for candidate in batch.calls if candidate.id in pending]:
                    outcomes[call.id] = self._record_unexecuted_result(
                        call,
                        ToolExecutionResult(
                            status="blocked",
                            error="Tool batch stopped after a blocked tool call.",
                        ),
                        iteration,
                    )
                    pending.pop(call.id)
                    terminal = self._record_tool_outcome(
                        outcomes[call.id], task, iteration, prompt, episodes, events, emit
                    )
                    if terminal is not None:
                        return terminal
                break

        ordered_outcomes = [outcomes[call_id] for call_id in ordered_ids]
        emit(
            "tool-batch-completed",
            iteration,
            outcomes=[
                {"tool_call_id": item.call.id, "tool": item.call.name, "status": item.result.status}
                for item in ordered_outcomes
            ],
        )
        if blocked_reason is not None:
            return self._terminate(
                AgentRunStatus.BLOCKED,
                task,
                iteration,
                blocked_reason,
                prompt,
                episodes,
                events,
                emit,
            )
        return ToolBatchExecution(
            observations=tuple(item.observation for item in ordered_outcomes),
            provider_results=tuple(self._provider_tool_result(item) for item in ordered_outcomes),
        )

    async def _execute_profiled_tool_call(
        self,
        task: ScopedAgentTask,
        iteration: int,
        call: ToolCall,
        prior_outcomes: dict[str, ToolCallOutcome],
        episodes: InMemoryEpisodeGraph,
        memory: InMemoryEpisodeStore,
        emit: Callable[..., None],
        deadline: float | None,
    ) -> ToolCallOutcome:
        span = self.profiler.start_span(
            ProfileSpanKind.TOOL,
            call.name,
            parent_span_id=self._profile_root_span.span_id if self._profile_root_span else None,
            attributes={"iteration": iteration, "tool_call_id": call.id},
        )
        try:
            outcome = await self._execute_tool_call(
                task,
                iteration,
                call,
                prior_outcomes,
                episodes,
                memory,
                emit,
                deadline,
            )
        except Exception:
            self.profiler.finish_span(span, ProfileSpanStatus.FAILED)
            raise
        status = {
            "succeeded": ProfileSpanStatus.COMPLETED,
            "failed": ProfileSpanStatus.FAILED,
            "blocked": ProfileSpanStatus.BLOCKED,
        }[outcome.result.status]
        self.profiler.finish_span(span, status, attributes={"result_status": outcome.result.status})
        return outcome

    async def _execute_tool_call(
        self,
        task: ScopedAgentTask,
        iteration: int,
        call: ToolCall,
        prior_outcomes: dict[str, ToolCallOutcome],
        episodes: InMemoryEpisodeGraph,
        memory: InMemoryEpisodeStore,
        emit: Callable[..., None],
        deadline: float | None,
    ) -> ToolCallOutcome:
        tool = self._tool_definition(call)
        if tool is None:
            return self._record_unexecuted_result(
                call,
                ToolExecutionResult(
                    status="failed",
                    error=f'Tool "{call.name}" is not declared for this agent.',
                ),
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )
        if self.tool_executor is None:
            return self._record_unexecuted_result(
                call,
                ToolExecutionResult(
                    status="failed",
                    error=f'No tool executor is configured for declared tool "{call.name}".',
                ),
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )
        try:
            validate_tool_arguments(tool, call.arguments)
        except AgentSdkError as error:
            return self._record_unexecuted_result(
                call,
                ToolExecutionResult(
                    status="failed",
                    error=error.message,
                    failure=AgentFailure.from_sdk_error(error),
                ),
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )

        effective_call = self._effective_call(call, prior_outcomes)
        context = ToolInvocationContext(
            agent_identity=self.definition.identity,
            task=task,
            iteration=iteration,
            call=effective_call,
        )
        emit("tool-requested", iteration, tool=call.name, tool_call_id=call.id)
        try:
            for hook in self.pre_tool_hooks:
                decision = await self._await_with_watchdog(
                    hook(context), self.watchdog_policy.tool_call_timeout_seconds, deadline
                )
                if decision and not decision.allowed:
                    result = ToolExecutionResult(
                        status="blocked",
                        error=decision.reason or f'Tool "{call.name}" was denied by policy.',
                    )
                    return self._record_unexecuted_result(
                        call,
                        result,
                        iteration,
                        terminal_status=AgentRunStatus.BLOCKED,
                    )
            result = await self._await_with_watchdog(
                self.tool_executor.execute(tool, context),
                self.watchdog_policy.tool_call_timeout_seconds,
                deadline,
            )
            for hook in self.post_tool_hooks:
                await self._await_with_watchdog(
                    hook(context, result), self.watchdog_policy.tool_call_timeout_seconds, deadline
                )
        except TimeoutError:
            tool_timeout = self.watchdog_policy.tool_call_timeout_seconds
            timeout_detail = (
                f"its {tool_timeout}s tool-call watchdog timeout"
                if tool_timeout is not None
                else "the run's configured deadline"
            )
            failure = AgentFailure(
                code="WATCHDOG_TOOL_TIMEOUT",
                message=f'Tool "{call.name}" (pre-tool hook, execution, or post-tool hook) '
                f"exceeded {timeout_detail}.",
                details={"tool": call.name, "watchdog_seconds": tool_timeout},
            )
            result = ToolExecutionResult(status="failed", error=failure.message, failure=failure)
            return self._record_unexecuted_result(
                call,
                result,
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )
        except AgentSdkError as error:
            result = ToolExecutionResult(
                status="failed",
                error=f'Tool "{call.name}" lifecycle failed: {error.message}',
                failure=AgentFailure.from_sdk_error(error),
            )
            return self._record_unexecuted_result(
                call,
                result,
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )
        except Exception as error:
            failure = AgentFailure(
                code="TOOL_LIFECYCLE_UNEXPECTED_ERROR",
                message=f'Tool "{call.name}" lifecycle raised {type(error).__name__}: {error}',
                details={"tool": call.name, "error_type": type(error).__name__},
            )
            result = ToolExecutionResult(status="failed", error=failure.message, failure=failure)
            return self._record_unexecuted_result(
                call,
                result,
                iteration,
                terminal_status=AgentRunStatus.FAILED,
            )

        outcome = self._record_executed_result(
            tool, effective_call, result, iteration, episodes, memory
        )
        if self.audit_logs is not None and self._active_audit_run_id is not None:
            self.audit_logs.append(
                self._active_audit_run_id,
                "tool-result",
                {
                    "tool": call.name,
                    "tool_call_id": call.id,
                    "arguments": effective_call.arguments,
                    "result": result.model_dump(mode="json"),
                    "result_handle_id": outcome.observation.result.handle.handle_id
                    if outcome.observation.result is not None
                    else None,
                },
                task_id=task.id,
                iteration=iteration,
            )
        emit(
            "tool-completed", iteration, tool=call.name, tool_call_id=call.id, status=result.status
        )
        return outcome

    def _record_executed_result(
        self,
        tool: ToolDefinition,
        call: ToolCall,
        result: ToolExecutionResult,
        iteration: int,
        episodes: InMemoryEpisodeGraph,
        memory: InMemoryEpisodeStore,
    ) -> ToolCallOutcome:
        summary = self._episode_summary_text(call, result)
        payload = {"call": call.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        episode_id: str | None = None
        if tool.episode_kind.value == "exploratory":
            episode = episodes.add_exploratory(summary, payload)
            memory_episode = memory.open_exploratory(self.definition.identity, content=payload)
            memory.close(memory_episode.id, description=summary)
            episode_id = episode.id
        else:
            episode = episodes.add_action(summary, call.consumed_episode_ids, payload)
            memory.mark_accessed(call.consumed_episode_ids)
            manifest = result.output.get("manifest") if isinstance(result.output, dict) else None
            memory_episode = memory.open_action(
                self.definition.identity,
                call.consumed_episode_ids,
                content=payload,
                requires_manifest=tool.requires_manifest,
                eda_manifest=manifest if isinstance(manifest, dict) else None,
            )
            memory.close(memory_episode.id)
            episode_id = episode.id
        projected = self.context_projector.project_tool_result(call, result, self.result_journal)
        return ToolCallOutcome(
            call=call,
            result=result,
            observation=ModelObservation(
                kind="tool-result",
                iteration=iteration,
                message=self._tool_message(call, result, projected.handle.handle_id),
                tool_call_id=call.id,
                tool_name=call.name,
                episode_id=episode_id,
                result=projected,
            ),
            episode_id=episode_id,
            episode_kind=tool.episode_kind.value,
        )

    @staticmethod
    def _pask_relevance_query(task: ScopedAgentTask) -> str:
        """Return the bounded task signal used by deterministic PASK scoring.

        Raw observations and provider reasoning are deliberately excluded. The
        selector receives only declared task instructions, acceptance criteria,
        and the scope label; it stores a digest rather than this raw text in its
        compaction dossier.
        """

        return "\n".join([task.scope.label, task.instructions, *task.acceptance_criteria])[:16_384]

    def _record_unexecuted_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        iteration: int,
        terminal_status: AgentRunStatus | None = None,
    ) -> ToolCallOutcome:
        projected = self.context_projector.project_tool_result(call, result, self.result_journal)
        return ToolCallOutcome(
            call=call,
            result=result,
            observation=ModelObservation(
                kind="tool-result",
                iteration=iteration,
                message=self._tool_message(call, result, projected.handle.handle_id),
                tool_call_id=call.id,
                tool_name=call.name,
                result=projected,
            ),
            terminal_status=terminal_status,
            terminal_reason=result.error,
        )

    @staticmethod
    def _tool_message(call: ToolCall, result: ToolExecutionResult, handle_id: str) -> str:
        if result.status == "succeeded":
            return f'Tool "{call.name}" completed; full result is available via {handle_id}.'
        return (
            f'Tool "{call.name}" {result.status}: '
            f"{result.error or 'no reason supplied'}; full result is available via {handle_id}."
        )

    def _provider_tool_result(self, outcome: ToolCallOutcome) -> ProviderToolResult:
        """Create the only bounded raw-result path used by provider continuations.

        The normal next ``ModelContext`` remains state-first. A provider that issued a
        function call can consume this one-time projection to resolve that call in its
        native protocol; it is never appended to the SDK's ordinary observations.
        """

        content = json.dumps(
            outcome.result.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        limit = self.context_projector.policy.tool_result_preview_chars
        truncated = len(content) > limit
        if truncated:
            content = f"{content[:limit]}…[truncated]"
        return ProviderToolResult(
            call_id=outcome.call.id,
            name=outcome.call.name,
            status=outcome.result.status,
            content=content,
            truncated=truncated,
        )

    def _effective_call(
        self,
        call: ToolCall,
        prior_outcomes: dict[str, ToolCallOutcome],
    ) -> ToolCall:
        dependency_episode_ids = [
            outcome.episode_id
            for dependency_id in call.depends_on_call_ids
            if (outcome := prior_outcomes[dependency_id]).episode_kind == "exploratory"
            and outcome.episode_id is not None
        ]
        consumed = [*call.consumed_episode_ids, *dependency_episode_ids]
        return call.model_copy(update={"consumed_episode_ids": list(dict.fromkeys(consumed))})

    def _tool_definition(self, call: ToolCall) -> ToolDefinition | None:
        return next(
            (candidate for candidate in self.definition.tools if candidate.name == call.name), None
        )

    @staticmethod
    async def _await_with_watchdog(
        operation: Awaitable[Any],
        operation_timeout_seconds: float | None,
        deadline: float | None,
    ) -> Any:
        timeout = operation_timeout_seconds
        if deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            timeout = min(timeout, remaining) if timeout is not None else remaining
        if timeout is None:
            return await operation
        return await asyncio.wait_for(operation, timeout=timeout)

    def _record_provider_usage(
        self,
        telemetry_context: TelemetryContext,
        usage: ProviderUsage,
        iteration: int,
    ) -> None:
        if self.telemetry is None:
            return
        values = {
            "model.provider_input_tokens": usage.input_tokens,
            "model.provider_output_tokens": usage.output_tokens,
            "model.provider_cached_input_tokens": usage.cached_input_tokens,
            "model.provider_reasoning_tokens": usage.reasoning_tokens,
            "model.provider_context_window_tokens": usage.context_window_tokens,
        }
        for metric_id, value in values.items():
            if value is None:
                record_metric_unavailable(
                    self.telemetry,
                    telemetry_context,
                    metric_id,
                    "tokens",
                    "Selected provider did not report this token counter.",
                    details={"iteration": iteration, "request_id": usage.request_id},
                )
                continue
            record_metric_value(
                self.telemetry,
                telemetry_context,
                metric_id,
                float(value),
                "tokens",
                details={
                    "iteration": iteration,
                    "request_id": usage.request_id,
                    "kind": "provider-reported",
                },
            )

        if usage.context_window_tokens is None or usage.input_tokens is None:
            record_metric_unavailable(
                self.telemetry,
                telemetry_context,
                "model.provider_remaining_context_tokens",
                "tokens",
                "Provider did not report both context-window capacity and input usage.",
                details={"iteration": iteration, "request_id": usage.request_id},
            )
        else:
            record_metric_value(
                self.telemetry,
                telemetry_context,
                "model.provider_remaining_context_tokens",
                float(max(usage.context_window_tokens - usage.input_tokens, 0)),
                "tokens",
                details={
                    "iteration": iteration,
                    "request_id": usage.request_id,
                    "kind": "provider-reported",
                },
            )

    def _terminate(
        self,
        status: AgentRunStatus,
        task: ScopedAgentTask,
        iterations: int,
        reason: str | None,
        prompt: Any,
        episodes: InMemoryEpisodeGraph,
        events: list[AgentLifecycleEvent],
        emit: Callable[..., None],
        projection_history: Sequence[ContextProjectionMetadata] = (),
        output: Any = None,
        failure: AgentFailure | None = None,
    ) -> AgentResult:
        project_state = self._active_project_state
        recorded = False
        if project_state is not None and self._active_project_id is not None:
            try:
                project_state = self.project_state_store.apply(
                    self._active_project_id,
                    ProjectStateReducer.agent_result_transition(
                        task.id,
                        status.value,
                        project_state_hash_from_result(status, output, reason),
                    ),
                )
            except Exception as error:
                status, reason, failure = _demote_for_state_failure(
                    status, reason, failure, _state_update_failure(error)
                )
            else:
                self._active_project_state = project_state
                recorded = True
        escalation = None
        target = self.definition.termination_policy.escalation
        if status is not AgentRunStatus.COMPLETED and target.value != "none":
            escalation = AgentEscalation(
                target=target.value, reason=reason or "Agent did not complete."
            )
        emit(
            "terminated",
            iterations,
            status=status.value,
            reason=reason,
            failure_code=failure.code if failure is not None else None,
        )
        if escalation:
            emit("escalated", iterations, target=escalation.target, reason=escalation.reason)
        if recorded and project_state is not None:
            emit(
                "state-updated",
                iterations,
                project_id=project_state.project_id,
                revision=project_state.revision,
                state_hash=project_state.state_hash,
                action_id=f"agent-result:{task.id}",
            )
        profile_status = {
            AgentRunStatus.COMPLETED: ProfileSpanStatus.COMPLETED,
            AgentRunStatus.BLOCKED: ProfileSpanStatus.BLOCKED,
            AgentRunStatus.FAILED: ProfileSpanStatus.FAILED,
            AgentRunStatus.CANCELLED: ProfileSpanStatus.CANCELLED,
        }[status]
        if self._profile_root_span is not None:
            self.profiler.finish_span(
                self._profile_root_span,
                profile_status,
                attributes={"agent_status": status.value, "iterations": iterations},
            )
        profile = self.profiler.finish_run(profile_status)
        if self.telemetry is not None:
            telemetry_context = self.telemetry_context or TelemetryContext(
                run_id=task.id,
                task_id=task.id,
                agent_id=self.definition.identity,
            )
            profile_event = self.telemetry.emit(
                "agent.profile-completed",
                telemetry_context,
                actor=TelemetryActor(
                    kind="agent", identifier=self.definition.identity, role="base-agent"
                ),
                authority=TelemetryAuthority.DETERMINISTIC,
                status=status.value,
                payload={
                    "integrity_hash": profile.integrity_hash,
                    "wall_duration_ns": profile.wall_duration_ns,
                    "process_cpu_duration_ns": profile.process_cpu_duration_ns,
                    "phase_counts": {
                        summary.kind.value: summary.count for summary in profile.summaries
                    },
                },
            )
            if not self._provider_usage_reported:
                for metric_id in (
                    "model.provider_input_tokens",
                    "model.provider_output_tokens",
                    "model.provider_cached_input_tokens",
                    "model.provider_reasoning_tokens",
                    "model.provider_context_window_tokens",
                    "model.provider_remaining_context_tokens",
                ):
                    record_metric_unavailable(
                        self.telemetry,
                        telemetry_context,
                        metric_id,
                        "tokens",
                        "Selected model adapter did not return provider usage.",
                        source_event_id=profile_event.event_id,
                    )
            for span in profile.spans:
                if span.kind is ProfileSpanKind.TOOL:
                    record_metric_value(
                        self.telemetry,
                        telemetry_context,
                        "tool.duration_ms",
                        span.duration_ns / 1_000_000,
                        "milliseconds",
                        source_event_id=profile_event.event_id,
                        details={"tool": span.name, "status": span.status.value},
                    )
            record_terminal_agent_metrics(
                self.telemetry,
                telemetry_context,
                self.telemetry.list_events(telemetry_context.run_id, limit=1_000),
                profile.model_dump(mode="json"),
                completed=status is AgentRunStatus.COMPLETED,
                terminal_reason=reason,
                source_event_id=profile_event.event_id,
            )
            if self.audit_logs is not None:
                self.audit_logs.append(
                    telemetry_context.run_id,
                    "profile-completed",
                    {
                        "integrity_hash": profile.integrity_hash,
                        "status": status.value,
                        "phase_counts": {
                            summary.kind.value: summary.count for summary in profile.summaries
                        },
                    },
                    task_id=task.id,
                    iteration=iterations,
                )
        result = AgentResult(
            status=status,
            task_id=task.id,
            iterations=iterations,
            output=output,
            reason=reason,
            failure=failure,
            escalation=escalation,
            context=prompt,
            project_state=project_state.model_view() if project_state is not None else None,
            episodes=episodes.list(),
            projection_history=list(projection_history),
            events=list(events),
            profile=profile.model_dump(mode="json"),
        )
        self._active_project_id = None
        self._active_project_state = None
        self._profile_root_span = None
        self._active_audit_run_id = None
        return result

    def _record_tool_outcome(
        self,
        outcome: ToolCallOutcome,
        task: ScopedAgentTask,
        iteration: int,
        prompt: Any,
        episodes: InMemoryEpisodeGraph,
        events: list[AgentLifecycleEvent],
        emit: Callable[..., None],
    ) -> AgentResult | None:
        try:
            self._apply_tool_outcome_to_project_state(outcome, emit, iteration)
        except Exception as error:
            failure = _state_update_failure(error)
            return self._terminate(
                AgentRunStatus.FAILED,
                task,
                iteration,
                failure.message,
                prompt,
                episodes,
                events,
                emit,
                failure=failure,
            )
        return None

    def _apply_tool_outcome_to_project_state(
        self,
        outcome: ToolCallOutcome,
        emit: Callable[..., None],
        iteration: int,
    ) -> ProjectState:
        if self._active_project_id is None or self._active_project_state is None:
            raise RuntimeError(
                "Project state must be initialized before a tool outcome is recorded."
            )
        projected = outcome.observation.result
        if projected is None:
            raise RuntimeError("Tool outcomes must carry a projected journal result.")
        action_output_summary_chars = (
            self.project_state_projector.policy.action_output_summary_chars
        )
        state = self.project_state_store.apply(
            self._active_project_id,
            ProjectStateReducer.tool_transition(
                outcome.call,
                outcome.result,
                projected.handle,
                output_summary_max_chars=action_output_summary_chars,
            ),
            # apply()'s own summary wraps tool_transition's already-bounded
            # output_summary plus a few short fields (tool_call_id, tool_name,
            # status, error) - a second, independent hardcoded 2048 limit
            # here re-truncated that inner value once it was raised past
            # 2048, so this needs the same headroom, not just the inner call.
            summary_max_chars=action_output_summary_chars + 512,
        )
        self._active_project_state = state
        emit(
            "state-updated",
            iteration,
            project_id=state.project_id,
            revision=state.revision,
            state_hash=state.state_hash,
            action_id=outcome.call.id,
        )
        return state

    @staticmethod
    def _episode_summary_text(
        call: ToolCall, result: ToolExecutionResult, *, max_chars: int = 320
    ) -> str:
        arguments_preview = BaseAgent._bounded_json_text(call.arguments, 120)
        detail = (
            BaseAgent._bounded_json_text(result.output, max_chars)
            if result.status == "succeeded"
            else (result.error or "no error detail")[:max_chars]
        )
        return f'Tool "{call.name}"({arguments_preview}) {result.status}: {detail}'

    @staticmethod
    def _bounded_json_text(value: Any, max_chars: int) -> str:
        try:
            encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        except TypeError:
            encoded = str(value)
        return encoded if len(encoded) <= max_chars else f"{encoded[:max_chars]}...(truncated)"

    @staticmethod
    def _project_id_for(task: ScopedAgentTask) -> str:
        candidate = task.scope.boundaries.get("project_id")
        return candidate if isinstance(candidate, str) and candidate.strip() else task.id

    @staticmethod
    def _stage_schema_for(task: ScopedAgentTask) -> StageStateSchema:
        stage = task.scope.boundaries.get("stage")
        label = stage if isinstance(stage, str) and stage.strip() else "unclassified"
        return StageStateSchema(schema_id=f"{label}-v1", stage=label)

    @staticmethod
    def _estimate_model_context_tokens(prompt: Any, project_state_view: Any) -> int:
        """Conservatively estimate only material actually supplied to a model adapter."""

        payload = {
            "prompt": prompt.model_dump(mode="json"),
            "project_state": project_state_view.model_dump(mode="json"),
        }
        return estimate_tokens(payload)

    @staticmethod
    def _with_projection_history(
        result: AgentResult,
        projection_history: Sequence[ContextProjectionMetadata],
    ) -> AgentResult:
        return result.model_copy(update={"projection_history": list(projection_history)})


def _state_update_failure(error: Exception) -> AgentFailure:
    if isinstance(error, AgentSdkError):
        return AgentFailure.from_sdk_error(error)
    return AgentFailure(
        code="PROJECT_STATE_UPDATE_FAILED",
        message=f"Project state update raised {type(error).__name__}: {error}",
        details={"error_type": type(error).__name__},
    )


def _demote_for_state_failure(
    status: AgentRunStatus,
    reason: str | None,
    failure: AgentFailure | None,
    state_failure: AgentFailure,
) -> tuple[AgentRunStatus, str, AgentFailure | None]:
    if status is AgentRunStatus.COMPLETED:
        return (
            AgentRunStatus.FAILED,
            "The agent finished and its output was accepted, but the result could not be "
            f"recorded in project state: {state_failure.message}",
            state_failure,
        )
    detail = (
        f"Additionally, the result could not be recorded in project state: {state_failure.message}"
    )
    return status, f"{reason} {detail}" if reason else detail, failure


def project_state_hash_from_result(
    status: AgentRunStatus,
    output: Any,
    reason: str | None,
) -> str:
    """Produce provenance for a terminal state reduction without retaining its raw output."""

    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {"status": status.value, "output": output, "reason": reason},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
