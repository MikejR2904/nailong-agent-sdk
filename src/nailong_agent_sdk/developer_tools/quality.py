# Copyright (c) 2026 David Michael Indraputra

"""Deterministic static-quality checks for an Agent SDK checkout.

This module runs a closed Ruff invocation. It does not accept arbitrary commands,
execute agent tools, or inspect files outside the supplied checkout root.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..foundations.contracts import StrictModel


class RuffUnavailableError(RuntimeError):
    pass


class QualityCheckReport(StrictModel):
    """Machine-readable result from the repository's configured Ruff checks."""

    schema_version: str = "agent-sdk-quality-check-v1"
    checkout_root: str
    checked_paths: list[str]
    passed: bool
    return_code: int
    diagnostics: str = ""
    formatter_checked: bool = False


def check_source_quality(checkout_root: Path, *, check_format: bool = True) -> QualityCheckReport:
    """Run the closed Ruff import, lint, and optional formatting checks.

    ``checkout_root`` must contain the SDK's ``src`` tree. Ruff is a development
    dependency, so callers should first install the package's ``dev`` group.
    """

    root = checkout_root.resolve()
    paths = _checked_paths(root)
    ruff = shutil.which("ruff")
    if ruff is None:
        raise RuffUnavailableError(
            "Ruff is unavailable; install the SDK development dependency group."
        )

    commands = [
        [
            ruff,
            "check",
            *paths,
            "--select",
            "F401,F811,E,F,I,UP",
        ]
    ]
    if check_format:
        commands.append([ruff, "format", "--check", *paths])

    outputs: list[str] = []
    return_code = 0
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        outputs.append(completed.stdout)
        outputs.append(completed.stderr)
        if completed.returncode != 0:
            return_code = completed.returncode
            break
    return QualityCheckReport(
        checkout_root=str(root),
        checked_paths=paths,
        passed=return_code == 0,
        return_code=return_code,
        diagnostics="".join(outputs).strip(),
        formatter_checked=check_format,
    )


def _checked_paths(root: Path) -> list[str]:
    candidates = ["src", "tests", "examples", "scripts"]
    paths = [candidate for candidate in candidates if (root / candidate).is_dir()]
    if not (root / "src" / "nailong_agent_sdk").is_dir():
        raise ValueError(f'The checkout root "{root}" must contain src/nailong_agent_sdk.')
    return paths
