# Copyright (c) 2026 David Michael Indraputra

"""One implementation per operation: artifact tools, verification, repair metrics."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nailong_agent_sdk import (
    ApprovalRegistry,
    ArtifactStore,
    CapabilityGrant,
    CapabilityPolicy,
    HarnessExecutionContext,
    HarnessToolExecutor,
    HarnessToolRegistry,
    ProcessSupervisor,
    ScopedAgentTask,
    ToolCall,
)
from nailong_agent_sdk.agent.verification import (
    VerificationDecision,
    normalize_verification_result,
)
from nailong_agent_sdk.foundations.contracts import (
    AgentResult,
    AgentRunStatus,
    EpisodeKind,
    ToolDefinition,
)
from nailong_agent_sdk.integrations.langchain import _agent_result_projection
from nailong_agent_sdk.state.planning import ModelTier, PlanTask
from nailong_agent_sdk.tools.tools import ToolInvocationContext

_TASK = ScopedAgentTask.model_validate(
    {
        "id": "t1",
        "input": {},
        "scope": {"label": "s"},
        "locked_interface": None,
        "instructions": "Read.",
        "acceptance_criteria": ["read"],
    }
)


def _executor(root: Path, authorized: list[str]) -> HarnessToolExecutor:
    plan_task = PlanTask(
        task_id="t1",
        scope="s",
        locked_interface={},
        instructions="Read.",
        acceptance_criteria="read",
        model_tier=ModelTier.STANDARD,
        authorized_artifact_ids=authorized,
    )
    policy = CapabilityPolicy(
        [CapabilityGrant(role="reader", capabilities=["artifact.read", "artifact.diff"])]
    )
    return HarnessToolExecutor(
        HarnessToolRegistry(),
        HarnessExecutionContext(
            run_id="r1",
            node_id="n1",
            role="reader",
            plan_task=plan_task,
            run_root=root,
            artifacts=ArtifactStore(root),
            policy=policy,
            approvals=ApprovalRegistry(),
            supervisor=ProcessSupervisor(),
        ),
    )


def _run(executor: HarnessToolExecutor, name: str, arguments: dict):
    tool = ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object"},
        episode_kind=EpisodeKind.EXPLORATORY,
    )
    call = ToolCall(id="c1", name=name, arguments=arguments)
    invocation = ToolInvocationContext(agent_identity="a", task=_TASK, iteration=1, call=call)
    return asyncio.run(executor.execute(tool, invocation))


def test_registry_authorizes_then_uses_the_core_artifact_tools(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    base = store.write_text("a.md", "one\ntwo\n").artifact_id
    draft = store.write_text("b.md", "one\nthree\n").artifact_id
    executor = _executor(tmp_path, [base, draft])

    read = _run(executor, "read_artifact", {"artifact_id": base})
    assert read.status == "succeeded" and read.output["content"] == "one\ntwo\n"
    grep = _run(executor, "grep_artifact", {"artifact_id": base, "needle": "two"})
    assert grep.output["matches"] == [2]
    diff = _run(
        executor,
        "diff_declared_artifacts",
        {"base_artifact_id": base, "draft_artifact_id": draft},
    )
    assert diff.status == "succeeded" and "+three" in str(diff.output)

    locked_out = _executor(tmp_path, [])
    denied = _run(locked_out, "read_artifact", {"artifact_id": base})
    assert denied.status == "failed" and "not authorized" in (denied.error or "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, VerificationDecision(True)),
        ((False, "no"), VerificationDecision(False, "no")),
        (VerificationDecision(True, "ok"), VerificationDecision(True, "ok")),
    ],
)
def test_verification_results_normalize_in_one_place(value, expected) -> None:
    assert normalize_verification_result(value) == expected


def test_verification_rejects_malformed_returns() -> None:
    with pytest.raises(TypeError):
        normalize_verification_result((True, 3))  # type: ignore[arg-type]


def test_langchain_projection_is_outcome_only_and_redacted() -> None:
    result = AgentResult(
        status=AgentRunStatus.COMPLETED,
        task_id="t1",
        iterations=2,
        output={"status": "complete", "api_key": "sk-live-secret-value-1234567890"},
        project_state={"internal": True},
        profile={"spans": []},
    )
    projected = _agent_result_projection(result)
    assert set(projected) == {
        "status",
        "task_id",
        "iterations",
        "output",
        "reason",
        "failure",
        "escalation",
    }
    assert "sk-live-secret-value-1234567890" not in str(projected)
    full = _agent_result_projection(result, include_diagnostics=True)
    assert "project_state" in full
    assert "sk-live-secret-value-1234567890" not in str(full)


def test_automatic_stage_failure_records_repair_metric(tmp_path: Path) -> None:
    from nailong_agent_sdk import TelemetryStore
    from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
    from nailong_agent_sdk.state.orchestration_models import (
        ComplexityRoutingRules,
        ControllerPhase,
        GapMetadata,
        SkillToolProfile,
    )
    from nailong_agent_sdk.state.planning import Plan
    from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
    from nailong_agent_sdk.state.stage_gates import StageCompletenessPolicy

    telemetry = TelemetryStore(tmp_path)
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    snapshot = SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h")
    record = runtime.create_controller(
        snapshot,
        SkillToolProfile(stage="draft", source_snapshot_id="snap"),
        ComplexityRoutingRules(multi_agent_min_categories=3, multi_agent_min_blast_radius=3),
        GapMetadata(),
        max_repair_attempts=2,
    )
    task = PlanTask(
        task_id="t1",
        scope="s",
        locked_interface={},
        instructions="Do.",
        acceptance_criteria="done",
        model_tier=ModelTier.STANDARD,
    )
    runtime.submit_plan(record.controller_id, Plan(plan_id="p1", tasks=[task]))
    runtime.approve_plan(record.controller_id, True)
    runtime.dispatch(record.controller_id)
    decision = runtime.evaluate_stage_completeness(
        record.controller_id,
        StageCompletenessPolicy(policy_id="gate", stage="draft", required_artifact_paths=["x"]),
    )
    assert not decision.complete
    assert runtime.get_controller(record.controller_id).phase is ControllerPhase.REPAIR_REQUIRED
    run_id = runtime.get_controller(record.controller_id).run_id
    metrics = [m for m in telemetry.list_metrics(run_id) if "repair" in m.metric_id]
    assert [m.value for m in metrics if m.metric_id == "controller.repair_attempt_count"] == [1.0]
    failures = [
        event
        for event in telemetry.iter_events(run_id)
        if event.event_type == "controller.stage-failure"
    ]
    assert failures[0].payload["trigger"] == "stage-completeness"
    telemetry.close()
