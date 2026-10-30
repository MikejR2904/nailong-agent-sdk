import asyncio
import json
import re

import pytest

from nailong_agent_sdk.mcp.authority import (
    ApprovalsDisabled,
    McpAuthority,
    McpAuthorityPolicy,
    McpOperation,
    McpPrincipal,
    OperationNotPermitted,
    Unauthenticated,
    current_principal,
)
from nailong_agent_sdk.mcp.security import (
    BearerTokenMiddleware,
    ServerConfigurationError,
    load_http_service_settings,
)
from tests.support.mcp_authority import (
    APPROVER_SECRET,
    DECISIONS,
    HOST_SECRET,
    RULES,
    SNAPSHOT,
    error_of,
    plan_payload,
    stamp,
)
from tests.support.mcp_server import Server


@pytest.mark.parametrize(
    ("policy", "principal", "operation", "expected"),
    [
        ({}, None, McpOperation.APPROVAL, None),
        ({"require_principal": True}, None, McpOperation.READ, Unauthenticated),
        ({"approver_configured": True}, McpAuthority.HOST, McpOperation.READ, None),
        ({"approver_configured": True}, McpAuthority.HOST, McpOperation.HOST, None),
        (
            {"approver_configured": True},
            McpAuthority.HOST,
            McpOperation.APPROVAL,
            OperationNotPermitted,
        ),
        ({"approver_configured": True}, McpAuthority.APPROVER, McpOperation.READ, None),
        ({"approver_configured": True}, McpAuthority.APPROVER, McpOperation.APPROVAL, None),
        (
            {"approver_configured": True},
            McpAuthority.APPROVER,
            McpOperation.HOST,
            OperationNotPermitted,
        ),
        ({}, McpAuthority.HOST, McpOperation.APPROVAL, ApprovalsDisabled),
        ({"allow_host_approvals": True}, McpAuthority.HOST, McpOperation.APPROVAL, None),
        ({"allow_host_approvals": True}, McpAuthority.HOST, McpOperation.HOST, None),
    ],
)
def test_the_policy_matrix(policy, principal, operation, expected):
    subject = McpAuthorityPolicy(**policy)
    presented = None if principal is None else McpPrincipal.for_secret(principal, "s" * 32)
    if expected is Unauthenticated:
        with pytest.raises(Unauthenticated):
            subject.resolve(presented, "tool")
        return
    resolved = subject.resolve(presented, "tool")
    if expected is None:
        subject.check(resolved, operation, "tool")
    else:
        with pytest.raises(expected):
            subject.check(resolved, operation, "tool")


def test_a_policy_cannot_name_both_a_separate_approver_and_host_approvals():
    with pytest.raises(ValueError, match="mutually exclusive"):
        McpAuthorityPolicy(approver_configured=True, allow_host_approvals=True)
    with pytest.raises(ValueError, match="approval_max_age_seconds"):
        McpAuthorityPolicy(approval_max_age_seconds=0)
    with pytest.raises(ValueError, match="clock_skew_seconds"):
        McpAuthorityPolicy(clock_skew_seconds=-1)


def test_a_principal_id_names_the_credential_without_revealing_it():
    one = McpPrincipal.for_secret(McpAuthority.APPROVER, APPROVER_SECRET)
    again = McpPrincipal.for_secret(McpAuthority.APPROVER, APPROVER_SECRET)
    other = McpPrincipal.for_secret(McpAuthority.APPROVER, "b" * 32)
    assert one == again and one.principal_id != other.principal_id
    assert re.fullmatch(r"approver-[0-9a-f]{12}", one.principal_id)
    assert APPROVER_SECRET not in repr(one)


def environment(**overrides):
    return {"AGENT_RUNTIME_AUTH_TOKEN": HOST_SECRET, **overrides}


def test_the_service_settings_carry_the_approver_credential_and_its_policy():
    plain = load_http_service_settings(environment())
    assert plain.approver_token is None and plain.allow_host_approvals is False
    assert "AGENT_RUNTIME_APPROVER_TOKEN" in plain.approval_notice()
    policy = plain.authority_policy()
    assert policy.require_principal and not policy.approver_configured
    separated = load_http_service_settings(
        environment(
            AGENT_RUNTIME_APPROVER_TOKEN=APPROVER_SECRET,
            AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS="45",
        )
    )
    assert separated.approver_token == APPROVER_SECRET and separated.approval_notice() is None
    assert APPROVER_SECRET not in repr(separated)
    assert separated.authority_policy().approver_configured
    assert separated.authority_policy().approval_max_age_seconds == 45.0
    single = load_http_service_settings(environment(AGENT_RUNTIME_ALLOW_HOST_APPROVALS="1"))
    assert single.allow_host_approvals and "also decides approvals" in single.approval_notice()


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            {"AGENT_RUNTIME_APPROVER_TOKEN": "short"},
            "AGENT_RUNTIME_APPROVER_TOKEN must be a secret",
        ),
        (
            {"AGENT_RUNTIME_APPROVER_TOKEN": "a" * 16 + " " + "a" * 16},
            "AGENT_RUNTIME_APPROVER_TOKEN must be a secret",
        ),
        (
            {"AGENT_RUNTIME_APPROVER_TOKEN": HOST_SECRET},
            "AGENT_RUNTIME_APPROVER_TOKEN must differ from AGENT_RUNTIME_AUTH_TOKEN",
        ),
        (
            {
                "AGENT_RUNTIME_APPROVER_TOKEN": APPROVER_SECRET,
                "AGENT_RUNTIME_ALLOW_HOST_APPROVALS": "1",
            },
            "AGENT_RUNTIME_ALLOW_HOST_APPROVALS=1 contradicts AGENT_RUNTIME_APPROVER_TOKEN",
        ),
        (
            {"AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS": "soon"},
            "AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS must be a number of seconds above 0 "
            'and at most 86400, got "soon"',
        ),
        (
            {"AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS": "0"},
            "AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS must be a number of seconds",
        ),
    ],
)
def test_a_bad_approver_setting_is_refused_naming_the_variable(extra, expected):
    with pytest.raises(ServerConfigurationError) as raised:
        load_http_service_settings(environment(**extra))
    assert expected in str(raised.value)
    assert APPROVER_SECRET not in str(raised.value) and HOST_SECRET not in str(raised.value)


async def _probe(token_header):
    seen = []

    async def app(scope, receive, send):
        seen.append(current_principal())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BearerTokenMiddleware(app, token=HOST_SECRET, approver_token=APPROVER_SECRET)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": token_header}
    await middleware(scope, receive, send)
    status = next(m for m in sent if m["type"] == "http.response.start")["status"]
    return status, seen, current_principal()


def test_the_middleware_resolves_the_principal_from_the_presented_token():
    def headers(token):
        return [(b"authorization", f"Bearer {token}".encode())]

    status, seen, after = asyncio.run(_probe(headers(HOST_SECRET)))
    assert status == 200 and seen[0].authority is McpAuthority.HOST and after is None
    status, seen, after = asyncio.run(_probe(headers(APPROVER_SECRET)))
    assert status == 200 and seen[0].authority is McpAuthority.APPROVER and after is None
    status, seen, _ = asyncio.run(_probe(headers("c" * 32)))
    assert status == 401 and seen == []
    status, seen, _ = asyncio.run(_probe([(b"x-agent-authority", b"approver")]))
    assert status == 401 and seen == []


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("authority"), approver=True)
    yield instance
    instance.stop()


def wire_controller(service, snapshot_id):
    created = service.call(
        "create_controller",
        {
            "snapshot": {"snapshot_id": snapshot_id, **SNAPSHOT},
            "profile": {"stage": "design", "source_snapshot_id": snapshot_id},
            "routing_rules": RULES,
            "gap_metadata": {},
        },
    )
    controller_id = created["controller"]["controller_id"]
    submitted = service.call(
        "submit_controller_plan", {"controller_id": controller_id, "plan": plan_payload()}
    )
    assert submitted["ok"], submitted
    return controller_id


def test_over_the_wire_the_bearer_token_decides_who_may_approve(service):
    controller_id = wire_controller(service, "snap-wire-authority")
    refused = service.call(
        "approve_controller_plan", {"controller_id": controller_id, "approved": True}
    )
    assert error_of(refused)["type"] == "OperationNotPermitted"
    viewed = service.as_approver("get_controller_state", {"controller_id": controller_id})
    assert viewed["controller"]["phase"] == "awaiting-plan-approval"
    decided = service.as_approver(
        "approve_controller_plan",
        {
            "controller_id": controller_id,
            "approved": True,
            "plan_hash": viewed["binding"]["plan_hash"],
            "issued_at_utc": stamp(),
            "approver_label": "wire-reviewer",
        },
    )
    assert decided["ok"] and decided["controller"]["phase"] == "dispatch-ready"
    assert decided["decision_record"]["authority"] == "approver"
    events = service.as_approver("get_telemetry_events", {"run_id": DECISIONS, "limit": 1000})
    types = [e["event_type"] for e in events["events"]]
    assert "mcp.authority.refused" in types and "mcp.decision.applied" in types
    assert service.approver_token not in json.dumps(events)
    assert service.token not in json.dumps(events)
    assert events["integrity_chain_valid"] is True


def test_over_the_wire_headers_a_caller_invents_do_not_make_the_host_an_approver(service):
    controller_id = wire_controller(service, "snap-wire-forged")
    forged = {
        **service.auth_headers,
        "X-Agent-Authority": "approver",
        "X-Principal": "approver",
        "X-Approver": "true",
        "X-Forwarded-User": "designer",
    }
    refused = service.call(
        "approve_controller_plan",
        {"controller_id": controller_id, "approved": True, "approver_label": "designer"},
        headers=forged,
    )
    assert error_of(refused)["type"] == "OperationNotPermitted"
    state = service.call("get_controller_state", {"controller_id": controller_id})
    assert state["controller"]["phase"] == "awaiting-plan-approval"


def test_over_the_wire_the_approver_cannot_start_work_or_prune(service):
    for name, sent in (
        ("start_run", {"plan": plan_payload()}),
        ("prune_runs", {"older_than_seconds": 1, "dry_run": False}),
    ):
        error = error_of(service.as_approver(name, sent))
        assert error["type"] == "OperationNotPermitted"


def test_a_service_without_an_approver_credential_says_so_at_start_and_refuses(tmp_path):
    instance = Server(tmp_path)
    try:
        error = error_of(
            instance.call("approve_controller_plan", {"controller_id": "c", "approved": True})
        )
        assert error["type"] == "ApprovalsDisabled"
        assert "AGENT_RUNTIME_APPROVER_TOKEN" in error["message"]
        read = instance.call("list_telemetry_runs")
        assert read["ok"] is True
    finally:
        instance.stop()
    log = instance.log_path.read_text("utf-8")
    assert "approval operations are disabled" in log and "AGENT_RUNTIME_APPROVER_TOKEN" in log
