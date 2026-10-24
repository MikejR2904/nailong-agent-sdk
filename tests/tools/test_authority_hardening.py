import asyncio

from nailong_agent_sdk.tools.approvals import ApprovalRegistry, ApprovalStatus
from tests.support.tools import build, call, invocation, run, tool_def


def written(context):
    return len(context.artifacts.history("out.txt")) if context.artifacts.paths() else 0


def test_an_approval_of_another_run_or_node_never_authorizes_a_call(tmp_path):
    registry = ApprovalRegistry()
    other = registry.request("other-run", "other-node", "draft.write", "x")
    registry.submit(other.approval_id, True, "ok")
    executor, context = build(
        tmp_path,
        declared=("out.txt",),
        approve=(),
        approvals=registry,
        approval_ids={"draft.write": other.approval_id},
    )
    result = run(call(executor, "write_draft", path="out.txt", content="x"))
    assert result.status == "blocked" and result.failure.code == "TOOL_DENIED_BY_POLICY"
    assert 'belongs to run "other-run" node "other-node"' in result.error
    assert written(context) == 0


def test_a_revoked_approval_stops_the_next_call_and_names_the_revocation(tmp_path):
    executor, context = build(tmp_path, declared=("out.txt",))
    assert run(call(executor, "write_draft", path="out.txt", content="1")).status == "succeeded"
    approval_id = context.approval_ids_by_capability["draft.write"]
    context.approvals.revoke(approval_id, "operator withdrew it")
    result = run(call(executor, "write_draft", path="out.txt", content="2"))
    assert result.status == "blocked" and result.error == "operator withdrew it"
    assert written(context) == 1
    assert context.approvals.get(approval_id).status is ApprovalStatus.REVOKED


def test_a_decided_approval_cannot_be_revoked_twice_or_after_rejection(tmp_path):
    registry = ApprovalRegistry()
    request = registry.request("r", "n", "draft.write", "why")
    registry.submit(request.approval_id, False, "no")
    try:
        registry.revoke(request.approval_id, "late")
    except ValueError as error:
        assert 'has the decision "rejected" and cannot be revoked' in str(error)
    else:
        raise AssertionError("a rejected approval was revoked")


def test_a_single_use_approval_runs_exactly_one_call_even_under_concurrency(tmp_path):
    executor, context = build(tmp_path, declared=("out.txt",), approve=())
    context = context.__class__(
        **{**context.__dict__, "single_use_capabilities": frozenset({"draft.write"})}
    )
    executor = executor.__class__(executor._registry, context)
    first = run(call(executor, "write_draft", path="out.txt", content="1"))
    assert first.status == "blocked"
    context.approvals.submit(first.output["approval_id"], True, "human")

    async def burst():
        return await asyncio.gather(
            *(
                executor.execute(
                    tool_def("write_draft"),
                    invocation(
                        "write_draft", {"path": "out.txt", "content": str(i)}, call_id=f"c{i}"
                    ),
                )
                for i in range(12)
            )
        )

    results = run(burst())
    assert sorted(r.status for r in results).count("succeeded") == 1
    assert written(context) == 1
    spent = context.approvals.get(first.output["approval_id"])
    assert spent.status is ApprovalStatus.CONSUMED
    again = run(call(executor, "write_draft", path="out.txt", content="z"))
    assert again.failure.code == "TOOL_APPROVAL_REQUIRED"
    assert again.output["approval_id"] != first.output["approval_id"]


def test_an_argument_bound_approval_covers_only_the_arguments_it_was_granted_for(tmp_path):
    executor, context = build(tmp_path, declared=("out.txt",), approve=())
    context = context.__class__(**{**context.__dict__, "bind_approvals_to_arguments": True})
    executor = executor.__class__(executor._registry, context)
    first = run(call(executor, "write_draft", path="out.txt", content="approved text"))
    context.approvals.submit(first.output["approval_id"], True, "human")
    assert (
        run(call(executor, "write_draft", path="out.txt", content="approved text")).status
        == "succeeded"
    )
    other = run(call(executor, "write_draft", path="out.txt", content="different text"))
    assert other.status == "blocked" and other.failure.code == "TOOL_APPROVAL_REQUIRED"
    assert other.output["approval_id"] != first.output["approval_id"]
    assert written(context) == 1


def test_a_digest_mismatch_on_a_pinned_approval_id_is_a_policy_denial_with_approval_required(
    tmp_path,
):
    registry = ApprovalRegistry()
    pinned = registry.request("run-1", "node-1", "draft.write", "x", arguments_digest="sha-a")
    registry.submit(pinned.approval_id, True, "ok")
    executor, context = build(
        tmp_path,
        declared=("out.txt",),
        approve=(),
        approvals=registry,
        approval_ids={"draft.write": pinned.approval_id},
    )
    result = run(call(executor, "write_draft", path="out.txt", content="x"))
    assert result.status == "blocked"
    assert "was granted for different arguments" in result.error
    assert written(context) == 0
