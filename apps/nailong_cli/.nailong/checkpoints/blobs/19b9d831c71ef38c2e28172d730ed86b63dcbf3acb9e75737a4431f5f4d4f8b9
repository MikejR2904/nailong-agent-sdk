"""Giving the agent more than one directory: the file tools, the sandbox and the undo list."""

from __future__ import annotations

from pathlib import Path

from .build import choose_sandbox
from .state import ReplState
from .turns import switch_agent


def add_directory(state: ReplState, name: str, path: Path) -> None:
    if not path.is_dir():
        raise ValueError(f"The directory {path} does not exist.")
    if name == "project" or name in state.parts.extra_dirs:
        raise ValueError(f'The directory name "{name}" is already used; pass another name.')
    state.parts.extra_dirs[name] = path
    state.parts.checkpoints.add_root(name, path)
    state.parts.sandbox_kwargs, state.sandbox_label = choose_sandbox(
        state.config, state.parts.extra_dirs
    )
    switch_agent(state)
