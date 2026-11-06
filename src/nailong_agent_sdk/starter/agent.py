# Copyright (c) 2026 David Michael Indraputra

"""One object that builds the definition, runtime and task, runs them and reports plainly."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..agent.base_agent.types import (
    AgentWatchdogPolicy,
    CancellationToken,
    MessageInbox,
    PauseGate,
)
from ..agent.cost_model import PriceTable
from ..agent.evidence_profile import EvidenceProfile
from ..agent.model import AgentModel, FailoverAgentModel, ModelStreamEvent, ModelStreamListener
from ..agent.runtime import AgentRuntimeServices
from ..agent.verification import VerificationGateRegistry
from ..foundations.contracts import (
    AgentDefinition,
    AgentResult,
    AgentRunStatus,
    ModelBinding,
    ScopedAgentTask,
    SkillContext,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from ..memory.context_projection import ContextProjectionPolicy
from ..memory.episode_store import InMemoryEpisodeStore
from .checks import CHECK_GATE_ID, OutputCheck, gate_callback
from .hooks import AfterTool, BeforeTool, after_tool_hooks, before_tool_hooks
from .images import with_images
from .models import ModelSetup
from .session_store import SessionStore, validate_session_id
from .sessions import DEFAULT_HISTORY_CHARS, DEFAULT_HISTORY_TURNS, Session
from .toolbox import Approver, Toolbox, ToolGroup
from .tools import FunctionTool, tool

DEFAULT_WORKSPACE = Path(".nailong-agent")
DEFAULT_CRITERION = "The request is answered completely and accurately."
REPLY_FORMAT = (
    "\n\nWhen you are finished, reply with exactly one JSON object of the form "
    '{"type":"final","output":{...}} where output matches the output schema; the "type" and '
    '"output" keys are required. Use {"type":"blocked","reason":"..."} if you cannot continue.'
)
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"const": "complete"}, "answer": {"type": "string"}},
    "required": ["status", "answer"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Answer:
    status: str
    output: Any
    reason: str | None
    steps: int
    result: AgentResult
    session: str = ""
    cost: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == AgentRunStatus.COMPLETED.value

    @property
    def text(self) -> str:
        if isinstance(self.output, dict) and isinstance(self.output.get("answer"), str):
            return self.output["answer"]
        if isinstance(self.output, BaseModel):
            return self.output.model_dump_json()
        return "" if self.output is None else json.dumps(self.output, sort_keys=True)

    def __str__(self) -> str:
        return self.text if self.ok else f"[{self.status}] {self.reason or 'no reason recorded'}"


class Agent:
    def __init__(
        self,
        *,
        model: ModelSetup,
        instructions: str,
        tools: Sequence[FunctionTool | ToolGroup] = (),
        fallbacks: Sequence[ModelSetup] = (),
        output: dict[str, Any] | type[BaseModel] | None = None,
        approve: Approver | None = None,
        recoverable_refusals: bool = False,
        before_tool: Sequence[BeforeTool] = (),
        after_tool: Sequence[AfterTool] = (),
        check: OutputCheck | None = None,
        on_stream: ModelStreamListener | None = None,
        skills: Sequence[SkillContext] = (),
        max_steps: int = 12,
        deadline_seconds: float | None = None,
        model_turn_timeout_seconds: float | None = None,
        tool_timeout_seconds: float | None = None,
        invalid_output_retries: int = 2,
        context: ContextProjectionPolicy | None = None,
        memory: Callable[[], InMemoryEpisodeStore] | None = None,
        history_limit: int | None = None,
        prices: PriceTable | None = None,
        workspace: Path = DEFAULT_WORKSPACE,
        evidence: EvidenceProfile | None = None,
        services: AgentRuntimeServices | None = None,
        name: str = "agent",
    ) -> None:
        if services is not None and evidence is not None:
            raise ValueError(
                "Pass either services or evidence, not both: an evidence profile is applied when "
                "the runtime services are opened, and services you pass are already open."
            )
        self._name = name
        self._recoverable = recoverable_refusals
        self._model_class = output if isinstance(output, type) else None
        self._toolbox = Toolbox(
            tools,
            approve,
            guards=before_tool if recoverable_refusals else (),
            refusal_status="failed" if recoverable_refusals else "blocked",
        )
        self._model = _model_for(model, fallbacks)
        self._on_stream = on_stream
        self._skills = list(skills)
        self._pre_hooks = [] if recoverable_refusals else before_tool_hooks(before_tool)
        self._post_hooks = after_tool_hooks(after_tool)
        self._gates = _gates_for(check)
        self._watchdog = _watchdog_for(
            deadline_seconds,
            model_turn_timeout_seconds,
            tool_timeout_seconds,
            invalid_output_retries,
        )
        self._context = context
        self._memory = memory
        self._history_limit = history_limit
        self._prices = prices
        self.services = services or AgentRuntimeServices.open(workspace, evidence=evidence)
        self._sessions = SessionStore(self.services.run_root)
        self._instructions = instructions
        self.inbox = MessageInbox()
        self.pause = PauseGate()
        self._binding = ModelBinding(
            provider=model.provider,
            model=model.name,
            parameters=dict(model.parameters),
            fallbacks=[setup.binding for setup in fallbacks],
        )
        self._output_schema = _output_schema(output)
        self._max_steps = max_steps
        self._gate_id = CHECK_GATE_ID if check is not None else None

    @property
    def name(self) -> str:
        return self._name

    @property
    def definition(self) -> AgentDefinition:
        return AgentDefinition(
            identity=self._name,
            instructions=VersionedInstructions(version="1", text=self._instructions + REPLY_FORMAT),
            input_schema={"type": "object"},
            tools=self._toolbox.definitions,
            model_binding=self._binding,
            output_schema=self._output_schema,
            termination_policy=TerminationPolicy(
                max_iterations=self._max_steps, status_field="status"
            ),
            verification_gate_id=self._gate_id,
        )

    def session(
        self,
        name: str | None = None,
        *,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        history_chars: int = DEFAULT_HISTORY_CHARS,
    ) -> Session:
        session_id = validate_session_id(name) if name else f"session-{uuid.uuid4().hex[:8]}"
        self._sessions.create(session_id, self._name)
        return Session(self, self._sessions, session_id, history_turns, history_chars)

    def resume(
        self,
        name: str,
        *,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        history_chars: int = DEFAULT_HISTORY_CHARS,
    ) -> Session:
        self._sessions.verify(name)
        return Session(self, self._sessions, name, history_turns, history_chars)

    def sessions(self) -> list[str]:
        return self._sessions.ids()

    def latest_session(self) -> str | None:
        return self._sessions.latest()

    def delete_session(self, session_id: str) -> None:
        self._sessions.delete(session_id)

    async def run(
        self,
        prompt: str,
        *,
        input: dict[str, Any] | None = None,
        criteria: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
        session: str | None = None,
        resume_from: Answer | None = None,
        images: Sequence[Path | dict[str, str]] = (),
        cancellation: CancellationToken | None = None,
    ) -> Answer:
        return await self._execute(
            prompt,
            input,
            criteria,
            skills,
            session,
            resume_from,
            images,
            cancellation,
            self._on_stream,
        )

    async def stream(
        self,
        prompt: str,
        *,
        input: dict[str, Any] | None = None,
        criteria: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
        session: str | None = None,
        resume_from: Answer | None = None,
        images: Sequence[Path | dict[str, str]] = (),
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent | Answer]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        finished = object()

        async def listener(event: ModelStreamEvent) -> None:
            if self._on_stream is not None:
                outcome = self._on_stream(event)
                if inspect.isawaitable(outcome):
                    await outcome
            await queue.put(event)

        async def drive() -> Answer:
            try:
                return await self._execute(
                    prompt,
                    input,
                    criteria,
                    skills,
                    session,
                    resume_from,
                    images,
                    cancellation,
                    listener,
                )
            finally:
                await queue.put(finished)

        runner = asyncio.create_task(drive())
        try:
            while (item := await queue.get()) is not finished:
                yield item
            yield runner.result()
        finally:
            if not runner.done():
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner

    def run_sync(
        self,
        prompt: str,
        *,
        input: dict[str, Any] | None = None,
        criteria: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
        session: str | None = None,
        resume_from: Answer | None = None,
        images: Sequence[Path | dict[str, str]] = (),
    ) -> Answer:
        if any(getattr(group, "loop_bound", False) for group in self._toolbox.groups):
            raise RuntimeError(
                "Agent.run_sync cannot be used with MCP tools: their connections belong to one "
                "event loop, so connect, run and close inside the same `async with` block and "
                "use `await agent.run(...)`."
            )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.run(
                    prompt,
                    input=input,
                    criteria=criteria,
                    skills=skills,
                    session=session,
                    resume_from=resume_from,
                    images=images,
                )
            )
        raise RuntimeError(
            "Agent.run_sync was called inside a running event loop; use `await agent.run(...)`."
        )

    def as_tool(
        self,
        *,
        name: str,
        description: str,
        needs_approval: bool = False,
        read_only: bool = False,
    ) -> FunctionTool:
        async def delegate(task: str) -> str:
            answer = await self.run(task)
            if not answer.ok:
                raise RuntimeError(f'Agent "{self._name}" did not complete: {answer.reason}')
            return answer.text

        return tool(
            delegate,
            name=name,
            description=description,
            needs_approval=needs_approval,
            read_only=read_only,
        )

    def say(self, message: str) -> None:
        self.inbox.put(message)

    def close(self) -> None:
        self.services.audit_logs.close()
        self.services.telemetry.close()

    def __enter__(self) -> Agent:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    async def _execute(
        self,
        prompt: str,
        input: dict[str, Any] | None,
        criteria: Sequence[str],
        skills: Sequence[SkillContext],
        session: str | None,
        resume_from: Answer | None,
        images: Sequence[Path | dict[str, str]],
        cancellation: CancellationToken | None,
        listener: ModelStreamListener | None,
    ) -> Answer:
        task_id = f"{self._name}-{uuid.uuid4().hex[:12]}"
        session = session or (resume_from.session if resume_from is not None else None)
        task_input = dict(input or {})
        with_images(task_input, images)
        if resume_from is not None:
            task_input["previous_answer"] = _json_safe(resume_from.output)
        task = ScopedAgentTask(
            id=task_id,
            input=task_input,
            scope=TaskScope(
                label=self._name, boundaries={"project_id": session} if session else {}
            ),
            locked_interface={},
            instructions=prompt,
            acceptance_criteria=list(criteria) or [DEFAULT_CRITERION],
            skills=[*self._skills, *skills],
        )
        agent = self.services.create_agent(
            self.definition,
            self._model,
            tool_executor=self._toolbox if self._toolbox.definitions else None,
            recover_from_unknown_tools=self._recoverable,
            verification_gates=self._gates,
            pre_tool_hooks=self._pre_hooks,
            post_tool_hooks=self._post_hooks,
            watchdog_policy=self._watchdog,
            context_projection_policy=self._context,
            episode_store_factory=self._memory,
            on_model_stream=listener,
            episode_history_limit=self._history_limit,
            price_table=self._prices,
            inbox=self.inbox,
            pause=self.pause,
        )
        result = await agent.run(task, cancellation)
        await self.services.checkpoint_after_run(task_id)
        cost = agent.cost_ledger.to_json() if agent.cost_ledger.calls else None
        return self._answer(result, session or task_id, cost)

    def _answer(self, result: AgentResult, session: str, cost: dict[str, Any] | None) -> Answer:
        status, output, reason = result.status.value, result.output, result.reason
        if self._model_class is not None and result.status is AgentRunStatus.COMPLETED:
            try:
                output = self._model_class.model_validate(output)
            except ValidationError as error:
                status = AgentRunStatus.FAILED.value
                reason = f"The output did not validate as {self._model_class.__name__}: {error}"
        return Answer(status, output, reason, result.iterations, result, session, cost)


def _model_for(model: ModelSetup, fallbacks: Sequence[ModelSetup]) -> AgentModel:
    if not fallbacks:
        return model.model
    setups = [model, *fallbacks]
    return FailoverAgentModel(
        [setup.model for setup in setups], bindings=[setup.binding for setup in setups]
    )


def _output_schema(output: dict[str, Any] | type[BaseModel] | None) -> dict[str, Any]:
    if output is None:
        return ANSWER_SCHEMA
    if isinstance(output, type):
        return output.model_json_schema()
    return output


def _gates_for(check: OutputCheck | None) -> VerificationGateRegistry | None:
    if check is None:
        return None
    registry = VerificationGateRegistry()
    registry.register_callable(CHECK_GATE_ID, gate_callback(check))
    return registry


def _watchdog_for(
    deadline: float | None,
    model_turn: float | None,
    tool_call: float | None,
    invalid_output_retries: int = 2,
) -> AgentWatchdogPolicy | None:
    if (
        deadline is None
        and model_turn is None
        and tool_call is None
        and invalid_output_retries == 2
    ):
        return None
    return AgentWatchdogPolicy(
        invalid_output_retries=invalid_output_retries,
        run_deadline_seconds=deadline,
        model_turn_timeout_seconds=model_turn,
        tool_call_timeout_seconds=tool_call,
    )


def _json_safe(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value
