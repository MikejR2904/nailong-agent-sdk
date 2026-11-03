"""A short description of the machine the agent is working on, so it does not guess."""

from __future__ import annotations

import platform
import shutil
import sys
from datetime import date
from pathlib import Path


def environment_note(project: Path, *, sandboxed: bool, today: date | None = None) -> str:
    windows = sys.platform == "win32"
    if windows:
        shell = (
            "Windows cmd.exe (not bash): there are no heredocs, `head`, `grep`, `ls` or `find`; "
            "use `dir`, `findstr` and `type`, or write a Python script to a file and run it"
        )
    else:
        shell = "a POSIX shell (sh)"
    python = next((name for name in ("python3", "python") if shutil.which(name)), None)
    lines = [
        "Environment:",
        f"- Working directory: {project}",
        f"- Platform: {platform.system() or sys.platform} {platform.release()}".rstrip(),
        f"- Commands run in {shell}",
        f"- Python is available as `{python}`" if python else "- Python was not found on PATH",
        f"- Git is {'available' if shutil.which('git') else 'not available'}",
        f"- Commands are {'sandboxed' if sandboxed else 'not sandboxed'}; output is cut off when "
        "long, so write large results to a file and read the part you need",
        f"- Today is {(today or date.today()).isoformat()}",
    ]
    return "\n".join(lines)
