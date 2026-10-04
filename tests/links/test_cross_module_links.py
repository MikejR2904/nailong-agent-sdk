import hashlib
import json

import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding, GraphAgentExecutor
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.inspect import inspect_run
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    AgentRunStatus,
    EscalationTarget,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import Plan
from nailong_agent_sdk.tools.approvals import ApprovalStatus
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy
from nailong_agent_sdk.tools.registry import (
    HarnessExecutionContext,
    HarnessToolExecutor,
    HarnessToolRegistry,
)
from nailong_agent_sdk.tools.supervisor import ProcessSupervisor
from tests.support.agents import COMPLETE_SCHEMA, DynamicModel, arun, definition, results_of
from tests.support.agents import task as agent_task
from tests.support.controllers import executing_runtime
from tests.support.plans import P
from tests.support.plans import task as plan_task
from tests.support.tools import build

CORE = {definition_.name: definition_ for definition_ in core_tool_definitions()}


def test_governed_run_end_to_end_with_durable_services(tmp_path):
    run_root = tmp_path / "run"
    (run_root / "specs").mkdir(parents=True)
    (run_root / "data").mkdir()
    (run_root / "specs" / "readme.md").write_text("alpha\nbeta needle\ngamma TBD\n", "utf-8")
    big_lines = "".join(f"line {i} " + "x" * 40 + "\n" for i in range(6000))
    (run_root / "data" / "big.txt").write_text(big_lines, "utf-8")
    services = AgentRuntimeServices.open(run_root)
    try:
        executor, context = build(
            tmp_path, journal=services.result_journal, declared=("out/design.md",)
        )
        base = context.artifacts.write_text("base.md", "alpha\nbeta needle\ngamma TBD\n")
        context.plan_task.authorized_artifact_ids.append(base.artifact_id)
        seen = {}

        def script(model_context):
            iteration = model_context.iteration
            results = results_of(model_context)
            if iteration == 1:
                return {
                    "type": "tool-batch",
                    "calls": [
                        {"id": "g1", "name": "glob", "arguments": {"pattern": "specs/*.md"}},
                        {"id": "g2", "name": "grep", "arguments": {"pattern": "needle"}},
                        {"id": "g3", "name": "read_file", "arguments": {"path": "specs/readme.md"}},
                        {"id": "g4", "name": "read_file", "arguments": {"path": "data/big.txt"}},
                    ],
                }
            if iteration == 2:
                seen["g"] = {
                    key: results[key].result.model_dump() for key in ("g1", "g2", "g3", "g4")
                }
                seen["handle"] = results["g4"].result.handle
                return {
                    "type": "tool-call",
                    "call": {
                        "id": "h1",
                        "name": "get_tool_result",
                        "arguments": {"handle_id": results["g4"].result.handle.handle_id},
                    },
                }
            if iteration == 3:
                seen["h1"] = results["h1"].result.model_dump()
                return {
                    "type": "tool-call",
                    "call": {
                        "id": "w1",
                        "name": "write_draft",
                        "arguments": {
                            "path": "out/design.md",
                            "content": "alpha\nbeta needle\ngamma TBD\n",
                        },
                    },
                }
            if iteration == 4:
                return {
                    "type": "tool-call",
                    "call": {
                        "id": "e1",
                        "name": "edit_draft",
                        "arguments": {
                            "path": "out/design.md",
                            "old_text": "TBD",
                            "new_text": "done",
                        },
                    },
                }
            if iteration == 5:
                artifact = results["e1"].result.preview["artifact"]
                seen["edited"] = artifact
                return {
                    "type": "tool-call",
                    "call": {
                        "id": "d1",
                        "name": "diff_declared_artifacts",
                        "arguments": {
                            "base_artifact_id": base.artifact_id,
                            "draft_artifact_id": artifact["artifact_id"],
                            "draft_occurrence_id": artifact.get("occurrence_id", ""),
                        },
                    },
                }
            seen["diff"] = results["d1"].result.model_dump()
            return {"type": "final", "output": {"status": "complete", "summary": "done"}}

        subject = services.create_agent(
            definition(tools=list(CORE.values()), max_iterations=8),
            DynamicModel(script),
            tool_executor=executor,
        )
        result = arun(subject.run(agent_task("T1")))
        assert result.status is AgentRunStatus.COMPLETED and result.iterations == 6, (
            result.status,
            result.reason,
        )

        events = list(services.telemetry.iter_events("T1"))
        requested = [e for e in events if e.event_type == "agent.tool-requested"]
        completed = [e for e in events if e.event_type == "agent.tool-completed"]
        assert len(requested) == len(completed) == 8
        assert (
            events[-1].event_type == "agent.profile-completed"
            and events[0].event_type == "agent.run-started"
        )

        audit_entries = services.audit_logs.list_entries("T1", limit=1000)
        rendered = json.dumps([entry.model_dump(mode="json") for entry in audit_entries])
        for name in (
            "glob",
            "grep",
            "read_file",
            "get_tool_result",
            "write_draft",
            "edit_draft",
            "diff_declared_artifacts",
        ):
            assert name in rendered, f"audit transcript has no record of {name}"

        handle = seen["handle"]
        journal_payload = services.result_journal.read(handle.handle_id)
        canonical = json.dumps(
            journal_payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        assert (
            hashlib.sha256(canonical).hexdigest() == handle.content_hash
            and len(canonical) == handle.byte_count
        )
        recovered = seen["h1"]
        assert recovered["status"] == "succeeded"

        assert (run_root / "out" / "design.md").read_text(
            "utf-8"
        ) == "alpha\nbeta needle\ngamma done\n"
        diff_text = json.dumps(seen["diff"])
        assert "-gamma TBD" in diff_text and "+gamma done" in diff_text, diff_text[:300]

        state = result.project_state
        recorded = next(a for a in state["artifacts"] if a["relative_path"] == "out/design.md")
        assert recorded["artifact_id"] == seen["edited"]["artifact_id"], (
            "project state still points at the pre-edit artifact"
        )

        services.telemetry.close()
        services.audit_logs.close()
        inspection = inspect_run(run_root, "T1")
        assert (
            inspection.telemetry_chain_valid
            and inspection.audit_chain_valid
            and inspection.event_count == len(events)
        )
    finally:
        services.telemetry.close()
        services.audit_logs.close()


def single_plan():
    return Plan(plan_id="p1", tasks=[plan_task("T1", ["a"], [("a", P)])])


def blocked_run_setup(run_root):
    coordinator = HarnessCoordinator(run_root)
    run = coordinator.start_run(single_plan())
    services = AgentRuntimeServices.open(run_root)
    approvals = coordinator.approvals(run.run_id)
    plan_task_record = single_plan().tasks[0]
    approval_map = {}

    def make_binding():
        def definition_for():
            return AgentDefinition(
                identity="worker:T1",
                instructions=VersionedInstructions(version="v1", text="write the draft"),
                input_schema={"type": "object"},
                tools=[CORE["write_draft"]],
                model_binding=ModelBinding(provider="fake", model="fake-1"),
                output_schema=COMPLETE_SCHEMA,
                termination_policy=TerminationPolicy(
                    max_iterations=4, status_field="status", escalation=EscalationTarget.NONE
                ),
            )

        def task_adapter(node, node_context):
            return ScopedAgentTask(
                id="T1",
                input={},
                scope=TaskScope(label="s"),
                locked_interface={},
                instructions="i",
                acceptance_criteria=["c"],
            )

        def model_factory(node, node_context):
            return ScriptedModel(
                [
                    {
                        "type": "tool-call",
                        "call": {
                            "id": "w1",
                            "name": "write_draft",
                            "arguments": {"path": "out/a.md", "content": "hello\n"},
                        },
                    },
                    {"type": "final", "output": {"status": "complete"}},
                ]
            )

        def tool_executor_factory(node, node_context):
            hctx = HarnessExecutionContext(
                run_id=run.run_id,
                node_id=node.node_id,
                role="worker",
                plan_task=plan_task_record,
                run_root=run_root,
                artifacts=ArtifactStore(run_root),
                policy=CapabilityPolicy(
                    [
                        CapabilityGrant(
                            role="worker", capabilities=["draft.write"], allowed_paths=["."]
                        )
                    ]
                ),
                approvals=approvals,
                supervisor=ProcessSupervisor([]),
                spec_snapshots={},
                declared_output_paths=("out/a.md",),
                approval_ids_by_capability=dict(approval_map),
                result_journal=services.result_journal,
            )
            return HarnessToolExecutor(HarnessToolRegistry(), hctx)

        return GraphAgentBinding(
            node_id="node:T1",
            definition=definition_for(),
            task_adapter=task_adapter,
            model_factory=model_factory,
            tool_executor_factory=tool_executor_factory,
        )

    executor = GraphAgentExecutor(services, {"node:T1": make_binding()})
    return coordinator, run, services, approvals, approval_map, executor


def test_a_node_blocked_on_an_approval_can_continue_after_the_approval_is_granted(tmp_path):
    run_root = tmp_path / "run"
    run_root.mkdir()
    coordinator, run, services, approvals, approval_map, executor = blocked_run_setup(run_root)
    try:
        first = arun(coordinator.execute_run(run.run_id, executor.executors()))
        first_status = first.graph["statuses"]["node:T1"]
        pending = approvals.list(run.run_id)
        first.graph["results"]["node:T1"].get("reason")
        assert (
            first_status == "blocked"
            and len(pending) == 1
            and pending[0].capability == "draft.write"
        )

        coordinator.submit_approval(
            run.run_id, pending[0].approval_id, True, "approved by the designer"
        )
        approval_map["draft.write"] = pending[0].approval_id
        assert approvals.get(pending[0].approval_id).status is ApprovalStatus.APPROVED
        coordinator.resume_run(run.run_id)
        second = arun(coordinator.execute_run(run.run_id, executor.executors()))
        second_status = second.graph["statuses"]["node:T1"]
        assert second_status == "completed" and (run_root / "out" / "a.md").exists()
    finally:
        services.telemetry.close()
        services.audit_logs.close()


def test_controller_completion_requires_the_graph_to_have_run(tmp_path):
    runtime, controller_id = executing_runtime(tmp_path / "never-ran")
    with pytest.raises(ValueError) as never_ran:
        runtime.complete(controller_id)
    message = str(never_ran.value)
    assert "node:T1 (runnable)" in message and "node:T2 (pending)" in message
    assert runtime.get_controller(controller_id).phase.value == "executing"


def test_controller_completion_is_refused_when_every_node_is_blocked(tmp_path):
    async def blocked(node, node_context):
        return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="needs approval")

    runtime, controller_id = executing_runtime(tmp_path / "all-blocked")
    arun(runtime.execute_graph(controller_id, {GraphNodeKind.AGENT: blocked}))
    assert runtime.get_controller(controller_id).phase.value == "executing"
    with pytest.raises(ValueError, match="nodes that did not complete: node:T1 \\(blocked\\)"):
        runtime.complete(controller_id)
    assert runtime.get_controller(controller_id).phase.value == "executing"
