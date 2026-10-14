import asyncio
from datetime import UTC, datetime, timedelta

from nailong_agent_sdk.mcp.authority import (
    McpAuthority,
    McpAuthorityPolicy,
    McpPrincipal,
    authenticated_as,
)
from nailong_agent_sdk.mcp.server import create_mcp_server
from tests.support.plans import simple_plan

HOST_SECRET = "h" * 32
APPROVER_SECRET = "a" * 32
DECISIONS = "mcp-decisions"
SNAPSHOT = {"version": "1", "content_hash": "h"}
RULES = {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5}


def stamp(seconds_ago=0.0):
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def plan_payload(**changes):
    return {**simple_plan().model_dump(mode="json"), **changes}


class Harness:
    def __init__(self, tmp_path, **policy):
        settings = {"approver_configured": True, "require_principal": True, **policy}
        self.run_root = tmp_path / "run"
        self.server = create_mcp_server(self.run_root, authority=McpAuthorityPolicy(**settings))
        self.host = McpPrincipal.for_secret(McpAuthority.HOST, HOST_SECRET)
        self.approver = McpPrincipal.for_secret(McpAuthority.APPROVER, APPROVER_SECRET)

    def call(self, name, arguments, principal=None):
        async def invoke():
            with authenticated_as(principal):
                return await self.server.call_tool(name, arguments)

        return asyncio.run(invoke()).structured_content

    def as_host(self, name, arguments):
        return self.call(name, arguments, self.host)

    def as_approver(self, name, arguments):
        return self.call(name, arguments, self.approver)

    def controller(self, snapshot_id, plan=None, *, submit=True, **options):
        created = self.as_host(
            "create_controller",
            {
                "snapshot": {"snapshot_id": snapshot_id, **SNAPSHOT},
                "profile": {"stage": "design", "source_snapshot_id": snapshot_id},
                "routing_rules": RULES,
                "gap_metadata": {},
                "max_repair_attempts": 1,
                **options,
            },
        )
        assert created["ok"], created
        controller_id = created["controller"]["controller_id"]
        if submit:
            submitted = self.as_host(
                "submit_controller_plan",
                {"controller_id": controller_id, "plan": plan or plan_payload()},
            )
            assert submitted["ok"], submitted
        return controller_id

    def plan_hash(self, controller_id):
        state = self.as_approver("get_controller_state", {"controller_id": controller_id})
        return state["binding"]["plan_hash"]

    def phase(self, controller_id):
        state = self.as_host("get_controller_state", {"controller_id": controller_id})
        return state["controller"]["phase"]

    def decisions(self):
        events = self.as_approver("get_telemetry_events", {"run_id": DECISIONS, "limit": 1000})
        return events["events"] if events["ok"] else []

    def decided(self, controller_id, **overrides):
        return {
            "controller_id": controller_id,
            "approved": True,
            "plan_hash": self.plan_hash(controller_id),
            "issued_at_utc": stamp(),
            **overrides,
        }


def error_of(result):
    assert result["ok"] is False, result
    return result["errors"][0]
