import json
import os
import sys

import pytest

from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy, SideEffectClass
from nailong_agent_sdk.tools.registry import HarnessToolRegistry, RegisteredTool
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.tools import build, call, run


def test_registry_and_definitions_are_consistent():
    registry_names = set(HarnessToolRegistry().names())
    core_names = {definition.name for definition in core_tool_definitions()}
    assert core_names <= registry_names, core_names - registry_names
    extras = registry_names - core_names
    assert extras == {"read_spec"}
    for definition in core_tool_definitions():
        assert definition.input_schema["type"] == "object"


def test_duplicate_and_unknown_handler_registry_errors():
    tools = [RegisteredTool("a", "x", SideEffectClass.READ_ONLY)] * 2
    with pytest.raises(ValueError, match="unique"):
        HarnessToolRegistry(tools)
    with pytest.raises(ValueError, match="Custom handlers require registered tools"):
        HarnessToolRegistry([], custom_handlers={"zzz": None})


def test_role_without_grant_is_blocked_with_named_role_and_capability(tmp_path):
    executor, _ = build(tmp_path, grants=[CapabilityGrant(role="someone-else", capabilities=[])])
    result = run(call(executor, "brief", text="hello"))
    assert result.status == "blocked"
    assert 'Role "worker"' in result.error and '"utility.brief"' in result.error


def test_unknown_registered_tool_fails_with_name(tmp_path):
    executor, _ = build(tmp_path)
    result = run(call(executor, "does_not_exist"))
    assert result.status == "failed"
    assert '"does_not_exist"' in result.error


def test_sensitive_requested_path_denied_before_grant(tmp_path):
    executor, _ = build(tmp_path, declared=(".ssh/id_rsa",))
    result = run(call(executor, "write_draft", path=".ssh/id_rsa", content="x"))
    assert result.status == "blocked"
    assert "sensitive-path pattern" in result.error


def test_sensitive_credential_files_are_denied(tmp_path):
    executor, ctx = build(tmp_path)
    for relative in (
        ".ssh/id_rsa",
        ".aws/credentials",
        ".netrc",
        ".docker/config.json",
        "sub/id_ed25519",
        ".kube/config",
    ):
        target = ctx.run_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("TOP-SECRET-CRED", "utf-8")
        result = run(call(executor, "read_file", path=relative))
        assert result.status == "failed", relative
        assert "denied credential path" in result.error, relative
    globbed = run(call(executor, "glob", pattern="**/*"))
    assert not any(
        name in match.replace("\\", "/")
        for match in globbed.output["matches"]
        for name in (".ssh/id_rsa", ".aws/credentials", ".netrc", "id_ed25519")
    )
    grepped = run(call(executor, "grep", pattern="TOP-SECRET-CRED"))
    assert grepped.output["matches"] == []


@pytest.mark.skipif(os.name != "nt", reason="NTFS-specific name aliases")
@pytest.mark.parametrize("alias", [".netrc::$DATA", ".netrc.", ".netrc ", ".NETRC", ".netrc..."])
def test_sensitive_file_aliases_on_ntfs_do_not_bypass_denylist(tmp_path, alias):
    executor, ctx = build(tmp_path)
    (ctx.run_root / ".netrc").write_text("NTFS-ALIAS-SECRET", "utf-8")
    result = run(call(executor, "read_file", path=alias))
    blob = json.dumps(result.model_dump(mode="json"))
    assert "NTFS-ALIAS-SECRET" not in blob


def test_observation_credential_like_files_outside_denylist(tmp_path):
    executor, ctx = build(tmp_path)
    readable = []
    for name in (".env", "server.pem", "id_ecdsa", ".npmrc", ".git-credentials", ".pypirc"):
        (ctx.run_root / name).write_text("CRED-LIKE", "utf-8")
        result = run(call(executor, "read_file", path=name))
        if result.status == "succeeded":
            readable.append(name)
    assert readable == [], f"credential-like files readable via read_file: {readable}"


def test_read_scope_allowed_paths_apply_to_read_tools(tmp_path):
    executor, ctx = build(tmp_path, allowed_paths=["docs"])
    (ctx.run_root / "docs").mkdir()
    (ctx.run_root / "docs" / "ok.txt").write_text("ok", "utf-8")
    (ctx.run_root / "private").mkdir()
    (ctx.run_root / "private" / "no.txt").write_text("OUT-OF-SCOPE", "utf-8")
    inside = run(call(executor, "read_file", path="docs/ok.txt"))
    outside = run(call(executor, "read_file", path="private/no.txt"))
    assert inside.status == "succeeded"
    assert outside.status != "succeeded", (
        "allowed_paths=['docs'] did not stop read_file('private/no.txt')"
    )


def test_approval_flow_and_blocked_call_dedupe(tmp_path):
    template = CommandTemplate(
        name="hello", command=[sys.executable, "-c", "print('hi')"], timeout_seconds=30
    )
    executor, ctx = build(tmp_path, templates=[template])
    first = run(call(executor, "run_registered_command", template_name="hello"))
    assert first.status == "blocked" and first.output["capability"] == "process.execute"
    second = run(call(executor, "run_registered_command", template_name="hello"))
    third = run(call(executor, "run_registered_command", template_name="hello"))
    pending = [
        request
        for request in ctx.approvals.list()
        if request.capability == "process.execute" and request.status.value == "pending"
    ]
    assert len(pending) == 1, (
        f"{len(pending)} pending approvals created for 3 identical blocked calls"
    )
    assert second.status == "blocked" and third.status == "blocked"


def test_approval_granted_and_rejected_paths(tmp_path):
    template = CommandTemplate(
        name="hello", command=[sys.executable, "-c", "print('hi')"], timeout_seconds=30
    )
    executor, ctx = build(tmp_path, templates=[template])
    blocked = run(call(executor, "run_registered_command", template_name="hello"))
    approval_id = blocked.output["approval_id"]
    pending = run(call(executor, "run_registered_command", template_name="hello"))
    assert pending.status == "blocked"
    ctx.approvals.submit(approval_id, True, "ok")
    ctx.approval_ids_by_capability["process.execute"] = approval_id
    allowed = run(call(executor, "run_registered_command", template_name="hello"))
    assert allowed.status == "succeeded" and allowed.output["output"].strip() == "hi"
    with pytest.raises(ValueError, match='already has the decision "approved"'):
        ctx.approvals.submit(approval_id, False)
    other = ctx.approvals.request("run-1", "node-1", "process.execute", "second")
    ctx.approvals.submit(other.approval_id, False, "nope")
    ctx.approval_ids_by_capability["process.execute"] = other.approval_id
    rejected = run(call(executor, "run_registered_command", template_name="hello"))
    assert rejected.status == "blocked" and rejected.error == "nope"
    mismatched = ctx.approvals.request("run-1", "node-1", "other.capability", "x")
    ctx.approvals.submit(mismatched.approval_id, True)
    ctx.approval_ids_by_capability["process.execute"] = mismatched.approval_id
    wrong = run(call(executor, "run_registered_command", template_name="hello"))
    assert wrong.status == "blocked" and "does not match" in wrong.error


def test_read_spec_scope_enforcement(tmp_path):
    executor, _ = build(tmp_path, scope="REQ-1", spec_snapshots={"REQ-1": "Spec text"})
    ok = run(call(executor, "read_spec", pointer="REQ-1"))
    assert ok.output == {"pointer": "REQ-1", "content": "Spec text"}
    foreign = run(call(executor, "read_spec", pointer="REQ-2"))
    assert foreign.status == "failed" and "outside the scoped PlanTask" in foreign.error
    unavailable, _ = build(tmp_path / "u", scope="REQ-1", spec_snapshots={})
    missing = run(call(unavailable, "read_spec", pointer="REQ-1"))
    assert missing.status == "failed" and "unavailable" in missing.error


def test_capability_policy_unit_matrix(tmp_path):
    policy = CapabilityPolicy(
        [
            CapabilityGrant(role="r", capabilities=["draft.write"], allowed_paths=["out"]),
            CapabilityGrant(role="open", capabilities=["filesystem.read"], allowed_paths=[]),
        ]
    )
    root = tmp_path
    decision = policy.evaluate(
        role="r",
        capability="draft.write",
        side_effect=SideEffectClass.MUTATING,
        run_root=root,
        requested_paths=["out/a.txt"],
    )
    assert decision.allowed is False and decision.approval_required is True
    outside = policy.evaluate(
        role="r",
        capability="draft.write",
        side_effect=SideEffectClass.MUTATING,
        run_root=root,
        requested_paths=["elsewhere/a.txt"],
    )
    assert outside.allowed is False and "outside the declared capability scope" in outside.reason
    traversal = policy.evaluate(
        role="r",
        capability="draft.write",
        side_effect=SideEffectClass.MUTATING,
        run_root=root,
        requested_paths=["out/../../x"],
    )
    assert traversal.allowed is False
    sibling = policy.evaluate(
        role="r",
        capability="draft.write",
        side_effect=SideEffectClass.MUTATING,
        run_root=root,
        requested_paths=["out_evil/a.txt"],
    )
    assert sibling.allowed is False
    empty_scope = policy.evaluate(
        role="open",
        capability="filesystem.read",
        side_effect=SideEffectClass.READ_ONLY,
        run_root=root,
        requested_paths=["x"],
    )
    assert empty_scope.allowed is False
    nopath = policy.evaluate(
        role="open",
        capability="filesystem.read",
        side_effect=SideEffectClass.READ_ONLY,
        run_root=root,
    )
    assert nopath.allowed is True


def test_filesystem_read_reaches_unauthorized_artifact_blobs_and_journal(tmp_path):
    (tmp_path / "run").mkdir()
    journal = FileToolResultJournal(tmp_path / "run")
    executor, ctx = build(tmp_path, allowed_paths=["docs"], journal=journal)
    base = ctx.artifacts.write_text("docs/base.txt", "base\n")
    secret = ctx.artifacts.write_text(
        "other/secret.txt", "TOP-SECRET-ARTIFACT\n", manifest={"run_id": "other-run"}
    )
    ctx.plan_task.authorized_artifact_ids.append(base.artifact_id)
    run(call(executor, "glob", pattern=".agent-artifacts/content/*"))
    blob = run(call(executor, "read_file", path=f".agent-artifacts/content/{secret.sha256}"))
    working = run(call(executor, "read_file", path="other/secret.txt"))
    run(call(executor, "grep", pattern="TOP-SECRET-ARTIFACT"))
    assert blob.status != "succeeded" and working.status != "succeeded"


def test_internal_run_state_is_not_readable_through_file_tools(tmp_path):
    executor, ctx = build(tmp_path)
    record = ctx.artifacts.write_text("docs/a.txt", "SECRET-BLOB\n")
    blob = run(call(executor, "read_file", path=f".agent-artifacts/content/{record.sha256}"))
    listed = run(call(executor, "glob", pattern=".agent-artifacts/**/*"))
    grepped = run(call(executor, "grep", pattern="SECRET-BLOB"))
    assert blob.status == "failed" and "SDK-internal run state" in blob.error
    assert listed.output["matches"] == []
    paths = [match["path"].replace("\\", "/") for match in grepped.output["matches"]]
    assert paths == ["docs/a.txt"]


@pytest.mark.parametrize("name", [".ENV", ".Env.local", "Server.PEM", "ID_ECDSA", ".NpmRc"])
def test_credential_like_file_names_are_denied_in_any_letter_case(tmp_path, name):
    executor, ctx = build(tmp_path)
    (ctx.run_root / name).write_text("CRED-LIKE", "utf-8")
    result = run(call(executor, "read_file", path=name))
    assert result.status == "failed" and "denied credential path" in result.error


def test_the_read_scope_of_a_role_without_filesystem_read_is_empty():
    policy = CapabilityPolicy(
        [
            CapabilityGrant(role="a", capabilities=["filesystem.read"], allowed_paths=["docs"]),
            CapabilityGrant(role="b", capabilities=["draft.write"], allowed_paths=["docs"]),
        ]
    )
    assert policy.read_scope("a") == ("docs",)
    assert policy.read_scope("b") == () and policy.read_scope("unknown") == ()
