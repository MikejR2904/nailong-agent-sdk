import asyncio
from types import SimpleNamespace

from nailong_agent_sdk.starter import ApprovalRequest

from nailong_cli.approvals import NOT_SANDBOXED, Approver, describe
from nailong_cli.permissions import PermissionStore, permissions_path
from tests.conftest import make_console


def ask(approver, tool, **arguments):
    return asyncio.run(approver(ApprovalRequest(tool, arguments)))


def approver_for(home, project, answers="", *, interactive=True, sandbox="bubblewrap", **options):
    console, out, _ = make_console(answers, interactive=interactive)
    store = PermissionStore(permissions_path(home))
    return Approver(console, store, project, sandbox, **options), out, store


def test_saved_rules_are_per_project_and_match_by_pattern(home, tmp_path):
    store = PermissionStore(permissions_path(home))
    one, two = tmp_path / "one", tmp_path / "two"
    store.add(one, "run_command", "pytest*")
    assert store.allows(one, "run_command", "pytest -q tests")
    assert not store.allows(one, "run_command", "rm -rf /")
    assert not store.allows(one, "write_file", "pytest -q")
    assert not store.allows(two, "run_command", "pytest -q")
    assert store.rules(one) == [("run_command", "pytest*")]


def test_a_saved_rule_approves_without_asking(home, project):
    approver, out, store = approver_for(home, project, interactive=False)
    store.add(project, "write_file", "notes.md")
    assert ask(approver, "write_file", path="notes.md", content="x") is True
    assert out.getvalue() == ""


def test_interactive_answers_are_honoured(home, project):
    approver, out, _ = approver_for(home, project, "y\nn\n\n")
    assert ask(approver, "write_file", path="a.txt", content="x") is True
    assert ask(approver, "write_file", path="b.txt", content="x") is False
    assert ask(approver, "write_file", path="c.txt", content="x") is False
    assert "Approve write_file?" in out.getvalue() and "create a.txt" in out.getvalue()


def test_session_approval_covers_that_tool_only(home, project):
    approver, out, _ = approver_for(home, project, "s\nn\n")
    assert ask(approver, "edit_file", path="a.py", old_text="a", new_text="b") is True
    assert ask(approver, "edit_file", path="z.py", old_text="a", new_text="b") is True
    assert ask(approver, "write_file", path="x", content="") is False


def test_always_saves_the_exact_subject_for_next_time(home, project):
    approver, _, store = approver_for(home, project, "a\n")
    assert ask(approver, "run_command", command="pytest -q") is True
    assert store.allows(project, "run_command", "pytest -q")
    assert not store.allows(project, "run_command", "pytest -q; curl evil")


def test_an_unclear_answer_is_asked_again(home, project):
    approver, out, _ = approver_for(home, project, "maybe\ny\n")
    assert ask(approver, "write_file", path="a", content="") is True
    assert "Please answer y, s, a or n." in out.getvalue()


def test_a_command_is_never_allowed_for_a_whole_session_without_a_sandbox(home, project):
    approver, out, _ = approver_for(home, project, "s\ny\ny\n", sandbox=NOT_SANDBOXED)
    assert ask(approver, "run_command", command="make") is True
    assert ask(approver, "run_command", command="make test") is True
    text = out.getvalue()
    assert "never allowed for a whole session without a sandbox" in text
    assert text.count("Approve run_command?") == 2 and f"sandbox: {NOT_SANDBOXED}" in text


def test_without_a_terminal_only_saved_rules_approve_and_the_rest_is_refused(home, project):
    approver, out, store = approver_for(home, project, interactive=False)
    assert ask(approver, "write_file", path="a", content="") is False
    assert "it needs approval and there is no terminal" in out.getvalue()
    store.add(project, "run_command", "pytest*")
    assert ask(approver, "run_command", command="pytest") is True


def test_unattended_commands_without_a_sandbox_are_refused_even_when_a_rule_matches(home, project):
    approver, out, store = approver_for(home, project, interactive=False, sandbox=NOT_SANDBOXED)
    store.add(project, "run_command", "pytest*")
    assert ask(approver, "run_command", command="pytest") is False
    assert "no sandbox is active and --allow-unsandboxed was not given" in out.getvalue()
    allowed, _, store2 = approver_for(
        home, project, interactive=False, sandbox=NOT_SANDBOXED, allow_unsandboxed=True
    )
    assert ask(allowed, "run_command", command="pytest") is True


def test_previews_show_the_change_being_approved(project):
    (project / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    overwrite = describe(
        SimpleNamespace(tool="write_file", arguments={"path": "app.py", "content": "one\nTWO\n"}),
        project,
    )
    assert "overwrite app.py" in overwrite and "-two" in overwrite and "+TWO" in overwrite
    edit = describe(
        SimpleNamespace(
            tool="edit_file", arguments={"path": "app.py", "old_text": "two", "new_text": "2"}
        ),
        project,
    )
    assert "- two" in edit and "+ 2" in edit
    command = describe(SimpleNamespace(tool="run_command", arguments={"command": "make"}), project)
    assert command.strip() == "$ make"
    many = describe(
        SimpleNamespace(tool="write_file", arguments={"path": "big.txt", "content": "x\n" * 100}),
        project,
    )
    assert "more line(s)" in many
