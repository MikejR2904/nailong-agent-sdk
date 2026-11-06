"""apply_patch: change several files in one step from a patch the model writes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.tools.policy import sensitive_pattern_for

MAX_PATCH_CHARS = 200_000
MAX_FILES = 50
BEGIN = "*** Begin Patch"
END = "*** End Patch"
ADD = "*** Add File: "
UPDATE = "*** Update File: "
DELETE = "*** Delete File: "
MOVE = "*** Move to: "
END_OF_FILE = "*** End of File"


@dataclass
class Hunk:
    lines: list[tuple[str, str]] = field(default_factory=list)
    hint: str = ""


@dataclass
class Operation:
    kind: str
    path: str
    move_to: str | None = None
    added: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


def patch_paths(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        for prefix in (ADD, UPDATE, DELETE, MOVE):
            if line.startswith(prefix):
                found.append(line[len(prefix) :].strip())
    return found


def parse_patch(text: str) -> list[Operation]:
    lines = text.strip("\n").splitlines()
    if not lines or lines[0].strip() != BEGIN:
        raise ValueError(f'The patch must start with "{BEGIN}" on its first line.')
    if lines[-1].strip() != END:
        raise ValueError(f'The patch must end with "{END}" on its last line.')
    operations: list[Operation] = []
    current: Operation | None = None
    hunk: Hunk | None = None
    for number, line in enumerate(lines[1:-1], start=2):
        if line.startswith(ADD):
            current, hunk = Operation("add", line[len(ADD) :].strip()), None
            operations.append(current)
        elif line.startswith(DELETE):
            current, hunk = Operation("delete", line[len(DELETE) :].strip()), None
            operations.append(current)
        elif line.startswith(UPDATE):
            current, hunk = Operation("update", line[len(UPDATE) :].strip()), None
            operations.append(current)
        elif line.startswith(MOVE):
            if current is None or current.kind != "update":
                raise ValueError(f'Line {number}: "{MOVE}" must follow an "{UPDATE.strip()}" line.')
            current.move_to = line[len(MOVE) :].strip()
        elif line.strip() == END_OF_FILE:
            continue
        elif current is None:
            raise ValueError(f"Line {number}: expected a *** file header, found {line!r}.")
        elif current.kind == "add":
            if not line.startswith("+"):
                raise ValueError(f"Line {number}: lines of a new file must start with +.")
            current.added.append(line[1:])
        elif current.kind == "update":
            if line.startswith("@@"):
                hunk = Hunk(hint=line[2:].strip())
                current.hunks.append(hunk)
                continue
            if hunk is None:
                hunk = Hunk()
                current.hunks.append(hunk)
            marker = line[:1]
            if marker not in {" ", "+", "-"}:
                if line == "":
                    marker, line = " ", " "
                else:
                    raise ValueError(
                        f"Line {number}: a change line must start with a space, + or -, "
                        f"found {line!r}."
                    )
            hunk.lines.append((marker, line[1:]))
        else:
            raise ValueError(f"Line {number}: a deleted file takes no further lines.")
    if not operations:
        raise ValueError("The patch contains no file operations.")
    if len(operations) > MAX_FILES:
        raise ValueError(f"The patch touches {len(operations)} files; the limit is {MAX_FILES}.")
    for operation in operations:
        if operation.kind == "update" and not operation.hunks and not operation.move_to:
            raise ValueError(f"The update of {operation.path} has no changes.")
    return operations


def _matches(file_lines: list[str], start: int, wanted: list[str], loose: bool) -> bool:
    if start + len(wanted) > len(file_lines):
        return False
    for offset, text in enumerate(wanted):
        have = file_lines[start + offset]
        if (have.rstrip() != text.rstrip()) if loose else (have != text):
            return False
    return True


def _apply_hunks(path: str, text: str, hunks: list[Hunk]) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    ended = text.endswith(("\n", "\r\n"))
    lines = text.replace("\r\n", "\n").split("\n")
    if ended:
        lines.pop()
    cursor = 0
    for number, hunk in enumerate(hunks, start=1):
        old = [content for kind, content in hunk.lines if kind != "+"]
        new = [content for kind, content in hunk.lines if kind != "-"]
        where = None
        for loose in (False, True):
            for index in range(cursor, len(lines) - len(old) + 1):
                if _matches(lines, index, old, loose):
                    where = index
                    break
            if where is not None:
                break
        if where is None:
            first = old[0] if old else "(nothing to match)"
            hint = f" near {hunk.hint!r}" if hunk.hint else ""
            raise ValueError(
                f"Hunk {number} of {path} does not match the file{hint}: the first line it "
                f"expects is {first!r}. Read the file again and rewrite the hunk from its "
                "current text."
            )
        lines[where : where + len(old)] = new
        cursor = where + len(new)
    result = newline.join(lines)
    return result + newline if ended or not lines else result


def plan_patch(
    project: Path, operations: list[Operation], protected: tuple[str, ...]
) -> dict[Path, str | None]:
    root = project.resolve()

    def resolve(relative: str) -> Path:
        candidate = Path(relative)
        if not relative.strip() or candidate.is_absolute():
            raise ValueError(f"Patch path {relative!r} must be relative to the project.")
        target = (root / candidate).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Patch path {relative!r} leaves the project.")
        top = target.relative_to(root).parts[0] if target != root else ""
        if top in protected:
            raise ValueError(
                f"Patch path {relative!r} is in {top}, which the agent may not change."
            )
        if sensitive_pattern_for(target) is not None:
            raise ValueError(f"Patch path {relative!r} is a denied credential path.")
        return target

    planned: dict[Path, str | None] = {}
    for operation in operations:
        target = resolve(operation.path)
        current = planned[target] if target in planned else _read(target)
        if operation.kind == "add":
            if current is not None or (target.exists() and target not in planned):
                raise ValueError(f"{operation.path} already exists, so it cannot be added.")
            planned[target] = "\n".join(operation.added) + ("\n" if operation.added else "")
        elif operation.kind == "delete":
            if current is None:
                raise ValueError(f"{operation.path} does not exist, so it cannot be deleted.")
            planned[target] = None
        else:
            if current is None:
                raise ValueError(f"{operation.path} does not exist, so it cannot be updated.")
            updated = _apply_hunks(operation.path, current, operation.hunks)
            destination = resolve(operation.move_to) if operation.move_to else target
            if destination != target:
                if (
                    destination in planned
                    and planned[destination] is not None
                    or (destination.exists() and destination not in planned)
                ):
                    raise ValueError(
                        f"{operation.move_to} already exists, so nothing can move there."
                    )
                planned[target] = None
            planned[destination] = updated
    return planned


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8")


def apply_planned(planned: dict[Path, str | None]) -> None:
    for path, content in planned.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".nailong-tmp")
            temporary.write_bytes(content.encode("utf-8"))
            os.replace(temporary, path)


def patch_tool(project: Path, protected: tuple[str, ...] = ()) -> FunctionTool:
    def apply_patch(patch: str) -> dict[str, Any]:
        """Change one or more files at once with a patch.

        The patch starts with "*** Begin Patch" and ends with "*** End Patch". Between them each
        file has a header: "*** Add File: path" (every line starting with +), "*** Delete File:
        path", or "*** Update File: path" (optionally followed by "*** Move to: new/path").
        An update holds hunks: a line starting "@@" (optionally naming the nearby function), then
        lines that start with a space (unchanged context), - (remove) or + (add). Give a few
        lines of context so each hunk matches only one place. Either every file changes or none.
        """
        if len(patch) > MAX_PATCH_CHARS:
            raise ValueError(
                f"The patch has {len(patch)} characters; the limit is {MAX_PATCH_CHARS}."
            )
        operations = parse_patch(patch)
        planned = plan_patch(project, operations, protected)
        apply_planned(planned)
        root = project.resolve()
        return {
            "changed": [
                f"{'deleted' if content is None else 'wrote'} {path.relative_to(root).as_posix()}"
                for path, content in planned.items()
            ]
        }

    return tool(apply_patch, needs_approval=True)
