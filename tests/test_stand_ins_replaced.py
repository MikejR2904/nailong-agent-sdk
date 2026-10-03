"""Former stand-ins now behave as production services."""

from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path

import pytest

from nailong_agent_sdk import (
    ModelBinding,
    physical_design_worker_definition,
    synthesis_worker_definition,
    timing_signoff_worker_definition,
)
from nailong_agent_sdk.specifications.documents import DocumentNodeKind, VisionStatus
from nailong_agent_sdk.specifications.retrieval import InMemoryRetrievalCache
from nailong_agent_sdk.specifications.retrieval_models import RetrievalResult, RetrievalStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import ModelTier, Plan, PlanTask
from nailong_agent_sdk.tools.approvals import ApprovalRegistry, ApprovalStatus


def _request_in_child(path: str, queue) -> None:
    registry = ApprovalRegistry(Path(path))
    queue.put(registry.request("run-x", "node", "draft.write", "child asks").approval_id)


def test_approvals_are_durable_across_instances_and_processes(tmp_path):
    path = tmp_path / "approvals.json"
    first = ApprovalRegistry(path)
    a1 = first.request("run-1", "n1", "draft.write", "needs write")
    queue = multiprocessing.get_context("spawn").Queue()
    child = multiprocessing.get_context("spawn").Process(
        target=_request_in_child, args=(str(path), queue)
    )
    child.start()
    child.join(30)
    a2 = queue.get(timeout=5)
    assert {a1.approval_id, a2} == {"approval-1", "approval-2"}

    second = ApprovalRegistry(path)  # e.g. after a restart
    decided = second.submit(a1.approval_id, True, "operator ok")
    assert decided.status is ApprovalStatus.APPROVED
    assert first.get(a1.approval_id).status is ApprovalStatus.APPROVED  # seen by the other
    with pytest.raises(ValueError, match="already has a decision"):
        first.submit(a1.approval_id, False)


def test_approval_listing_orders_numerically(tmp_path):
    registry = ApprovalRegistry(tmp_path / "a.json")
    for _ in range(11):
        registry.request("r", "n", "c", "x")
    ids = [item.approval_id for item in registry.list()]
    assert ids[-2:] == ["approval-10", "approval-11"]


def _plan() -> Plan:
    return Plan(
        plan_id="p1",
        tasks=[
            PlanTask(
                task_id="t1",
                scope="s",
                locked_interface={},
                instructions="i",
                acceptance_criteria="a",
                model_tier=ModelTier.STANDARD,
            )
        ],
    )


def test_coordinator_approvals_survive_a_restart(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(_plan())
    request = coordinator.approvals(run.run_id).request(run.run_id, "node:t1", "rtl.yosys", "x")

    restarted = HarnessCoordinator(tmp_path)
    decided = restarted.submit_approval(run.run_id, request.approval_id, True, "approved later")
    assert decided.status is ApprovalStatus.APPROVED
    assert HarnessCoordinator(tmp_path).approvals(run.run_id).get(request.approval_id).status == (
        ApprovalStatus.APPROVED
    )


def test_images_without_a_vision_service_go_to_review(tmp_path):
    from nailong_agent_sdk.specifications.documents import (
        DocumentFormat,
        DocumentNode,
        DocumentTree,
        SourceRef,
        SpecificationCategory,
    )
    from nailong_agent_sdk.specifications.preprocessing import SpecificationPreprocessor

    source = SourceRef(
        document_id="d",
        relative_path="spec.md",
        source_hash="h",
        format=DocumentFormat.MD,
        location="line 1",
    )
    tree = DocumentTree(
        document_id="d",
        category=SpecificationCategory.ARCHITECTURAL,
        title="Spec",
        format=DocumentFormat.MD,
        relative_path="spec.md",
        source_hash="h",
        nodes=[
            DocumentNode(node_id="n1", kind=DocumentNodeKind.TEXT, source=source, content="x"),
            DocumentNode(
                node_id="n2",
                kind=DocumentNodeKind.IMAGE,
                source=source,
                content={"path": "diagram.png"},
                vision_status=VisionStatus.PENDING,
            ),
        ],
    )
    resolved = asyncio.run(SpecificationPreprocessor(tmp_path).resolve_images(tree))
    assert [node.vision_status for node in resolved.nodes] == [
        VisionStatus.NOT_REQUIRED,
        VisionStatus.REVIEW_REQUIRED,
    ]


def test_retrieval_cache_expires_and_bounds_entries():
    now = [100.0]
    cache = InMemoryRetrievalCache(max_entries=2, clock=lambda: now[0])
    result = RetrievalResult(status=RetrievalStatus.RETRIEVED, query_digest="d", backend="b")
    cache.set("a", result, ttl_seconds=10)
    assert cache.get("a") is result
    now[0] = 110.0
    assert cache.get("a") is None  # expired exactly at its deadline
    cache.set("a", result, 60)
    cache.set("b", result, 60)
    cache.get("a")  # "a" is now most recently used
    cache.set("c", result, 60)
    assert cache.get("b") is None and cache.get("a") is result and cache.get("c") is result


@pytest.mark.parametrize(
    "factory, process_tool",
    [
        (synthesis_worker_definition, "run_yosys"),
        (physical_design_worker_definition, "run_openroad"),
        (timing_signoff_worker_definition, "run_opensta"),
    ],
)
def test_stage_workers_have_real_tool_contracts(factory, process_tool):
    from nailong_agent_sdk.tools.registry import HarnessToolRegistry

    definition = factory(ModelBinding(provider="p", model="m"))
    registry = HarnessToolRegistry()
    assert all(registry.resolve(tool.name) for tool in definition.tools)
    assert process_tool in {tool.name for tool in definition.tools}
    assert definition.verification_gate_id == "status-is-complete"
    assert definition.output_schema["properties"]["status"] == {"const": "complete"}
