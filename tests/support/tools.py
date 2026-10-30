import asyncio
import os
import subprocess
from pathlib import Path

from nailong_agent_sdk.foundations.contracts import (
    EpisodeKind,
    ScopedAgentTask,
    TaskScope,
    ToolCall,
    ToolDefinition,
)
from nailong_agent_sdk.state.planning import ModelTier, PlanTask
from nailong_agent_sdk.tools.approvals import ApprovalRegistry
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy
from nailong_agent_sdk.tools.registry import (
    HarnessExecutionContext,
    HarnessToolExecutor,
    HarnessToolRegistry,
)
from nailong_agent_sdk.tools.supervisor import ProcessSupervisor
from nailong_agent_sdk.tools.tools import ToolInvocationContext


def all_capabilities():
    return sorted({tool.capability for tool in HarnessToolRegistry.default_tools()})


_DEFS = {definition.name: definition for definition in core_tool_definitions()}


def tool_def(name):
    if name in _DEFS:
        return _DEFS[name]
    return ToolDefinition(
        name=name,
        description="d",
        input_schema={"type": "object"},
        episode_kind=EpisodeKind.EXPLORATORY,
    )


def invocation(name, args, call_id="c1"):
    task = ScopedAgentTask(
        id="T1",
        input={},
        scope=TaskScope(label="s"),
        locked_interface={},
        instructions="i",
        acceptance_criteria=["c"],
    )
    return ToolInvocationContext(
        agent_identity="a",
        task=task,
        iteration=1,
        call=ToolCall(id=call_id, name=name, arguments=args),
    )


def build(
    tmp_path,
    *,
    grants=None,
    role="worker",
    declared=(),
    authorized=(),
    templates=(),
    journal=None,
    approval_ids=None,
    approvals=None,
    search_client=None,
    ask_human=None,
    scope="REQ-1",
    spec_snapshots=None,
    allowed_paths=None,
    registry=None,
    approve=("draft.write",),
):
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactStore(run_root)
    if grants is None:
        grants = [
            CapabilityGrant(
                role=role,
                capabilities=all_capabilities(),
                allowed_paths=allowed_paths if allowed_paths is not None else ["."],
            )
        ]
    approvals = approvals or ApprovalRegistry()
    approval_ids = dict(approval_ids or {})
    for capability in approve:
        granted = approvals.request("run-1", "node-1", capability, "auto-approved by test")
        approvals.submit(granted.approval_id, True, "auto")
        approval_ids[capability] = granted.approval_id
    context = HarnessExecutionContext(
        run_id="run-1",
        node_id="node-1",
        role=role,
        plan_task=PlanTask(
            task_id="T1",
            scope=scope,
            locked_interface={},
            instructions="i",
            acceptance_criteria="c",
            model_tier=ModelTier.CHEAP,
            authorized_artifact_ids=list(authorized),
        ),
        run_root=run_root,
        artifacts=artifacts,
        policy=CapabilityPolicy(grants),
        approvals=approvals,
        supervisor=ProcessSupervisor(list(templates)),
        spec_snapshots=spec_snapshots or {},
        declared_output_paths=tuple(declared),
        approval_ids_by_capability=approval_ids,
        result_journal=journal,
        search_client=search_client,
        ask_human=ask_human,
    )
    return HarnessToolExecutor(registry or HarnessToolRegistry(), context), context


async def call(executor, name, **args):
    return await executor.execute(tool_def(name), invocation(name, args))


def run(coro, timeout=90):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


def mkjunction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
