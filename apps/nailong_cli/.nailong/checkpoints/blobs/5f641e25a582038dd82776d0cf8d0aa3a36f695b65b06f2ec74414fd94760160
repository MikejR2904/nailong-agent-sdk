"""Reading a shell command well enough to match approval rules safely and spot read-only ones."""

from __future__ import annotations

import fnmatch
import shlex
from pathlib import PurePath

GIT_READ_ONLY = frozenset(
    {"status", "diff", "log", "show", "rev-parse", "ls-files", "blame", "describe"}
)
GIT_BRANCH_FLAGS = frozenset({"-a", "-v", "-vv", "-r", "--list", "--show-current"})
READ_ONLY_PROGRAMS = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "type",
        "head",
        "tail",
        "wc",
        "pwd",
        "echo",
        "grep",
        "rg",
        "which",
        "where",
        "tree",
        "stat",
        "file",
        "du",
        "df",
        "whoami",
        "date",
        "diff",
        "uniq",
        "cut",
        "basename",
        "dirname",
        "realpath",
    }
)
FIND_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
)


def split_command(command: str) -> list[str] | None:
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    length = len(command)

    def flush() -> None:
        text = "".join(current).strip()
        if text:
            segments.append(text)
        current.clear()

    while index < length:
        char = command[index]
        if quote == "'":
            current.append(char)
            if char == "'":
                quote = ""
            index += 1
            continue
        if char == "\\":
            current.append(command[index : index + 2])
            index += 2
            continue
        if quote == '"':
            if char == "`" or command.startswith("$(", index):
                return None
            if char == '"':
                quote = ""
            current.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
        elif char == "`" or command.startswith(("$(", "<(", ">("), index) or char in "<>":
            return None
        elif char in ";\n&|":
            flush()
        else:
            current.append(char)
        index += 1
    if quote:
        return None
    flush()
    return segments


def allowed_by(patterns: list[str], tool: str, subject: str) -> bool:
    if tool != "run_command":
        return any(fnmatch.fnmatchcase(subject, pattern) for pattern in patterns)
    if subject in patterns:
        return True
    segments = split_command(subject)
    if not segments:
        return False
    return all(
        any(fnmatch.fnmatchcase(segment, pattern) for pattern in patterns) for segment in segments
    )


def denied_by(patterns: list[str], tool: str, subject: str) -> bool:
    candidates = [subject]
    if tool == "run_command":
        candidates += split_command(subject) or []
    return any(fnmatch.fnmatchcase(text, pattern) for text in candidates for pattern in patterns)


def is_read_only_command(command: str) -> bool:
    segments = split_command(command)
    if not segments:
        return False
    return all(_segment_is_read_only(segment) for segment in segments)


def _segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    program = PurePath(tokens[0]).name.lower()
    rest = tokens[1:]
    if program == "git":
        return _git_is_read_only(rest)
    if program == "find":
        return not FIND_WRITES.intersection(rest)
    if program == "sort":
        return not any(
            token in {"-o", "--output"} or token.startswith("--output=") for token in rest
        )
    return program in READ_ONLY_PROGRAMS


def _git_is_read_only(arguments: list[str]) -> bool:
    if not arguments:
        return False
    subcommand, rest = arguments[0], arguments[1:]
    if any(token.startswith("--output") for token in rest):
        return False
    if subcommand == "branch":
        return all(token in GIT_BRANCH_FLAGS for token in rest)
    return subcommand in GIT_READ_ONLY
