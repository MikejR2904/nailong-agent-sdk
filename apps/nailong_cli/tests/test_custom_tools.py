from __future__ import annotations

from nailong_cli import custom_tools
from tests.conftest import call, final, run_cli, tool_names

SHOUT = '''
from nailong_agent_sdk.starter import tool


@tool
def shout(text: str) -> str:
    """Return the text in capitals."""
    return text.upper()
'''

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def user_tools(home):
    return home / ".config" / "nailong" / "tools"


def project_tools(project):
    return project / ".nailong" / "tools"


def test_a_tool_in_the_user_folder_is_available_and_the_agent_can_call_it(project, home):
    write(user_tools(home) / "shout.py", SHOUT)
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("shout", text="hello"),
        final("done"),
        lines="Shout it\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "shout" in tool_names(setup.model.calls[0])
    assert "HELLO" in str(setup.model.calls[1].observations)


def test_a_file_with_only_a_run_function_becomes_a_tool_named_after_the_file(project, home):
    write(
        user_tools(home) / "word_count.py",
        'def run(text: str) -> int:\n    """Count the words."""\n    return len(text.split())\n',
    )
    _, _, _, setup = run_cli(
        project,
        home,
        BYPASS,
        call("word_count", text="a b c"),
        final("done"),
        lines="Count\n/exit\n",
        interactive=True,
    )
    assert "word_count" in tool_names(setup.model.calls[0])
    assert "3" in str(setup.model.calls[1].observations)


def test_a_custom_tool_needs_approval_even_if_its_author_did_not_ask_for_it(project, home):
    write(user_tools(home) / "shout.py", SHOUT)
    _, out, err, _ = run_cli(
        project, home, ["-p", "Shout"], call("shout", text="hi"), final("done")
    )
    assert "Refused shout" in out + err and "needs approval" in out + err


def test_a_project_tool_file_waits_for_you_to_read_and_trust_it(project, home):
    write(project_tools(project) / "shout.py", SHOUT)
    _, out, _, setup = run_cli(
        project, home, BYPASS, final(), lines="Hello\n/exit\n", interactive=True
    )
    assert "is not loaded" in out and "/trust-tools" in out
    assert "shout" not in tool_names(setup.model.calls[0])


def test_trusting_a_project_tool_loads_it_for_the_next_turn(project, home):
    write(project_tools(project) / "shout.py", SHOUT)
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("shout", text="ok"),
        final("done"),
        lines="/trust-tools\ny\nUse it\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "Trust it?" in out and "def shout" in out
    assert "shout" in tool_names(setup.model.calls[0])
    assert "OK" in str(setup.model.calls[1].observations)


def test_declining_leaves_the_file_unloaded(project, home):
    write(project_tools(project) / "shout.py", SHOUT)
    _, _, _, setup = run_cli(
        project, home, BYPASS, final(), lines="/trust-tools\nn\nHi\n/exit\n", interactive=True
    )
    assert "shout" not in tool_names(setup.model.calls[0])


def test_a_trusted_file_that_changes_is_not_loaded_until_trusted_again(project, home):
    path = project_tools(project) / "shout.py"
    write(path, SHOUT)
    custom_tools.trust(home, path)
    assert [t.name for t in custom_tools.load_custom_tools(project, home).tools] == ["shout"]
    write(path, SHOUT + "\n# edited\n")
    result = custom_tools.load_custom_tools(project, home)
    assert result.tools == [] and "changed since you trusted it" in result.notices[0]


def test_a_broken_file_is_named_and_the_others_still_load(project, home):
    write(user_tools(home) / "good.py", SHOUT)
    write(user_tools(home) / "bad.py", "raise RuntimeError('boom')\n")
    write(user_tools(home) / "empty.py", "x = 1\n")
    result = custom_tools.load_custom_tools(project, home)
    assert [t.name for t in result.tools] == ["shout"]
    text = "\n".join(result.notices)
    assert "bad.py could not be loaded: RuntimeError: boom" in text
    assert "empty.py" in text and "defines no tool" in text


def test_a_tool_may_not_take_the_name_of_a_built_in(project, home):
    write(
        user_tools(home) / "read_file.py",
        'def run(path: str) -> str:\n    """Fake read."""\n    return "fake"\n',
    )
    _, out, _, setup = run_cli(project, home, [], final(), lines="Hi\n/exit\n", interactive=True)
    assert 'custom tool "read_file" is skipped' in out
    first = setup.model.calls[0]
    assert tool_names(first).count("read_file") == 1


def test_two_files_cannot_both_define_the_same_tool(project, home):
    write(user_tools(home) / "a.py", SHOUT)
    write(user_tools(home) / "b.py", SHOUT)
    result = custom_tools.load_custom_tools(project, home)
    assert len(result.tools) == 1 and "already comes from" in result.notices[0]


def test_tools_new_makes_a_working_template_and_user_ones_load_at_once(project, home):
    _, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("echoer", text="abc"),
        final("done"),
        lines="/tools new echoer user\n/tools new Bad-Name\n/tools\nTry\n/exit\n",
        interactive=True,
    )
    assert (user_tools(home) / "echoer.py").is_file()
    assert "must be letters, digits and underscores" in err
    assert "echoer: Say in one sentence" in out and "[user:" in out
    assert "ABC" in str(setup.model.calls[-1].observations)


def test_planning_mode_does_not_offer_custom_tools(project, home):
    write(user_tools(home) / "shout.py", SHOUT)
    _, _, _, setup = run_cli(
        project,
        home,
        ["--permission-mode", "plan"],
        final(),
        lines="Plan\n/exit\n",
        interactive=True,
    )
    assert "shout" not in tool_names(setup.model.calls[0])
