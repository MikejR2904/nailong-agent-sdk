import json

from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from tests.support.tools import build, call, run, tool_def


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


def test_notebook_edit_accepts_empty_source_per_schema(tmp_path):
    executor, ctx = build(tmp_path, declared=("n.ipynb",))
    run(call(executor, "notebook_edit", path="n.ipynb", new_source="print(1)"))
    schema = tool_def("notebook_edit").input_schema
    assert schema["properties"]["new_source"] == {"type": "string"}
    cleared = run(call(executor, "notebook_edit", path="n.ipynb", new_source=""))
    assert cleared.status == "succeeded", cleared.error
