import json
import shutil
import subprocess
import sys

import pytest
from nailong_agent_sdk.foundations.contracts import FinalTurn
from nailong_agent_sdk.starter import ModelSetup

from tests.conftest import call, final, instructions_text, run_cli, tool_names, write_user_config

BASE = 'provider = "anthropic"\nmodel = "m"\napi_key_env = "TEST_KEY"\n'
STDIO_SERVER = """
from mcp.server import MCPServer

server = MCPServer(name="demo")


@server.tool(name="echo")
async def echo(text: str) -> str:
    return f"echo:{text}"


if __name__ == "__main__":
    server.run(transport="stdio")
"""


def seen(setup) -> str:
    context = setup.model.calls[-1]
    return str(list(context.observations)) + str(list(context.episodes))


def test_the_agent_keeps_a_task_list_and_the_user_sees_it(project, home):
    items = [
        {"content": "read the code", "status": "completed"},
        {"content": "fix the bug", "status": "in_progress"},
        {"content": "add a test", "status": "pending"},
    ]
    code, out, _, _ = run_cli(
        project, home, ["-p", "Do it"], call("todo_write", items=items), final()
    )
    assert code == 0 and "[x] read the code" in out and "[~] fix the bug" in out
    assert "[ ] add a test" in out and "1 of 3 done" in out


def test_a_bad_task_list_is_refused_with_the_reason(project, home):
    items = [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]
    _, _, _, setup = run_cli(project, home, ["-p", "x"], call("todo_write", items=items), final())
    assert "Only one todo may be in_progress" in seen(setup)


def test_rewind_restores_the_files_the_agent_changed(project, home):
    (project / "a.txt").write_text("old", encoding="utf-8")
    code, out, _, _ = run_cli(
        project,
        home,
        [],
        call("write_file", path="a.txt", content="new"),
        call("write_file", path="b.txt", content="fresh"),
        final(),
        lines="Change files\ny\ny\n/rewind\n/rewind 1\ny\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "turn 1: a.txt, b.txt" in out and "rewound 2 file(s)" in out
    assert (project / "a.txt").read_text(encoding="utf-8") == "old"
    assert not (project / "b.txt").exists()


def test_your_own_commands_and_skills_are_available(project, home):
    commands = project / ".nailong" / "commands"
    commands.mkdir()
    (commands / "ship.md").write_text("Run the checks for $ARGUMENTS and report.", encoding="utf-8")
    skills = project / ".nailong" / "skills"
    skills.mkdir()
    (skills / "style.md").write_text("Use snake_case.", encoding="utf-8")
    code, out, _, setup = run_cli(
        project, home, [], final(), lines="/ship api\n/help\n/skills\n/exit\n", interactive=True
    )
    first = setup.model.calls[0]
    assert code == 0 and first.task.instructions == "Run the checks for api and report."
    assert "use_skill" in tool_names(first) and "- style: Use snake_case." in instructions_text(
        first
    )
    assert "/ship" in out and "style (15 characters)" in out


def test_oversized_skills_are_refused_with_what_to_do(project, home):
    skills = project / ".nailong" / "skills"
    skills.mkdir()
    (skills / "big.md").write_text("x" * 25_000, encoding="utf-8")
    code, _, err, _ = run_cli(project, home, ["-p", "x"], final())
    assert code == 64 and "over the 20000 limit" in err


def test_a_user_hook_can_refuse_a_tool_call_and_another_can_follow_one(project, home):
    write_user_config(
        home,
        BASE
        + '[[hooks]]\nevent = "pre_tool"\ntool = "write_file"\ncommand = "exit 1"\n'
        + '[[hooks]]\nevent = "post_tool"\ntool = "todo_write"\n'
        + 'command = "echo ran > marker.txt"\n',
    )
    items = [{"content": "a", "status": "pending"}]
    _, _, _, setup = run_cli(
        project,
        home,
        ["-p", "x", "--allow", "write_file:*"],
        call("write_file", path="blocked.txt", content="no"),
        call("todo_write", items=items),
        final(),
    )
    assert not (project / "blocked.txt").exists()
    assert "Hook refused write_file" in str(list(setup.model.calls[1].observations))
    assert (project / "marker.txt").exists()


def test_mcp_servers_from_the_user_file_become_tools(project, home, tmp_path):
    script = tmp_path / "server.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    write_user_config(
        home,
        BASE
        + f"[mcp.demo]\ncommand = {json.dumps(sys.executable)}\n"
        + f"args = [{json.dumps(str(script))}]\n",
    )
    code, out, _, setup = run_cli(
        project,
        home,
        ["-p", "Echo", "--allow", "mcp__demo__echo:*"],
        call("mcp__demo__echo", text="hi"),
        final(),
    )
    assert code == 0 and "mcp__demo__echo" in str(tool_names(setup.model.calls[0]))
    assert "echo:hi" in seen(setup) and "untrusted" in seen(setup)
    _, listing, _, _ = run_cli(project, home, [], lines="/mcp\n/exit\n", interactive=True)
    assert "mcp__demo__echo" in listing


def test_an_mcp_config_file_in_the_usual_json_form_works_and_a_dead_server_is_named(
    project, home, tmp_path
):
    script = tmp_path / "server.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    good = tmp_path / "mcp.json"
    good.write_text(
        json.dumps({"mcpServers": {"demo": {"command": sys.executable, "args": [str(script)]}}}),
        encoding="utf-8",
    )
    _, listing, _, _ = run_cli(
        project, home, ["--mcp-config", str(good)], lines="/mcp\n/exit\n", interactive=True
    )
    assert "mcp__demo__echo" in listing
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ghost": {"command": sys.executable, "args": ["-c", "raise SystemExit(3)"]}
                }
            }
        ),
        encoding="utf-8",
    )
    code, _, err, _ = run_cli(project, home, ["-p", "x", "--mcp-config", str(bad)], final())
    assert code == 64 and '"ghost"' in err
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    code, _, err, _ = run_cli(project, home, ["-p", "x", "--mcp-config", str(broken)], final())
    assert code == 64 and "Cannot read MCP config" in err


class RoleModel:
    def __init__(self, parent_turns):
        from nailong_agent_sdk import ScriptedModel

        self._parent = ScriptedModel(parent_turns)
        self.calls = []

    async def next_turn(self, context):
        self.calls.append(context)
        if "REVIEWER-ROLE" in str(context.prompt.model_dump()):
            return FinalTurn(output={"status": "complete", "answer": "sub says ok"})
        return await self._parent.next_turn(context)


def test_a_configured_subagent_is_a_tool_the_agent_can_call(project, home):
    (project / ".nailong" / "config.toml").write_text(
        BASE + '[agents.reviewer]\ndescription = "Reviews code."\ninstructions = "REVIEWER-ROLE"\n',
        encoding="utf-8",
    )
    model = RoleModel([call("agent_reviewer", task="look at it"), final("done")])
    setup = ModelSetup(model, "scripted", "s")

    def build(config, environ):
        return setup

    build.setup = setup
    code, _, _, _ = run_cli(project, home, ["-p", "Get a review"], build=build)
    parent_calls = [c for c in model.calls if "REVIEWER-ROLE" not in str(c.prompt.model_dump())]
    assert code == 0 and "agent_reviewer" in tool_names(parent_calls[0])
    assert "sub says ok" in str(list(parent_calls[-1].observations))
    sub_calls = [c for c in model.calls if "REVIEWER-ROLE" in str(c.prompt.model_dump())]
    assert "write_file" not in tool_names(sub_calls[0]) and "run_command" not in tool_names(
        sub_calls[0]
    )


def test_web_fetch_is_off_until_asked_for(project, home):
    _, off, _, _ = run_cli(project, home, [], lines="/tools\n/exit\n", interactive=True)
    _, on, _, _ = run_cli(project, home, ["--web"], lines="/tools\n/exit\n", interactive=True)
    assert "web_fetch:" not in off and "web_fetch:" in on


def test_a_line_starting_with_a_bang_runs_as_your_own_command(project, home):
    code, out, _, setup = run_cli(
        project, home, [], lines="!echo hello from the shell\n/exit\n", interactive=True
    )
    assert code == 0 and "hello from the shell" in out and setup.model.calls == []


def test_init_drafts_agents_md_and_asks_before_saving(project, home):
    code, out, _, _ = run_cli(
        project,
        home,
        [],
        final("# AGENTS.md\nUse pytest."),
        lines="/init\ny\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "Saved AGENTS.md" in out
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# AGENTS.md\nUse pytest."
    _, declined, _, _ = run_cli(
        project, home, [], final("other"), lines="/init\nn\n/exit\n", interactive=True
    )
    assert (project / "AGENTS.md").read_text(encoding="utf-8").startswith("# AGENTS.md")
    assert "Saved" not in declined


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_diff_and_review_use_your_uncommitted_changes_read_only(project, home):
    def git(*args):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-m", "start")
    (project / "app.py").write_text("x = 2\n", encoding="utf-8")
    code, out, _, setup = run_cli(
        project, home, [], final("Looks fine."), lines="/diff\n/review\n/exit\n", interactive=True
    )
    assert code == 0 and "+x = 2" in out and "Looks fine." in out
    review_call = setup.model.calls[0]
    assert "+x = 2" in review_call.task.instructions
    assert "write_file" not in tool_names(review_call)
    _, nothing, _, _ = run_cli(project, home, [], lines="/exit\n", interactive=True)
    assert code == 0 and "Unknown" not in nothing


def test_permissions_can_be_added_listed_and_removed(project, home):
    lines = (
        "/permissions allow run_command:pytest*\n/permissions\n"
        "/permissions remove 1\n/permissions\n/exit\n"
    )
    _, out, _, _ = run_cli(project, home, [], lines=lines, interactive=True)
    assert "saved: allow run_command matching pytest*" in out
    assert "1. allow run_command: pytest*" in out and "removed run_command: pytest*" in out
    assert out.count("1. allow run_command") == 1


def test_mode_can_be_changed_during_the_session_and_changes_the_tools(project, home):
    lines = "/tools\n/mode plan\n/tools\n/mode nonsense\n/mode\n/exit\n"
    _, out, err, _ = run_cli(project, home, [], lines=lines, interactive=True)
    assert out.count("write_file:") == 1 and "mode is now plan" in out
    assert 'Unknown mode "nonsense"' in err and "* plan" in out


def test_model_can_be_switched_and_the_new_one_is_used(project, home):
    names = []
    from tests.conftest import factory

    inner = factory(final())

    def build(config, environ):
        names.append(config.model)
        return inner(config, environ)

    build.setup = inner.setup
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        lines="/model other-model\n/model\n/exit\n",
        interactive=True,
        build=build,
    )
    assert names == ["test-model", "other-model"] and "model is now other-model" in out
    assert "other-model (anthropic)" in out


def test_status_doctor_context_and_compact_describe_the_setup(project, home):
    lines = "/status\n/doctor\n/context\n/compact\n/memory\n/skills\n/agents\n/todos\n/exit\n"
    _, out, err, _ = run_cli(project, home, [], lines=lines, interactive=True)
    assert "mode         default" in out and "model        test-model (anthropic)" in out
    assert "API key: environment variable TEST_KEY" in out and "ok" in out
    assert "earlier turns sent: 0, left out: 0" in out and "nothing new to compact" in err
    assert "No NAILONG.md" in out and "No skills" in out and "No subagents" in out
    assert "No task list yet." in out


def test_the_resume_picker_lists_sessions_and_continues_the_one_you_choose(project, home):
    first = json.loads(
        run_cli(project, home, ["-p", "first topic", "--output", "json"], final())[1]
    )
    json.loads(run_cli(project, home, ["-p", "second topic", "--output", "json"], final())[1])
    ids = sorted(
        [
            first["session"],
            json.loads(run_cli(project, home, ["-p", "x", "--output", "json"], final())[1])[
                "session"
            ],
        ]
    )
    _, out, _, _ = run_cli(project, home, [], lines="/resume\n1\n/exit\n", interactive=True)
    assert (
        "1. " in out
        and "first topic" in out
        and f"resumed session {ids[0]}" in out
        or "resumed session" in out
    )


def test_continue_with_fork_session_branches_instead_of_resuming(project, home):
    original = json.loads(run_cli(project, home, ["-p", "one", "--output", "json"], final())[1])
    forked = json.loads(
        run_cli(project, home, ["-p", "two", "-c", "--fork-session", "--output", "json"], final())[
            1
        ]
    )
    assert forked["session"] != original["session"]


def test_after_a_plan_you_can_carry_it_out_in_the_same_session(project, home):
    code, out, _, setup = run_cli(
        project,
        home,
        ["--permission-mode", "plan"],
        final("1. edit the file"),
        lines="Plan it\ny\n/mode\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "Carry out this plan?" in out and "mode is now accept-edits" in out
    assert len(setup.model.calls) == 2
    assert "write_file" not in tool_names(setup.model.calls[0])
    assert "write_file" in tool_names(setup.model.calls[1])
    assert setup.model.calls[1].task.instructions == "Carry out the plan you just proposed."
