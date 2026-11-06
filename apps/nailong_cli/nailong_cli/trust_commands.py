from __future__ import annotations

from pathlib import Path

from . import project_trust
from .config import _read, project_config_path


def review_project_settings(console, home: Path, project: Path) -> int:
    path = project_config_path(project)
    shared = project_trust.shared_part(_read(path))
    if not shared:
        console.line("  This project's settings add no hooks or MCP servers.")
        return 0
    if project_trust.is_trusted(home, path, shared):
        console.line("  You already approved these hooks and MCP servers:")
        for line in project_trust.describe(shared):
            console.line(f"    {line}")
        return 0
    if not console.interactive:
        console.error(
            f"{path} adds hooks or MCP servers; approving them needs a terminal so you can read "
            "them first. Run nailong --trust-project in one."
        )
        return 64
    console.line(console.style(f"  {path} would run these programs with your authority:", "bold"))
    for line in project_trust.describe(shared):
        console.line(f"    {line}")
    answer = console.ask(
        "  Trust them for this project? They are re-checked if the file changes. [y/N] "
    )
    if answer.lower() not in {"y", "yes"}:
        console.line("  Not trusted; they stay ignored.")
        return 0
    project_trust.trust(home, path, shared)
    console.line("  Trusted. They load the next time nailong starts in this project.")
    return 0


def trust_project_command(state, arguments: list[str]) -> bool:
    review_project_settings(state.console, state.home, state.project)
    return True
