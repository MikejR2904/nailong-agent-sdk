# Copyright (c) 2026 David Michael Indraputra

"""Acceptance checks a host can register: a held-out oracle and checks read from the workspace."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .cases import Case
from .checks import deliverable_bytes
from .forms import REFERENCE_GATES

HELD_OUT = "held-out"
WORKSPACE_RECOMPUTE = "workspace-recompute"
NAIVE_REFERENCE = "naive-reference"
NAIVE_HASH = "naive-hash"
GATE_NAMES = (HELD_OUT, WORKSPACE_RECOMPUTE, NAIVE_REFERENCE, NAIVE_HASH)
INDEPENDENT = frozenset({HELD_OUT})


@dataclass(frozen=True)
class Acceptance:
    verifier_id: str
    check: Callable[[Path], tuple[bool, str]]
    subject: Callable[[Path], bytes | None]

    def subject_digest(self, workspace: Path) -> str | None:
        content = self.subject(workspace)
        return None if content is None else hashlib.sha256(content).hexdigest()


def _same(delivered: bytes, reference: bytes) -> bool:
    try:
        return json.loads(delivered.decode("utf-8")) == json.loads(reference.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return delivered == reference


def _held_out(case: Case) -> Acceptance:
    def check(workspace: Path) -> tuple[bool, str]:
        content = deliverable_bytes(case, workspace)
        if content is None:
            return False, f'"{case.deliverable}" does not exist'
        if case.is_defective(case.deliverable, content):
            return False, "the held-out oracle rejects the deliverable"
        return True, "the held-out oracle accepts the deliverable"

    return Acceptance("held-out-oracle", check, lambda ws: deliverable_bytes(case, ws))


def _workspace_recompute(case: Case) -> Acceptance:
    def check(workspace: Path) -> tuple[bool, str]:
        content = deliverable_bytes(case, workspace)
        if content is None:
            return False, f'"{case.deliverable}" does not exist'
        if not case.generic_ok(case.deliverable, content):
            return False, "the deliverable is malformed"
        if not case.visible_ok(workspace, case.deliverable, content):
            return False, "the deliverable disagrees with the data in the workspace"
        return True, "the deliverable agrees with the data in the workspace"

    return Acceptance("workspace-recompute", check, lambda ws: deliverable_bytes(case, ws))


def _naive_reference(case: Case) -> Acceptance:
    def check(workspace: Path) -> tuple[bool, str]:
        content = deliverable_bytes(case, workspace)
        reference = workspace / REFERENCE_GATES[NAIVE_REFERENCE]
        if content is None or not reference.is_file():
            return False, "the deliverable or reference.json does not exist"
        if _same(content, reference.read_bytes()):
            return True, "the deliverable equals reference.json"
        return False, "the deliverable differs from reference.json"

    return Acceptance("naive-reference", check, lambda ws: deliverable_bytes(case, ws))


def _naive_hash(case: Case) -> Acceptance:
    def check(workspace: Path) -> tuple[bool, str]:
        content = deliverable_bytes(case, workspace)
        reference = workspace / REFERENCE_GATES[NAIVE_HASH]
        if content is None or not reference.is_file():
            return False, "the deliverable or reference.sha256 does not exist"
        digest = hashlib.sha256(content).hexdigest()
        if digest == reference.read_text(encoding="utf-8").strip():
            return True, "the deliverable matches reference.sha256"
        return False, "the deliverable does not match reference.sha256"

    return Acceptance("naive-hash", check, lambda ws: deliverable_bytes(case, ws))


def acceptance_for(name: str, case: Case) -> Acceptance:
    builders = {
        HELD_OUT: _held_out,
        WORKSPACE_RECOMPUTE: _workspace_recompute,
        NAIVE_REFERENCE: _naive_reference,
        NAIVE_HASH: _naive_hash,
    }
    if name not in builders:
        raise ValueError(f'unknown acceptance gate "{name}"; known: {", ".join(GATE_NAMES)}.')
    return builders[name](case)
