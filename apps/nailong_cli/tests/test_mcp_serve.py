from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from nailong_agent_sdk.mcp.client import McpClientManager
from nailong_agent_sdk.mcp.client_types import McpStdioServerConfig

from nailong_cli.cli import EXIT_USAGE


def server_args(project, *extra):
    return ["-m", "nailong_cli", "--mcp-serve", "--project", str(project), *extra]


def run_with_server(project, home, extra, work):
    config = McpStdioServerConfig(
        name="nailong",
        command=sys.executable,
        args=server_args(project, *extra),
        env={"USERPROFILE": str(home), "HOME": str(home)},
        connect_timeout_seconds=60,
    )

    async def session():
        manager = McpClientManager([config])
        await manager.connect_all()
        try:
            return await work(manager)
        finally:
            await manager.close()

    return asyncio.run(session())


def tool_names(manager):
    return {item.name for item in manager.list_tools()}


def test_by_default_only_the_reading_tools_are_offered_and_they_work(project, home):
    (project / "a.txt").write_text("hello from the project\n", encoding="utf-8")

    async def work(manager):
        text = await manager.call_tool("nailong", "read_file", {"path": "a.txt"})
        return tool_names(manager), text

    names, text = run_with_server(project, home, [], work)
    assert "read_file" in names
    assert not names & {"write_file", "edit_file", "run_command", "notebook_edit"}
    assert "hello from the project" in text


def test_accept_edits_offers_edits_but_still_protects_the_settings_folder(project, home):
    async def work(manager):
        await manager.call_tool("nailong", "write_file", {"path": "new.txt", "content": "made"})
        try:
            await manager.call_tool(
                "nailong", "write_file", {"path": ".nailong/config.toml", "content": "x"}
            )
        except Exception as error:
            refused = str(error)
        else:
            refused = ""
        return tool_names(manager), refused

    names, refused = run_with_server(project, home, ["--permission-mode", "accept-edits"], work)
    assert "write_file" in names and "run_command" not in names
    assert (project / "new.txt").read_text(encoding="utf-8") == "made"
    assert refused and "provider" in (project / ".nailong" / "config.toml").read_text("utf-8")


def test_command_modes_are_refused_without_a_sandbox_unless_you_say_so(project, home):
    finished = subprocess.run(
        [sys.executable, *server_args(project, "--permission-mode", "auto")],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "USERPROFILE": str(home), "HOME": str(home)},
        stdin=subprocess.DEVNULL,
    )
    assert finished.returncode == EXIT_USAGE
    assert "commands are not sandboxed" in finished.stderr
