# Copyright (c) 2026 David Michael Indraputra

"""The nailong-agent-sdk arm: a real AgentTaskRunner run behind the recording transport."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.agent.verification import (
    VerificationContext,
    VerificationDecision,
    VerificationGateRegistry,
)
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.observability.telemetry_models import TelemetryAuthority
from nailong_agent_sdk.tools.core import core_tool_definitions

from ..acceptance import Acceptance
from ..cases import Case, FaultSpec
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..schema import RunTruth
from ..truth import build_truth
from ..wire import PolicyEndpoint, SdkTransport

ENDPOINT = {
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-bench-0123456789abcdef",
    "allow_insecure_http": True,
}
COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"const": "complete"}},
    "required": ["status"],
    "additionalProperties": True,
}
CHECKPOINT_KEY = b"trust-bench-checkpoint-key-0123456789"
GATE_ID = "bench-acceptance"


@dataclass
class SdkRun:
    case: Case
    fault: FaultSpec | None
    run_id: str
    run_dir: Path
    run_root: Path
    workspace: Path
    status: str
    reason: str | None
    truth: RunTruth
    anchor_dir: Path | None
    agent_run_ids: tuple[str, ...] = ()
    graph_run_id: str | None = None

    @property
    def audit_run_ids(self) -> tuple[str, ...]:
        return self.agent_run_ids or (self.run_id,)


def run_id_for(case: Case, fault: FaultSpec | None) -> str:
    if fault is None:
        return f"{case.case_id}.clean"
    variant = f"-{fault.variant}" if fault.variant else ""
    return f"{case.case_id}.{fault.operator}{variant}"


def definition_for(case: Case, fault: FaultSpec | None) -> AgentDefinition:
    available = {tool.name: tool for tool in core_tool_definitions()}
    return AgentDefinition(
        identity=case.agent_label,
        instructions=VersionedInstructions(version="v1", text=case.task_text),
        input_schema={"type": "object"},
        tools=[available[name] for name in case.tools],
        model_binding=ModelBinding(provider="bench", model="policy-1"),
        output_schema=COMPLETE_SCHEMA,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=case.max_iterations(fault),
            status_field="status",
            escalation=EscalationTarget.NONE,
        ),
    )


def gate_registry(acceptance: Acceptance, workspace: Path) -> VerificationGateRegistry:
    def decide(context: VerificationContext) -> VerificationDecision:
        passed, reason = acceptance.check(workspace)
        return VerificationDecision(
            passed,
            None if passed else reason,
            verifier_id=acceptance.verifier_id,
            authority=TelemetryAuthority.DETERMINISTIC,
            evidence={
                "reason": reason[:200],
                "subject_sha256": acceptance.subject_digest(workspace),
            },
        )

    registry = VerificationGateRegistry()
    registry.register_callable(GATE_ID, decide)
    return registry


def options_for(case: Case) -> TaskRunOptions:
    capabilities = ["filesystem.read", "draft.write"]
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": capabilities,
                "approved_capabilities": capabilities,
            },
            "declared_output_paths": list(case.output_paths),
        }
    )


def run_sdk(
    case: Case,
    fault: FaultSpec | None,
    run_dir: Path,
    *,
    anchored: bool = False,
    acceptance: Acceptance | None = None,
) -> SdkRun:
    if len(case.nodes()) > 1:
        from .nailong_graph import run_sdk_graph

        return run_sdk_graph(case, fault, run_dir, acceptance=acceptance)
    run_id = run_id_for(case, fault)
    run_root = run_dir / "run"
    anchor_dir = run_dir / "anchor" if anchored else None
    checkpointer = None
    if anchor_dir is not None:
        signer = HmacSha256Signer(CHECKPOINT_KEY, signer_id="trust-bench")
        checkpointer = RunHeadCheckpointer(signer, FileCheckpointSink(anchor_dir))
    workspace = run_root / "workspaces" / file_safe_name(run_id)
    case.materialize(workspace, fault)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    endpoint = PolicyEndpoint(
        policy=case.policy(fault),
        recorder=recorder,
        agent_id=case.agent_label,
        watcher=WorkspaceWatcher(workspace),
    )
    services = AgentRuntimeServices.open(run_root, checkpointer=checkpointer)
    task = ScopedAgentTask(
        id=run_id,
        input={},
        scope=TaskScope(label=case.family, boundaries={}),
        locked_interface={},
        instructions=case.task_text,
        acceptance_criteria=["every requested output is written"],
    )
    definition = definition_for(case, fault)
    gates = None
    if acceptance is not None:
        definition = definition.model_copy(update={"verification_gate_id": GATE_ID})
        gates = gate_registry(acceptance, workspace)
    try:
        result = asyncio.run(
            AgentTaskRunner(services, transport=SdkTransport(endpoint)).run(
                definition, task, options_for(case), verification_gates=gates
            )
        )
    finally:
        endpoint.finish()
        recorder.close()
        services.audit_logs.close()
        services.telemetry.close()
    return SdkRun(
        case=case,
        fault=fault,
        run_id=run_id,
        run_dir=run_dir,
        run_root=run_root,
        workspace=workspace,
        status=result.status.value,
        reason=result.reason,
        truth=build_truth(case, recorder.events),
        anchor_dir=anchor_dir,
    )
