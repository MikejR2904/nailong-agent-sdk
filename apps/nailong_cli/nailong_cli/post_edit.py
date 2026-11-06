"""After the agent edits a file: format it, then check it and tell the agent what is wrong."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import ToolEvent

from .apply_patch import parse_patch
from .console import Console

EDIT_TOOLS = ("write_file", "edit_file", "notebook_edit", "apply_patch")
RUN_SECONDS = 30
MAX_REPORT_CHARS = 3_000


@dataclass(frozen=True)
class Checker:
    name: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]


BUILTIN_FORMATTERS = (
    Checker("ruff", ("ruff", "format", "$FILE"), (".py",)),
    Checker(
        "prettier",
        ("prettier", "--write", "$FILE"),
        (".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".html", ".md", ".yaml", ".yml"),
    ),
    Checker("gofmt", ("gofmt", "-w", "$FILE"), (".go",)),
    Checker("rustfmt", ("rustfmt", "$FILE"), (".rs",)),
    Checker("shfmt", ("shfmt", "-w", "$FILE"), (".sh",)),
)
BUILTIN_DIAGNOSTICS = (
    Checker("ruff", ("ruff", "check", "--output-format", "concise", "$FILE"), (".py",)),
)


def checkers_from_table(table: dict[str, dict[str, Any]], kind: str) -> list[Checker]:
    found = []
    for name, spec in table.items():
        command, extensions = spec.get("command"), spec.get("extensions")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) for part in command)
        ):
            raise ValueError(f'{kind}.{name} needs command = ["program", "args", "$FILE"].')
        if not isinstance(extensions, list) or not extensions:
            raise ValueError(f'{kind}.{name} needs extensions = [".py", ...].')
        if spec.get("disabled"):
            continue
        found.append(Checker(name, tuple(command), tuple(str(item) for item in extensions)))
    return found


def _usable(checker: Checker, project: Path) -> bool:
    if shutil.which(checker.command[0]) is None:
        return False
    if checker.name == "prettier":
        package = project / "package.json"
        return package.is_file() and "prettier" in package.read_text(
            encoding="utf-8", errors="ignore"
        )
    return True


def changed_files(event: ToolEvent) -> list[str]:
    arguments = event.arguments
    if event.tool == "apply_patch":
        try:
            operations = parse_patch(str(arguments.get("patch", "")))
        except ValueError:
            return []
        return [
            (operation.move_to or operation.path)
            for operation in operations
            if operation.kind != "delete"
        ]
    path = arguments.get("path")
    return [str(path)] if path else []


class PostEdit:
    def __init__(
        self,
        project: Path,
        console: Console,
        *,
        formatters: list[Checker],
        diagnostics: list[Checker],
        builtin_format: bool,
        builtin_diagnostics: bool,
        current_agent: Callable[[], Any],
        lsp: Any = None,
    ) -> None:
        self._project = project.resolve()
        self._console = console
        self._current_agent = current_agent
        self._lsp = lsp
        self._formatters = [
            *(
                item
                for item in BUILTIN_FORMATTERS
                if builtin_format and _usable(item, self._project)
            ),
            *formatters,
        ]
        self._diagnostics = [
            *(
                item
                for item in BUILTIN_DIAGNOSTICS
                if builtin_diagnostics and _usable(item, self._project)
            ),
            *diagnostics,
        ]

    @property
    def lsp(self) -> Any:
        return self._lsp

    @property
    def active(self) -> bool:
        return bool(self._formatters or self._diagnostics or (self._lsp and self._lsp.active))

    def describe(self) -> str:
        names = [f"format {item.name}" for item in self._formatters]
        names += [f"check {item.name}" for item in self._diagnostics]
        if self._lsp is not None and self._lsp.active:
            names.append(f"lsp {self._lsp.describe()}")
        return ", ".join(names) or "none"

    async def after_tool(self, event: ToolEvent) -> None:
        result = event.result
        if event.tool not in EDIT_TOOLS or result is None or result.status != "succeeded":
            return
        reports: list[str] = []
        for relative in changed_files(event):
            path = (self._project / relative).resolve()
            if not path.is_relative_to(self._project) or not path.is_file():
                continue
            await self._format(path, relative)
            reports.extend(await self._check(path, relative))
            if self._lsp is not None and self._lsp.active:
                reports.extend(await asyncio.to_thread(self._lsp.diagnose, path))
        if reports:
            self._tell("\n".join(reports)[:MAX_REPORT_CHARS])

    def close(self) -> None:
        if self._lsp is not None:
            self._lsp.close()

    async def _format(self, path: Path, relative: str) -> None:
        for item in self._formatters:
            if path.suffix in item.extensions:
                code, output = await self._run(item, path)
                if code != 0:
                    self._console.line(
                        self._console.style(
                            f"  formatter {item.name} failed on {relative}: {output[:200]}", "dim"
                        )
                    )
                return

    async def _check(self, path: Path, relative: str) -> list[str]:
        found = []
        for item in self._diagnostics:
            if path.suffix in item.extensions:
                code, output = await self._run(item, path)
                if code != 0 and output.strip():
                    found.append(f"{item.name} on {relative}:\n{output.strip()}")
        return found

    async def _run(self, item: Checker, path: Path) -> tuple[int, str]:
        argv = [str(path) if part == "$FILE" else part for part in item.command]

        def work() -> tuple[int, str]:
            try:
                done = subprocess.run(
                    argv,
                    cwd=self._project,
                    capture_output=True,
                    text=True,
                    timeout=RUN_SECONDS,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return 1, f"{item.name} did not finish within {RUN_SECONDS}s"
            except OSError as error:
                return 1, f"{item.name} could not start: {type(error).__name__}: {error}"
            return done.returncode, done.stdout + done.stderr

        return await asyncio.to_thread(work)

    def _tell(self, text: str) -> None:
        agent = self._current_agent()
        if agent is None:
            return
        try:
            agent.say(f"Checks found problems after your edit:\n{text}")
        except ValueError as error:
            self._console.line(
                self._console.style(f"  could not pass the check results on: {error}", "dim")
            )
