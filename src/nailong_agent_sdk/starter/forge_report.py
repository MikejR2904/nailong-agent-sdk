# Copyright (c) 2026 David Michael Indraputra

"""What a proposed tool's source reaches for, as a note for the reviewer (not a security check)."""

from __future__ import annotations

import ast
import difflib
from typing import Any

_NETWORK = frozenset(
    {"socket", "ssl", "http", "urllib", "requests", "httpx", "ftplib", "smtplib", "aiohttp"}
)
_PROCESS = frozenset(
    {"subprocess", "os", "ctypes", "multiprocessing", "pty", "signal", "shutil", "pathlib"}
)
_DYNAMIC = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "globals", "vars"}
)


def code_report(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    imports: set[str] = set()
    dynamic: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DYNAMIC:
                dynamic.add(node.func.id)
    return {
        "imports": sorted(imports),
        "network_modules": sorted(imports & _NETWORK),
        "process_or_file_modules": sorted(imports & _PROCESS),
        "dynamic_calls": sorted(dynamic),
        "note": (
            "A static scan to help the reviewer read the code. It is not a security boundary: "
            "code can reach the same things in ways a scan does not see."
        ),
    }


def source_diff(old: str | None, new: str, old_label: str, new_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
        )
    )
