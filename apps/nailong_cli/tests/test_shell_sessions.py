from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest
from nailong_agent_sdk.tools.process_freeze import FREEZER
from nailong_agent_sdk.tools.supervisor import listening_to_command_output

from nailong_cli import shell_sessions
from nailong_cli.config import ConfigError, load_config
from nailong_cli.notes import NotesStore
from nailong_cli.shell_sessions import ShellSessions
from tests.conftest import write_user_config
from tests.test_sdk_features import BASE, user_only_project

WINDOWS = os.name == "nt"
PY = f'"{sys.executable}"'


def sessions(tmp_path, hosts=()):
    return ShellSessions(tmp_path, None, None, hosts)


def set_variable(name, value):
    return f"set {name}={value}" if WINDOWS else f"export {name}={value}"


def show_variable(name):
    return f"echo %{name}%" if WINDOWS else f"echo ${name}"


def test_directory_and_variables_persist_between_commands(tmp_path):
    (tmp_path / "sub").mkdir()

    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            await shells.run("work", "cd sub", 30)
            await shells.run("work", set_variable("NL_TEST", "kept"), 30)
            return await shells.run("work", show_variable("NL_TEST"), 30)
        finally:
            await shells.close()

    there = asyncio.run(scenario())
    assert there["output"] == "kept" and there["directory"].endswith("sub")
    assert there["exit_code"] == 0


def test_exit_codes_and_clean_output_come_back_per_command(tmp_path):
    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            hello = await shells.run("work", "echo hello", 30)
            failed = await shells.run("work", f'{PY} -c "import sys; sys.exit(3)"', 30)
            return hello, failed
        finally:
            await shells.close()

    hello, failed = asyncio.run(scenario())
    assert hello["output"] == "hello" and hello["exit_code"] == 0
    assert failed["exit_code"] == 3


def test_a_command_that_outlasts_its_limit_closes_the_shell_and_says_what_was_lost(tmp_path):
    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            with pytest.raises(ValueError, match="did not finish within 1s.*was closed.*lost"):
                await shells.run("work", f'{PY} -c "import time; time.sleep(30)"', 1)
            with pytest.raises(ValueError, match='Shell "work" ended'):
                await shells.run("work", "echo again", 5)
        finally:
            await shells.close()

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 25


def test_a_command_that_ends_the_shell_is_reported_not_hung(tmp_path):
    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            with pytest.raises(ValueError, match="ended while the command ran"):
                await shells.run("work", "exit 4", 10)
        finally:
            await shells.close()

    asyncio.run(scenario())


def test_output_reaches_the_live_listener_before_the_command_finishes(tmp_path):
    code = "import time; print('first', flush=True); time.sleep(0.8); print('second', flush=True)"
    seen: list[tuple[float, str]] = []

    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            with listening_to_command_output(lambda text: seen.append((time.monotonic(), text))):
                finished = await shells.run("work", f'{PY} -c "{code}"', 30)
            return finished, time.monotonic()
        finally:
            await shells.close()

    finished, ended = asyncio.run(scenario())
    first = next(at for at, text in seen if "first" in text)
    assert ended - first >= 0.5 and finished["output"].splitlines() == ["first", "second"]


def test_pausing_freezes_the_running_command_and_its_time_limit_waits(tmp_path):
    counter = tmp_path / "count.txt"
    script = tmp_path / "count.py"
    script.write_text(
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{counter}')\n"
        "for i in range(20):\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )

    def position():
        for _ in range(20):
            try:
                return int(counter.read_text())
            except (OSError, ValueError):
                time.sleep(0.01)
        return -1

    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            task = asyncio.create_task(shells.run("work", f'{PY} "{script}"', 3.5))
            deadline = time.monotonic() + 20
            while position() < 3 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            frozen = FREEZER.freeze()
            await asyncio.sleep(0.3)
            held = position()
            await asyncio.sleep(2.5)
            same = position()
            FREEZER.thaw()
            return frozen, held, same, await task
        finally:
            await shells.close()

    frozen, held, same, result = asyncio.run(scenario())
    assert frozen == ["shell:work"] and held == same
    assert result["exit_code"] == 0 and position() == 19


def test_an_idle_shell_is_not_frozen_by_a_general_pause(tmp_path):
    async def scenario():
        shells = sessions(tmp_path)
        try:
            await shells.open("work", None)
            return FREEZER.freeze()
        finally:
            await shells.close()

    assert asyncio.run(scenario()) == []


def test_names_and_duplicates_are_checked_with_the_reason(tmp_path):
    async def scenario():
        shells = sessions(tmp_path)
        try:
            with pytest.raises(ValueError, match="must be 1 to 32 letters"):
                await shells.open("bad name!", None)
            await shells.open("a", None)
            with pytest.raises(ValueError, match='shell named "a" is already open'):
                await shells.open("a", None)
            with pytest.raises(ValueError, match='There is no shell "zzz"'):
                await shells.run("zzz", "echo", 5)
        finally:
            await shells.close()

    asyncio.run(scenario())


def test_remote_shells_are_off_until_hosts_are_listed_and_only_listed_hosts_work(tmp_path):
    async def attempt(shells, host):
        try:
            return await shells.open("r", host)
        finally:
            await shells.close()

    with pytest.raises(ValueError, match="Remote shells are off.*ssh_hosts"):
        asyncio.run(attempt(sessions(tmp_path), "admin@server1"))
    listed = sessions(tmp_path, ("admin@server1",))
    with pytest.raises(ValueError, match='Host "other" is not in ssh_hosts'):
        asyncio.run(attempt(listed, "other"))
    with pytest.raises(ValueError, match="is not in ssh_hosts"):
        asyncio.run(attempt(listed, "-oProxyCommand=evil"))


def test_the_ssh_command_never_asks_for_a_password_and_ends_options_before_the_host(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shell_sessions, "_which", lambda binary: binary)
    shells = sessions(tmp_path, ("admin@server1",))
    argv, env = shells._ssh_launch("admin@server1")
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and "-T" in argv
    assert argv[-2:] == ["--", "admin@server1"]
    if not WINDOWS:
        assert "ControlMaster=auto" in argv and "ControlPersist=10m" in argv
    assert "OPENROUTER_API_KEY" not in env
    asyncio.run(shells.close())


def test_notes_are_saved_appended_listed_and_bounded(tmp_path):
    notes = NotesStore(tmp_path / "notes")
    assert notes.read(None) == {"notes": []}
    notes.write("server migration", "goal: move db", append=False)
    notes.write("server migration", "done: backup", append=True)
    assert notes.read("server migration")["text"] == "goal: move db\ndone: backup\n"
    assert notes.read(None) == {"notes": ["server migration"]}
    with pytest.raises(ValueError, match="must be 1 to 64 letters"):
        notes.write("../escape", "x", append=False)
    with pytest.raises(ValueError, match="limit is 20000"):
        notes.write("big", "x" * 20_001, append=False)
    with pytest.raises(ValueError, match='There is no note "nope"'):
        notes.read("nope")


@pytest.mark.parametrize("value", ['"admin@x"', '["-oProxy"]', '["a b"]', "[1]"])
def test_a_bad_ssh_hosts_setting_is_named(project, home, value):
    with pytest.raises(ConfigError, match="ssh_hosts must be a list of host names"):
        user_only_project(project, home, f"ssh_hosts = {value}\n")


def test_ssh_hosts_is_a_user_setting_a_project_file_cannot_change(project, home):
    write_user_config(home, BASE + 'ssh_hosts = ["admin@server1"]\n')
    (project / ".nailong" / "config.toml").write_text(
        'ssh_hosts = ["evil@host"]\n', encoding="utf-8"
    )
    config = load_config(project, home=home)
    assert config.ssh_hosts == ("admin@server1",) and "ssh_hosts" in config.ignored_project_keys


def test_the_agent_can_open_a_shell_use_it_twice_and_the_session_is_listed(project, home):
    from tests.conftest import call, final
    from tests.test_terminal_ui import run_fancy

    (project / "sub").mkdir()
    code, out, err = run_fancy(
        project,
        home,
        call("shell_open", name="w"),
        call("shell_run", session="w", command="cd sub"),
        call("shell_run", session="w", command="cd"),
        final("done"),
        lines="Use a shell\n/jobs\n/exit\n",
        argv=["--permission-mode", "bypass", "--allow-unsandboxed"],
    )
    assert code == 0, err
    assert "shell_open" in out and "shell_run" in out
    assert "shell:w" in out and "idle" in out
