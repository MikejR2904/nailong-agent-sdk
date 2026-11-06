"""Small utilities: an external editor for long prompts, usage statistics, shell completion."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .state import ReplState

SHELLS = ("bash", "zsh", "fish", "powershell")
DEFAULT_DAYS = 7
TOP_EVENTS = 6


def editor_command(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    if not console.interactive:
        console.line("  /editor opens your editor, so it needs a terminal.")
        return True
    editor = (
        state.environ.get("VISUAL")
        or state.environ.get("EDITOR")
        or ("notepad" if os.name == "nt" else "nano")
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8", prefix="nailong-"
    ) as handle:
        handle.write(" ".join(arguments))
        path = Path(handle.name)
    try:
        done = subprocess.run(f'{editor} "{path}"', shell=True, check=False)
        text = path.read_text(encoding="utf-8").strip()
    finally:
        path.unlink(missing_ok=True)
    if done.returncode != 0:
        raise ValueError(
            f"The editor ({editor}) exited with status {done.returncode}; nothing was sent."
        )
    if not text:
        console.line("  The editor was closed with nothing written, so nothing was sent.")
        return True
    from .turns import run_turn

    run_turn(state, text)
    return True


def stats_command(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    days = DEFAULT_DAYS
    if arguments:
        if not arguments[0].isdigit() or int(arguments[0]) < 1:
            raise ValueError("Usage: /stats [days]  (a whole number of days, 1 or more)")
        days = int(arguments[0])
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    runs = []
    for item in state.agent.services.telemetry.list_runs(limit=1_000):
        if item.started_at and datetime.fromisoformat(item.started_at) >= cutoff:
            runs.append(item)
    per_day: Counter[str] = Counter(item.started_at[:10] for item in runs if item.started_at)
    statuses: Counter[str] = Counter()
    events: Counter[str] = Counter()
    for item in runs:
        statuses.update(item.statuses)
        events.update(item.event_types)
    console.line(
        f"  last {days} day(s): {len(runs)} run(s), {len(state.agent.sessions())} session(s) saved"
    )
    for day in sorted(per_day):
        console.line(f"    {day}  {per_day[day]} run(s)")
    if statuses:
        console.line(
            "  event statuses: " + ", ".join(f"{k} {v}" for k, v in statuses.most_common())
        )
    if events:
        top = ", ".join(f"{name} {count}" for name, count in events.most_common(TOP_EVENTS))
        console.line(f"  most frequent events: {top}")
    spent = state.parts.spending.total
    console.line(
        f"  spent this session: {spent}" if spent else "  spent this session: nothing recorded"
    )
    return True


def _options(parser: argparse.ArgumentParser) -> list[tuple[list[str], str, bool]]:
    found = []
    for action in parser._actions:
        if action.option_strings:
            found.append((list(action.option_strings), action.help or "", action.nargs != 0))
    return found


def completion_script(shell: str, parser: argparse.ArgumentParser) -> str:
    options = _options(parser)
    flat = " ".join(sorted({flag for flags, _, _ in options for flag in flags}))
    if shell == "bash":
        return f'complete -W "{flat}" nailong\n'
    if shell == "zsh":
        lines = ["#compdef nailong", "_arguments \\"]
        for flags, text, takes in options:
            clean = text.replace("'", "").replace("[", "(").replace("]", ")")[:70]
            spec = "{" + ",".join(flags) + "}" if len(flags) > 1 else flags[0]
            lines.append(f"  '{spec}[{clean}]{':value:' if takes else ''}' \\")
        lines.append("  '*:message:'")
        return "\n".join(lines) + "\n"
    if shell == "fish":
        lines = []
        for flags, text, takes in options:
            parts = ["complete -c nailong"]
            for flag in flags:
                parts.append(f"-l {flag[2:]}" if flag.startswith("--") else f"-s {flag[1:]}")
            if takes:
                parts.append("-r")
            parts.append("-d '" + text.replace("'", "")[:70] + "'")
            lines.append(" ".join(parts))
        return "\n".join(lines) + "\n"
    if shell == "powershell":
        words = ", ".join(f"'{flag}'" for flag in sorted({f for fs, _, _ in options for f in fs}))
        return (
            "Register-ArgumentCompleter -Native -CommandName nailong -ScriptBlock {\n"
            "    param($wordToComplete)\n"
            f'    @({words}) | Where-Object {{ $_ -like "$wordToComplete*" }} |\n'
            "        ForEach-Object { [System.Management.Automation.CompletionResult]::new("
            "$_, $_, 'ParameterName', $_) }\n}\n"
        )
    raise ValueError(f"Unknown shell {shell!r}; choose one of {list(SHELLS)}.")


def upgrade_hint() -> list[str]:
    prefix = Path(sys.prefix).as_posix().lower()
    if "/uv/tools/" in prefix or "/uv/tool" in prefix:
        return ["Installed with uv tool. Upgrade with:", "  uv tool upgrade nailong-cli"]
    if "/pipx/" in prefix:
        return ["Installed with pipx. Upgrade with:", "  pipx upgrade nailong-cli"]
    return [
        "Upgrade with the tool you installed it with, for example:",
        "  uv tool upgrade nailong-cli      (if you used uv tool install)",
        "  pipx upgrade nailong-cli         (if you used pipx)",
        "  python -m pip install -U nailong-cli",
    ]
