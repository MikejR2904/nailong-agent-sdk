import asyncio
import importlib.util
from pathlib import Path

import pytest

from nailong_agent_sdk import AgentRuntimeServices, Orchestrator
from nailong_agent_sdk.agent.orchestrator.models import OrchestrationStatus
from tests.support.paths import REPO_ROOT


def load(name: str):
    path = REPO_ROOT / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_supervisor_delegates_to_specialists_and_answers_once(tmp_path):
    example = load("multi_agent_starter")
    text = asyncio.run(example.supervisor_with_specialists(tmp_path))
    assert text == "The part is rated for 85 C (supported; one source is a vendor datasheet)."


def test_fan_out_runs_every_worker_and_the_editor_sees_all_notes(tmp_path):
    example = load("multi_agent_starter")
    brief = asyncio.run(example.fan_out_then_combine(tmp_path))
    assert brief.startswith("Pricing, security and limits are covered")


def test_the_writer_runs_after_both_researchers_and_receives_their_outputs(tmp_path):
    example = load("multi_agent_pipeline")
    result = asyncio.run(example.run(tmp_path / "team"))
    assert result["status"] == "executed" and result["architecture"] == "multi-agent"
    assert set(result["node_statuses"].values()) == {"completed"}
    assert set(result["writer_received"]) == {"node:research-pricing", "node:research-security"}
    assert result["writer_received"]["node:research-pricing"]["summary"].startswith("Per-seat")


def test_a_finished_orchestration_can_be_reopened_but_only_an_interrupted_one_recovers(tmp_path):
    example = load("multi_agent_pipeline")
    root = tmp_path / "team"
    result = asyncio.run(example.run(root))
    reopened = Orchestrator.resume(root, result["orchestration_id"])
    record = reopened.get(result["orchestration_id"])
    assert record.status is OrchestrationStatus.EXECUTED and record.graph_run_id == "run-1"
    with pytest.raises(ValueError, match="Only a dispatched orchestration can recover execution"):
        asyncio.run(
            reopened.recover_execution(
                result["orchestration_id"],
                AgentRuntimeServices.open(Path(root)),
                example.make_binding_factory({}),
            )
        )


def test_the_planner_example_suggests_a_team_and_runs_it(tmp_path):
    example = load("multi_agent_planner")
    result = asyncio.run(example.run(tmp_path))
    assert result["suggested"] == "team" and result["ran"] == "team" and result["ok"]
    assert result["order"][-1] == "brief" and result["text"].startswith("Brief:")
    assert "2 roles are involved (researcher, writer)" in result["reasons"][0]


def test_the_planner_example_can_keep_everything_in_one_agent(tmp_path):
    example = load("multi_agent_planner")
    result = asyncio.run(example.run(tmp_path, keep_in_one_agent=True))
    assert result["suggested"] == "team" and result["ran"] == "single"
    assert result["text"] == "Brief: A is cheaper, B has SOC 2."
