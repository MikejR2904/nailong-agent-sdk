from nailong_agent_sdk.tools.core import core_tool_definitions
from tests.support.agents import definition, final, task

KEY = "sk-live-0123456789abcdef"
BUILTIN = {item.name: item for item in core_tool_definitions()}


def live_options(base_url, **changes):
    endpoint = {"base_url": base_url, "api_key": KEY, "allow_insecure_http": True}
    return {"model_endpoint": endpoint, **changes}


def plan_payload():
    from tests.support.plans import simple_plan

    return simple_plan().model_dump(mode="json")


def dispatched_controller(server, snapshot_id, *, depth, nodes=2):
    plan = {**plan_payload(), "max_elastic_depth": depth, "max_elastic_nodes": nodes}
    controller = server.call(
        "create_controller",
        {
            "snapshot": {"snapshot_id": snapshot_id, "version": "1", "content_hash": "h"},
            "profile": {"stage": "design", "source_snapshot_id": snapshot_id},
            "routing_rules": {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5},
            "gap_metadata": {},
            "max_repair_attempts": 1,
        },
    )
    cid = controller["controller"]["controller_id"]
    server.call("submit_controller_plan", {"controller_id": cid, "plan": plan})
    server.call("approve_controller_plan", {"controller_id": cid, "approved": True})
    assert server.call("dispatch_controller", {"controller_id": cid})["ok"]
    return cid


def scripted_run(instance, task_id):
    return instance.call(
        "run_agent_task",
        {
            "definition": definition().model_dump(mode="json"),
            "task": task(task_id).model_dump(mode="json"),
            "runtime_options": {"scripted_turns": [final()]},
        },
    )
