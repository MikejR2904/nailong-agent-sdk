"""Line editing and a history file, when the platform's readline is available."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

MAX_HISTORY = 1_000


def enable_history(home: Path) -> Callable[[], None]:
    if sys.platform == "win32":
        return lambda: None
    try:
        import readline
    except ImportError:
        return lambda: None
    path = home / ".local" / "state" / "nailong" / "history"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        readline.read_history_file(str(path))
    except OSError:
        pass
    readline.set_history_length(MAX_HISTORY)

    def save() -> None:
        try:
            readline.write_history_file(str(path))
        except OSError:
            pass

    return save
