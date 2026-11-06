"""Who is editing which file, so tasks running side by side cannot overwrite each other.

A task that edits a file first claims it. A claim held by another task that is still alive makes
the edit fail with a message naming the holder, so the agent can do a different part of its work
first. Claims are released when the task ends; a claim left by a process that died is ignored.
The main agent never holds claims, but it is stopped from editing a file another task holds.
"""

from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import ToolEvent

from .storage import atomic_write, exclusive
from .taskstate import pid_alive

EDIT_TOOLS = ("write_file", "edit_file", "notebook_edit", "apply_patch")
LOCK_WAIT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0
current_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nailong_edit_owner", default=None
)


class ArtifactClaims:
    def __init__(self, directory: Path, project: Path) -> None:
        self._file = directory / "claims.json"
        self._project = project.resolve()

    def _key(self, path: str) -> str:
        target = (self._project / path).resolve()
        try:
            return target.relative_to(self._project).as_posix().lower()
        except ValueError:
            return target.as_posix().lower()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._file.is_file():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return {key: value for key, value in data.items() if pid_alive(int(value.get("pid", 0)))}

    def _locked(self):
        return exclusive(
            self._file.with_suffix(".lock"),
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="claims",
        )

    def claim(self, owner: str, path: str) -> None:
        key = self._key(path)
        with self._locked():
            claims = self._read()
            held = claims.get(key)
            if held is not None and held["owner"] != owner:
                raise ValueError(
                    f"{path} is being edited by {held['owner']}; leave it to them, or do a "
                    "different part of your task first and come back."
                )
            claims[key] = {"owner": owner, "pid": os.getpid()}
            atomic_write(self._file, json.dumps(claims))

    def check(self, owner: str, path: str) -> None:
        held = self._read().get(self._key(path))
        if held is not None and held["owner"] != owner:
            raise ValueError(f"{path} is being edited by {held['owner']}; wait for them to finish.")

    def release(self, owner: str) -> list[str]:
        with self._locked():
            claims = self._read()
            freed = [key for key, value in claims.items() if value["owner"] == owner]
            for key in freed:
                del claims[key]
            atomic_write(self._file, json.dumps(claims))
        return freed

    def holders(self) -> dict[str, str]:
        return {key: value["owner"] for key, value in self._read().items()}

    async def before_tool(self, event: ToolEvent) -> str | None:
        if event.tool not in EDIT_TOOLS:
            return None
        from .rules import subject_for

        owner = current_owner.get()
        paths = [line for line in subject_for(event.tool, event.arguments).splitlines() if line]
        try:
            for path in paths:
                if owner is None:
                    self.check("main", path)
                else:
                    self.claim(owner, path)
        except ValueError as error:
            return str(error)
        return None
