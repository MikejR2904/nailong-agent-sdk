"""Permission modes and the rule syntax shared by flags and commands."""

from __future__ import annotations

from dataclasses import dataclass

from .config import MODES

EDIT_TOOLS = frozenset({"write_file", "edit_file", "notebook_edit", "apply_patch"})
MODE_HELP = {
    "default": "ask before every write and command",
    "accept-edits": "writes and edits go through; commands still ask",
    "auto": "edits and read-only commands go through; anything else asks",
    "plan": "read-only: the agent investigates and proposes a plan, nothing is changed",
    "bypass": "nothing asks (needs a sandbox, or --allow-unsandboxed)",
}


@dataclass
class ModeRef:
    value: str = "default"

    def set(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f'Unknown mode "{mode}"; the modes are {", ".join(MODES)}.')
        self.value = mode


def parse_rule(text: str) -> tuple[str, str]:
    tool, _, pattern = text.partition(":")
    if not tool.strip():
        raise ValueError(f'Rule "{text}" needs a tool name, for example run_command:pytest*.')
    return tool.strip(), pattern.strip() or "*"
