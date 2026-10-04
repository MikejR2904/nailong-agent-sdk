import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.core.services import CoreToolDispatcher, CoreToolServices
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy, SideEffectClass
from nailong_agent_sdk.tools.registry import HarnessToolRegistry, RegisteredTool
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.processes import child_environment
from tests.support.tools import (
    build,
    call,
    mkjunction,
    run,
    tool_def,
)


def test_registry_and_definitions_are_consistent():
    registry_names = set(HarnessToolRegistry().names())
    core_names = {definition.name for definition in core_tool_definitions()}
    assert core_names <= registry_names, core_names - registry_names
    extras = registry_names - core_names
    assert extras == {"read_spec", "run_verilator", "run_yosys", "run_openroad", "run_opensta"}
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


def test_read_file_roundtrip_and_pagination(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)), "utf-8")
    result = run(call(executor, "read_file", path="a.txt", offset=2, limit=3))
    assert result.status == "succeeded"
    assert [row["line"] for row in result.output["lines"]] == [3, 4, 5]
    assert result.output["lines"][0]["text"] == "line 3"
    assert result.output["truncated"] is True
    assert result.output["path"] == "a.txt"
    last = run(call(executor, "read_file", path="a.txt", offset=8, limit=50))
    assert last.output["truncated"] is False
    assert len(last.output["lines"]) == 2


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "..\\outside.txt", "sub/../../outside.txt", "ABS"],
)
def test_read_file_rejects_traversal_and_absolute(tmp_path, path):
    executor, ctx = build(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET", "utf-8")
    (ctx.run_root / "sub").mkdir()
    target = str(outside) if path == "ABS" else path
    result = run(call(executor, "read_file", path=target))
    assert result.status == "failed"
    assert "OUTSIDE-SECRET" not in json.dumps(result.model_dump(mode="json"))
    assert result.error


def test_directory_junction_escape_is_rejected_everywhere(tmp_path):
    executor, ctx = build(tmp_path)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "data.txt").write_text("JUNCTION-SECRET", "utf-8")
    if not mkjunction(ctx.run_root / "link", outside):
        pytest.skip("cannot create junction/symlink in this environment")
    read = run(call(executor, "read_file", path="link/data.txt"))
    assert read.status == "failed" and "JUNCTION-SECRET" not in json.dumps(
        read.model_dump(mode="json")
    )
    globbed = run(call(executor, "glob", pattern="link/*"))
    assert globbed.output["matches"] == []
    globbed_all = run(call(executor, "glob", pattern="**/*"))
    assert not any("data.txt" in match for match in globbed_all.output["matches"])
    grepped = run(call(executor, "grep", pattern="JUNCTION-SECRET"))
    assert grepped.output["matches"] == []


def test_read_file_binary_oversize_directory_messages(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "bin.dat").write_bytes(b"\xff\xfe\x00\x80binary")
    (ctx.run_root / "big.txt").write_text("x" * 600_000, "utf-8")
    (ctx.run_root / "dir").mkdir()
    binary = run(call(executor, "read_file", path="bin.dat"))
    assert binary.status == "failed" and "not valid UTF-8" in binary.error
    big = run(call(executor, "read_file", path="big.txt"))
    assert big.status == "failed" and "read-byte limit" in big.error
    folder = run(call(executor, "read_file", path="dir"))
    assert folder.status == "failed" and "regular file" in folder.error
    missing = run(call(executor, "read_file", path="nope.txt"))
    assert missing.status == "failed" and "does not exist" in missing.error


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


def test_line_numbers_match_newline_delimited_lines(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "ff.txt").write_text("alpha\x0cbeta\nGAMMA\u2028delta\nend", "utf-8")
    result = run(call(executor, "read_file", path="ff.txt"))
    newline_lines = "alpha\x0cbeta\nGAMMA\u2028delta\nend".split("\n")
    shown = [row["text"] for row in result.output["lines"]]
    assert len(shown) == len(newline_lines)


def test_glob_rules_and_limits(tmp_path):
    executor, ctx = build(tmp_path)
    for name in ("b.txt", "a.txt", "sub/c.txt", "sub/deep/d.md"):
        target = ctx.run_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", "utf-8")
    ok = run(call(executor, "glob", pattern="**/*.txt"))
    assert ok.output["matches"] == sorted(ok.output["matches"])
    assert {m.replace("\\", "/") for m in ok.output["matches"]} == {"a.txt", "b.txt", "sub/c.txt"}
    for bad in ("../*", "/etc/*", "sub/../../*", "C:\\Windows\\*"):
        result = run(call(executor, "glob", pattern=bad))
        assert result.status == "failed", bad
        assert result.error, bad
    limited = run(call(executor, "glob", pattern="**/*", limit=2))
    assert len(limited.output["matches"]) == 2
    empty_pattern = run(call(executor, "glob", pattern=""))
    assert empty_pattern.status == "failed" and "non-empty string" in empty_pattern.error


def test_grep_basic_flags_limit_and_errors(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("Needle one\nnothing\nneedle two\n", "utf-8")
    (ctx.run_root / "sub").mkdir()
    (ctx.run_root / "sub" / "c.md").write_text("a needle here\n", "utf-8")
    sensitive = ctx.run_root / ".aws"
    sensitive.mkdir()
    (sensitive / "credentials").write_text("needle-secret\n", "utf-8")
    case = run(call(executor, "grep", pattern="needle"))
    paths = sorted((m["path"].replace("\\", "/"), m["line"]) for m in case.output["matches"])
    assert paths == [("a.txt", 3), ("sub/c.md", 1)]
    insensitive = run(call(executor, "grep", pattern="needle", case_sensitive=False))
    assert len(insensitive.output["matches"]) == 3
    limited = run(call(executor, "grep", pattern="needle", case_sensitive=False, limit=1))
    assert len(limited.output["matches"]) == 1 and limited.output["truncated"] is True
    scoped = run(call(executor, "grep", pattern="needle", file_glob="sub/*.md"))
    assert [m["path"].replace("\\", "/") for m in scoped.output["matches"]] == ["sub/c.md"]
    invalid = run(call(executor, "grep", pattern="("))
    assert invalid.status == "failed" and "GREP_REGEX_INVALID" in invalid.error
    assert "unterminated subpattern" in invalid.error or "missing )" in invalid.error


def test_grep_catastrophic_regex_is_bounded_and_named(tmp_path):
    (tmp_path / "run").mkdir()
    services = CoreToolServices(
        root=tmp_path / "run",
        artifacts=build(tmp_path)[1].artifacts,
        declared_output_paths=(),
        max_grep_seconds=3.0,
    )
    (tmp_path / "run" / "evil.txt").write_text("a" * 40 + "b\n", "utf-8")
    dispatcher = CoreToolDispatcher(services)
    started = time.monotonic()
    result = run(dispatcher.execute("grep", {"pattern": "(a+)+$"}))
    elapsed = time.monotonic() - started
    assert result.status == "failed" and "GREP_REGEX_TIMEOUT" in result.error
    assert elapsed < 8


def test_grep_latency_and_parallel_batch(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello world\n", "utf-8")
    singles = []
    for _ in range(3):
        started = time.monotonic()
        result = run(call(executor, "grep", pattern="hello"))
        singles.append(time.monotonic() - started)
        assert result.status == "succeeded"

    async def batch(width):
        started = time.monotonic()
        results = await asyncio.gather(
            *(call(executor, "grep", pattern="hello") for _ in range(width))
        )
        return time.monotonic() - started, results

    durations = {}
    failures = {}
    for width in (4, 8, 16):
        elapsed, results = run(batch(width), timeout=180)
        durations[width] = elapsed
        failures[width] = [r.error for r in results if r.status != "succeeded"]
    for width, errors in failures.items():
        assert errors == [], f"parallel width {width}: {errors[:2]}"


def test_write_draft_declared_only_and_roundtrip(tmp_path):
    executor, ctx = build(tmp_path, declared=("out/draft.md",))
    undeclared = run(call(executor, "write_draft", path="other.md", content="x"))
    assert undeclared.status == "failed" and "not declared" in undeclared.error
    assert "other.md" in undeclared.error, undeclared.error
    first = run(call(executor, "write_draft", path="out/draft.md", content="héllo\n文件\n"))
    assert first.status == "succeeded"
    record = first.output
    assert (ctx.run_root / "out" / "draft.md").read_text("utf-8") == "héllo\n文件\n"
    assert record["artifact_id"].startswith("sha256:")
    assert record["manifest"] == {"run_id": "run-1", "node_id": "node-1", "task_id": "T1"}
    second = run(call(executor, "write_draft", path="out/draft.md", content="héllo\n文件\n"))
    assert second.output["artifact_id"] == record["artifact_id"]
    assert second.output["occurrence_id"] != record["occurrence_id"]
    assert ctx.artifacts.read_text(record["artifact_id"]) == "héllo\n文件\n"


def test_write_and_edit_accept_empty_replacement_per_schema(tmp_path):
    executor, ctx = build(tmp_path, declared=("d.txt", "n.ipynb"))
    run(call(executor, "write_draft", path="d.txt", content="keep REMOVE keep\n"))
    schema = tool_def("edit_draft").input_schema
    assert schema["properties"]["new_text"] == {"type": "string"}
    deleted = run(call(executor, "edit_draft", path="d.txt", old_text=" REMOVE", new_text=""))
    assert deleted.status == "succeeded", deleted.error
    assert (ctx.run_root / "d.txt").read_text("utf-8") == "keep keep\n"


def test_write_draft_empty_content_matches_schema(tmp_path):
    executor, ctx = build(tmp_path, declared=("empty.txt",))
    schema = tool_def("write_draft").input_schema
    assert schema["properties"]["content"] == {"type": "string"}
    result = run(call(executor, "write_draft", path="empty.txt", content=""))
    assert result.status == "succeeded", result.error


def test_edit_draft_rules(tmp_path):
    executor, ctx = build(tmp_path, declared=("d.txt", "missing.txt"))
    run(call(executor, "write_draft", path="d.txt", content="a b a b a\n"))
    ambiguous = run(call(executor, "edit_draft", path="d.txt", old_text="a", new_text="Z"))
    assert ambiguous.status == "failed" and "ambiguous" in ambiguous.error
    absent = run(call(executor, "edit_draft", path="d.txt", old_text="q", new_text="Z"))
    assert absent.status == "failed" and "not found" in absent.error
    replaced = run(
        call(executor, "edit_draft", path="d.txt", old_text="a", new_text="Z", replace_all=True)
    )
    assert replaced.output["replacements"] == 3
    assert (ctx.run_root / "d.txt").read_text("utf-8") == "Z b Z b Z\n"
    nofile = run(call(executor, "edit_draft", path="missing.txt", old_text="a", new_text="b"))
    assert nofile.status == "failed" and "does not exist" in nofile.error


def test_notebook_edit_rules(tmp_path):
    executor, ctx = build(tmp_path, declared=("n.ipynb", "bad.ipynb", "list.ipynb"))
    created = run(call(executor, "notebook_edit", path="n.ipynb", new_source="print(1)\nprint(2)"))
    assert created.status == "succeeded"
    document = json.loads((ctx.run_root / "n.ipynb").read_text("utf-8"))
    assert document["cells"][0]["source"] == ["print(1)\n", "print(2)"]
    appended = run(
        call(executor, "notebook_edit", path="n.ipynb", new_source="\nprint(3)", mode="append")
    )
    assert appended.status == "succeeded"
    sparse = run(call(executor, "notebook_edit", path="n.ipynb", cell_index=3, new_source="x"))
    assert json.loads((ctx.run_root / "n.ipynb").read_text("utf-8"))["cells"][3]["source"] == ["x"]
    assert sparse.status == "succeeded"
    too_far = run(call(executor, "notebook_edit", path="n.ipynb", cell_index=2001, new_source="x"))
    assert too_far.status == "failed" and "between 0 and 2000" in too_far.error
    (ctx.run_root / "bad.ipynb").write_text("not json", "utf-8")
    bad = run(call(executor, "notebook_edit", path="bad.ipynb", new_source="x"))
    assert bad.status == "failed" and "bad.ipynb" in bad.error, bad.error
    (ctx.run_root / "list.ipynb").write_text("[1,2]", "utf-8")
    listed = run(call(executor, "notebook_edit", path="list.ipynb", new_source="x"))
    assert listed.status == "failed"
    assert "setdefault" not in listed.error, listed.error


def test_get_tool_result_cannot_read_outside_the_journal(tmp_path):
    (tmp_path / "run").mkdir()
    journal = FileToolResultJournal(tmp_path / "run")
    executor, ctx = build(tmp_path, journal=journal)
    (tmp_path / "secret_outside.json").write_text('{"api": "OUTSIDE-JSON-SECRET"}', "utf-8")
    (ctx.run_root / "inside.json").write_text('{"api": "INSIDE-RUNROOT-SECRET"}', "utf-8")
    outcomes = {}
    for handle in (
        "../../secret_outside",
        "..\\..\\secret_outside",
        "../inside",
        str(tmp_path / "secret_outside"),
    ):
        result = run(call(executor, "get_tool_result", handle_id=handle))
        blob = json.dumps(result.model_dump(mode="json"))
        outcomes[handle] = ("OUTSIDE-JSON-SECRET" in blob, "INSIDE-RUNROOT-SECRET" in blob)
    assert not any(any(flags) for flags in outcomes.values()), outcomes


def test_get_tool_result_normal_paths(tmp_path):
    from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult

    (tmp_path / "run").mkdir()
    journal = FileToolResultJournal(tmp_path / "run")
    executor, ctx = build(tmp_path, journal=journal)
    handle = journal.record(
        ToolCall(id="c1", name="grep", arguments={"pattern": "x"}),
        ToolExecutionResult(status="succeeded", output={"rows": list(range(500))}),
    )
    full = run(call(executor, "get_tool_result", handle_id=handle.handle_id, max_chars=64))
    assert full.status == "succeeded" and full.output["truncated"] is True
    assert len(full.output["content"]) == 64
    unknown = run(call(executor, "get_tool_result", handle_id="handle-999999"))
    assert unknown.status == "failed" and "handle-999999" in unknown.error
    no_journal_executor, _ = build(tmp_path / "x")
    nothing = run(call(no_journal_executor, "get_tool_result", handle_id="h"))
    assert nothing.status == "failed" and "No result journal" in nothing.error


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


def test_run_registered_command_result_shapes(tmp_path):
    templates = [
        CommandTemplate(
            name="ok", command=[sys.executable, "-c", "print('hi')"], timeout_seconds=30
        ),
        CommandTemplate(
            name="fail",
            command=[sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
            timeout_seconds=30,
        ),
        CommandTemplate(
            name="slow",
            command=[sys.executable, "-c", "import time; time.sleep(60)"],
            timeout_seconds=1,
        ),
        CommandTemplate(name="missing", command=["definitely-not-a-binary-xyz"], timeout_seconds=5),
        CommandTemplate(
            name="big",
            command=[sys.executable, "-c", "print('x' * 1_000_000)"],
            timeout_seconds=30,
            max_output_bytes=1000,
        ),
    ]
    executor, ctx = build(tmp_path, templates=templates, approve=("process.execute",))
    ok = run(call(executor, "run_registered_command", template_name="ok"))
    assert ok.status == "succeeded" and ok.output["exit_kind"] == "succeeded"
    fail = run(call(executor, "run_registered_command", template_name="fail"))
    assert fail.status == "failed" and fail.output["return_code"] == 3
    assert "3" in fail.error, fail.error
    slow_started = time.monotonic()
    slow = run(call(executor, "run_registered_command", template_name="slow"))
    assert time.monotonic() - slow_started < 15
    assert slow.status == "failed" and slow.output["timed_out"] is True
    assert "PROCESS_TIMEOUT" in slow.error
    missing = run(call(executor, "run_registered_command", template_name="missing"))
    assert missing.status == "failed"
    assert "definitely-not-a-binary-xyz" in missing.error, missing.error
    big = run(call(executor, "run_registered_command", template_name="big"))
    assert big.status == "succeeded"
    assert big.output["output_bytes_retained"] == 1000
    assert big.output["output_bytes_total"] >= 1_000_000
    unknown = run(call(executor, "run_registered_command", template_name="nope"))
    assert unknown.status == "failed" and '"nope"' in unknown.error


def test_read_artifact_authorization_and_forged_occurrence_chain(tmp_path):
    executor, ctx = build(tmp_path, declared=("forged.json", "draft.txt"))
    base = ctx.artifacts.write_text("base.txt", "base\n")
    secret = ctx.artifacts.write_text(
        "other/secret.txt", "TOP-SECRET-ARTIFACT\n", manifest={"run_id": "other-run"}
    )
    ctx.plan_task.authorized_artifact_ids.append(base.artifact_id)
    allowed = run(call(executor, "read_artifact", artifact_id=base.artifact_id))
    assert allowed.status == "succeeded" and allowed.output["content"] == "base\n"
    denied = run(call(executor, "read_artifact", artifact_id=secret.artifact_id))
    assert denied.status == "failed" and "not authorized" in denied.error
    greped = run(call(executor, "grep_artifact", artifact_id=secret.artifact_id, needle="TOP"))
    assert greped.status == "failed"
    no_proof = run(
        call(
            executor,
            "diff_declared_artifacts",
            base_artifact_id=base.artifact_id,
            draft_artifact_id=secret.artifact_id,
        )
    )
    assert no_proof.status == "failed" and "occurrence proof" in no_proof.error
    forged = {
        "occurrence_id": "occ-forged",
        "artifact_id": secret.artifact_id,
        "relative_path": "draft.txt",
        "sha256": secret.sha256,
        "size_bytes": secret.size_bytes,
        "kind": "draft",
        "manifest": {"run_id": "run-1", "node_id": "node-1", "task_id": "T1"},
        "written_at_utc": "2026-01-01T00:00:00+00:00",
    }
    wrote = run(call(executor, "write_draft", path="forged.json", content=json.dumps(forged)))
    assert wrote.status == "succeeded"
    attack = run(
        call(
            executor,
            "diff_declared_artifacts",
            base_artifact_id=base.artifact_id,
            draft_artifact_id=secret.artifact_id,
            draft_occurrence_id="../../forged",
        )
    )
    leaked = "TOP-SECRET-ARTIFACT" in json.dumps(attack.model_dump(mode="json"))
    assert not leaked, "unauthorized artifact content disclosed through forged occurrence record"


def test_diff_with_real_occurrence_proof_and_other_task_rejected(tmp_path):
    executor, ctx = build(tmp_path, declared=("draft.txt",))
    base = ctx.artifacts.write_text("base.txt", "line1\nline2\n")
    ctx.plan_task.authorized_artifact_ids.append(base.artifact_id)
    wrote = run(call(executor, "write_draft", path="draft.txt", content="line1\nLINE2\n"))
    good = run(
        call(
            executor,
            "diff_declared_artifacts",
            base_artifact_id=base.artifact_id,
            draft_artifact_id=wrote.output["artifact_id"],
            draft_occurrence_id=wrote.output["occurrence_id"],
        )
    )
    assert good.status == "succeeded" and "-line2" in good.output["unified_diff"]
    other_task = ctx.artifacts.write_text(
        "draft.txt", "stolen\n", manifest={"run_id": "run-1", "node_id": "node-1", "task_id": "T2"}
    )
    stolen = run(
        call(
            executor,
            "diff_declared_artifacts",
            base_artifact_id=base.artifact_id,
            draft_artifact_id=other_task.artifact_id,
            draft_occurrence_id=other_task.occurrence_id,
        )
    )
    assert stolen.status == "failed" and "occurrence proof" in stolen.error


def test_sleep_and_brief_bounds(tmp_path):
    executor, _ = build(tmp_path)
    for bad, fragment in (
        (-1, "between 0 and 30"),
        (31, "between 0 and 30"),
        (True, "numeric"),
        ("1", "numeric"),
        (float("nan"), "between 0 and 30"),
        (float("inf"), "between 0 and 30"),
    ):
        result = run(call(executor, "sleep", seconds=bad))
        assert result.status == "failed" and fragment in result.error, (bad, result.error)
    quick = run(call(executor, "sleep", seconds=0.05))
    assert quick.status == "succeeded"
    brief = run(call(executor, "brief", text="x" * 5000, max_chars=100))
    assert len(brief.output["text"]) == 100 and brief.output["truncated"] is True
    low = run(call(executor, "brief", text="abc", max_chars=19))
    assert low.status == "failed" and "between 20 and 2000" in low.error
    run(call(executor, "brief", text=""))


def test_web_search_and_human_question_paths(tmp_path):
    from nailong_agent_sdk.tools.core.services import SearchResult

    class FakeClient:
        async def search(self, query, limit):
            if query == "boom":
                raise RuntimeError("search backend exploded")
            return [
                SearchResult(title=f"t{i}", url=f"https://e.test/{i}", snippet="s")
                for i in range(20)
            ]

    async def human(question):
        return None if question == "silent" else f"answer to {question}"

    executor, _ = build(tmp_path, search_client=FakeClient(), ask_human=human)
    found = run(call(executor, "web_search", query="q", max_results=3))
    assert len(found.output["results"]) == 3 and found.output["untrusted_content"] is True
    boom = run(call(executor, "web_search", query="boom"))
    assert boom.status == "failed" and "search backend exploded" in boom.error
    zero = run(call(executor, "web_search", query="q", max_results=0))
    assert zero.status == "failed" and "between 1 and 10" in zero.error
    answered = run(call(executor, "ask_human_question", question="why"))
    assert answered.output == {"answer": "answer to why", "answered": True}
    silent = run(call(executor, "ask_human_question", question="silent"))
    assert silent.output == {"answer": None, "answered": False}
    bare, _ = build(tmp_path / "bare")
    missing = run(call(bare, "ask_human_question", question="x"))
    assert missing.status == "failed" and "No interactive human-question responder" in missing.error


def test_read_spec_scope_enforcement(tmp_path):
    executor, _ = build(tmp_path, scope="REQ-1", spec_snapshots={"REQ-1": "Spec text"})
    ok = run(call(executor, "read_spec", pointer="REQ-1"))
    assert ok.output == {"pointer": "REQ-1", "content": "Spec text"}
    foreign = run(call(executor, "read_spec", pointer="REQ-2"))
    assert foreign.status == "failed" and "outside the scoped PlanTask" in foreign.error
    unavailable, _ = build(tmp_path / "u", scope="REQ-1", spec_snapshots={})
    missing = run(call(unavailable, "read_spec", pointer="REQ-1"))
    assert missing.status == "failed" and "unavailable" in missing.error


@pytest.mark.parametrize(
    "arguments, fragment",
    [
        ({"path": 123}, '"path"'),
        ({"path": ""}, '"path"'),
        ({"path": "   "}, "non-empty"),
        ({"path": "a.txt", "offset": 1.0}, '"offset"'),
        ({"path": "a.txt", "offset": "1"}, '"offset"'),
        ({"path": "a.txt", "offset": True}, '"offset"'),
        ({"path": "a.txt", "offset": -1}, '"offset"'),
        ({"path": "a.txt", "limit": 0}, '"limit"'),
        ({"path": "a.txt", "limit": 2001}, '"limit"'),
        ({"path": "a.txt", "limit": None}, '"limit"'),
        ({"path": "a\x00b.txt"}, ""),
    ],
)
def test_read_file_argument_type_confusion_is_rejected_cleanly(tmp_path, arguments, fragment):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello", "utf-8")
    result = run(call(executor, "read_file", **arguments))
    assert result.status == "failed"
    assert result.error and fragment in result.error, result.error


def test_unicode_and_spaced_paths(tmp_path):
    executor, ctx = build(tmp_path, declared=("dir with space/résumé 文件.md",))
    wrote = run(call(executor, "write_draft", path="dir with space/résumé 文件.md", content="ok"))
    assert wrote.status == "succeeded"
    read = run(call(executor, "read_file", path="dir with space/résumé 文件.md"))
    assert read.output["lines"][0]["text"] == "ok"
    matched = run(call(executor, "glob", pattern="dir with space/*"))
    assert matched.output["matches"] == ["dir with space\\résumé 文件.md"] or matched.output[
        "matches"
    ] == ["dir with space/résumé 文件.md"]


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


def test_notebook_edit_accepts_empty_source_per_schema(tmp_path):
    executor, ctx = build(tmp_path, declared=("n.ipynb",))
    run(call(executor, "notebook_edit", path="n.ipynb", new_source="print(1)"))
    schema = tool_def("notebook_edit").input_schema
    assert schema["properties"]["new_source"] == {"type": "string"}
    cleared = run(call(executor, "notebook_edit", path="n.ipynb", new_source=""))
    assert cleared.status == "succeeded", cleared.error


def test_grep_parallel_width_threshold(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "a.txt").write_text("hello world\n", "utf-8")

    async def batch(width):
        started = time.monotonic()
        results = await asyncio.gather(
            *(call(executor, "grep", pattern="hello") for _ in range(width))
        )
        return time.monotonic() - started, [r.error for r in results if r.status != "succeeded"]

    report = {}
    for width in (32, 64):
        elapsed, errors = run(batch(width), timeout=400)
        report[width] = {"seconds": elapsed, "failed": len(errors), "sample": errors[:1]}
    assert all(entry["failed"] == 0 for entry in report.values()), report


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


def test_grep_line_numbers_follow_newlines(tmp_path):
    executor, ctx = build(tmp_path)
    (ctx.run_root / "ff.txt").write_text("alpha\x0cbeta\nGAMMA delta\nend", "utf-8")
    result = run(call(executor, "grep", pattern="delta"))
    assert [(m["path"], m["line"]) for m in result.output["matches"]] == [("ff.txt", 2)]


def test_the_read_scope_of_a_role_without_filesystem_read_is_empty():
    policy = CapabilityPolicy(
        [
            CapabilityGrant(role="a", capabilities=["filesystem.read"], allowed_paths=["docs"]),
            CapabilityGrant(role="b", capabilities=["draft.write"], allowed_paths=["docs"]),
        ]
    )
    assert policy.read_scope("a") == ("docs",)
    assert policy.read_scope("b") == () and policy.read_scope("unknown") == ()


def test_grep_does_not_rerun_a_host_script_that_lacks_a_main_guard(tmp_path):
    marker = tmp_path / "runs.txt"
    script = tmp_path / "host.py"
    script.write_text(
        "\n".join(
            [
                "import asyncio, pathlib",
                "from nailong_agent_sdk.tools.artifacts import ArtifactStore",
                "from nailong_agent_sdk.tools.core.services import CoreToolDispatcher, "
                "CoreToolServices",
                f"root = pathlib.Path(r'{tmp_path}') / 'run'",
                "root.mkdir(exist_ok=True)",
                "(root / 'a.txt').write_text('hello' + chr(10), 'utf-8')",
                f"open(r'{marker}', 'a').write('run' + chr(10))",
                "services = CoreToolServices(root=root, artifacts=ArtifactStore(root), "
                "declared_output_paths=())",
                "dispatcher = CoreToolDispatcher(services)",
                "print(asyncio.run(dispatcher.execute('grep', {'pattern': 'hello'})).status)",
            ]
        ),
        "utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=child_environment(),
        timeout=120,
    )
    assert completed.stdout.strip() == "succeeded", completed.stderr[-600:]
    assert marker.read_text("utf-8").splitlines() == ["run"]
