from __future__ import annotations

from nailong_cli.checkpoints import CheckpointStore
from tests.conftest import call, final, instructions_text, run_cli


def test_the_default_instructions_tell_the_agent_not_to_leave_test_or_scratch_files(project, home):
    _, _, _, setup = run_cli(project, home, [], final(), lines="Hi\n/exit\n", interactive=True)
    text = instructions_text(setup.model.calls[0])
    assert "Do not add test or scratch files to check your work unless the user asked" in text
    assert "delete any file you made only for checking before you finish" in text


def test_a_turn_reports_the_files_it_created_that_are_still_there(project, home):
    (project / "calc.py").write_text("x = 1\n", encoding="utf-8")
    _, out, err, _ = run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits"],
        call("edit_file", path="calc.py", old_text="x = 1", new_text="x = 2"),
        call("write_file", path="test_calc.py", content="assert True\n"),
        final("done"),
        lines="Fix calc.py\n/exit\n",
        interactive=True,
    )
    assert "created this turn and still there: test_calc.py  (/rewind removes them)" in out, err
    assert "calc.py," not in out.split("created this turn")[1]


def test_a_file_the_agent_made_and_then_deleted_is_not_reported(project, home):
    _, out, _, _ = run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits"],
        call("write_file", path="scratch.txt", content="x\n"),
        final("done"),
        lines="Go\n/exit\n",
        interactive=True,
    )
    assert "created this turn and still there: scratch.txt" in out
    (project / "scratch.txt").unlink()
    store = CheckpointStore(project, project / ".nailong" / "checkpoints", {})
    store.turn = 1
    assert store.created() == []


def test_created_lists_new_files_from_commands_too_and_not_files_that_already_existed(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "old.py").write_text("a\n", encoding="utf-8")
    store = CheckpointStore(root, tmp_path / "ck", {})
    store.begin_turn()
    from nailong_agent_sdk.starter import ToolEvent

    store.before_tool(ToolEvent("write_file", {"path": "old.py"}, 1))
    store.before_tool(ToolEvent("write_file", {"path": "new.py"}, 1))
    (root / "new.py").write_text("b\n", encoding="utf-8")
    store.before_tool(ToolEvent("run_command", {"command": "x"}, 2))
    (root / "from_command.py").write_text("c\n", encoding="utf-8")
    assert store.created() == ["from_command.py", "new.py"]
    assert store.created(99) == []


def test_asking_the_user_in_a_run_with_nobody_there_says_so_instead_of_returning_nothing(
    project, home
):
    _, out, err, setup = run_cli(
        project,
        home,
        ["-p", "Do it"],
        call("ask_human_question", question="Which one?"),
        final("done"),
    )
    seen = " ".join(str(item) for item in setup.model.calls[1].observations)
    assert "Nobody is at the terminal to answer" in seen and "Do not ask again" in seen


def test_a_refused_command_tells_the_model_why_and_not_to_try_again(project, home):
    _, out, err, setup = run_cli(
        project,
        home,
        ["-p", "Check it", "--permission-mode", "accept-edits"],
        call("run_command", command="python -m unittest"),
        final("done without running it"),
    )
    seen = " ".join(str(item) for item in setup.model.calls[1].observations)
    assert "no sandbox is active and --allow-unsandboxed was not given" in seen
    assert "Do not retry it or try another way to do the same thing" in seen


def test_a_person_saying_no_is_passed_on_as_a_no_not_a_bare_failure(project, home):
    _, out, err, setup = run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits", "--allow-unsandboxed"],
        call("run_command", command="echo hi"),
        final("ok"),
        lines="Run it\nn\n/exit\n",
        interactive=True,
    )
    seen = " ".join(str(item) for item in setup.model.calls[1].observations)
    assert "The person declined it" in seen


def test_a_project_too_large_to_snapshot_says_so_instead_of_claiming_nothing_changed(
    tmp_path, monkeypatch
):
    from nailong_agent_sdk.starter import ToolEvent

    from nailong_cli import checkpoints

    monkeypatch.setattr(checkpoints, "MAX_TREE_FILES", 3)
    root = tmp_path / "p"
    root.mkdir()
    for number in range(6):
        (root / f"f{number}.txt").write_text("x\n", encoding="utf-8")
    store = CheckpointStore(root, tmp_path / "ck", {})
    store.begin_turn()
    store.before_tool(ToolEvent("run_command", {"command": "x"}, 1))
    assert store.untracked_turns() == [1]
    assert store.turns() == [
        (1, ["(a command ran; the project is too large to undo what it changed)"])
    ]


def test_files_git_ignores_do_not_count_toward_the_snapshot_limit(tmp_path, monkeypatch):
    import subprocess

    from nailong_agent_sdk.starter import ToolEvent

    from nailong_cli import checkpoints

    monkeypatch.setattr(checkpoints, "MAX_TREE_FILES", 10)
    root = tmp_path / "p"
    (root / "build").mkdir(parents=True)
    for number in range(60):
        (root / "build" / f"o{number}.bin").write_text("x\n", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    (root / "keep.py").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    store = CheckpointStore(root, tmp_path / "ck", {})
    store.begin_turn()
    store.before_tool(ToolEvent("run_command", {"command": "x"}, 1))
    assert store.untracked_turns() == []
    (root / "made.py").write_text("b\n", encoding="utf-8")
    assert store.plan(1)["delete"] == ["made.py"]
    assert store.rewind(1) == ["made.py"] and not (root / "made.py").exists()
