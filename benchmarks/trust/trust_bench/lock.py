# Copyright (c) 2026 David Michael Indraputra

"""Pre-registration lock: the test split runs only against committed, hash-matched code."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PREREGISTRATION = "benchmarks/trust/PREREGISTRATION.md"
LOCK_FILE = "benchmarks/trust/LOCK.json"
REGISTRY = "benchmarks/trust/results/test_runs.jsonl"
HARNESS_DIRS = ("benchmarks/trust/trust_bench",)
SYSTEM_DIRS = ("src/nailong_agent_sdk",)
TRACKED = (
    PREREGISTRATION,
    *HARNESS_DIRS,
    *SYSTEM_DIRS,
    "tests/trust_bench",
    "tests/support/trust_runs.py",
    "tests/support/trust_rows.py",
    "tests/support/jev_judge.py",
)
DIGESTED = ("*.py", "*.json")
PACKAGES = (
    "langgraph",
    "langgraph-prebuilt",
    "langchain-core",
    "typesafe-sdk",
    "autogen-agentchat",
    "crewai",
)


class LockError(RuntimeError):
    """The test split cannot be run, or a lock cannot be written, and the message says why."""


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as error:
        raise LockError(
            "git is not installed or not on PATH, so the lock cannot be checked."
        ) from error
    except subprocess.CalledProcessError as error:
        raise LockError(f"git {' '.join(args)} failed: {error.stderr.strip()}") from error
    return done.stdout


def tree_digest(root: Path, directories: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for directory in directories:
        base = root / directory
        for path in sorted({found for pattern in DIGESTED for found in base.rglob(pattern)}):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def versions() -> dict[str, str]:
    found = {"python": platform.python_version(), "platform": platform.platform()}
    for name in PACKAGES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return found


def _uncommitted(root: Path) -> list[str]:
    out = _git(root, "status", "--porcelain", "--", *TRACKED)
    return [line[3:] for line in out.splitlines() if line.strip()]


def _require_committed(root: Path) -> str:
    if not (root / PREREGISTRATION).is_file():
        raise LockError(f"{PREREGISTRATION} does not exist; write the pre-registration first.")
    dirty = _uncommitted(root)
    if dirty:
        shown = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
        raise LockError(
            f"{len(dirty)} path(s) under the harness, the SDK source or the pre-registration have "
            f"uncommitted changes ({shown}); commit them before locking."
        )
    return _git(root, "rev-parse", "HEAD").strip()


def create_lock(root: Path, test_plan: dict[str, Any]) -> dict[str, Any]:
    from .gates import run_gates

    commit = _require_committed(root)
    failed = [gate.name for gate in run_gates() if not gate.passed]
    if failed:
        raise LockError(f"reproduction gates failed ({', '.join(failed)}); fix the harness first.")
    lock = {
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "preregistration_sha256": file_digest(root / PREREGISTRATION),
        "harness_sha256": tree_digest(root, HARNESS_DIRS),
        "system_sha256": tree_digest(root, SYSTEM_DIRS),
        "versions": versions(),
        "test_plan": test_plan,
    }
    target = root / LOCK_FILE
    if target.exists():
        raise LockError(f"{LOCK_FILE} already exists; a locked pre-registration is not rewritten.")
    target.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return lock


def _is_ancestor(root: Path, ancestor: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, "HEAD"],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def verify_lock(root: Path) -> dict[str, Any]:
    target = root / LOCK_FILE
    if not target.is_file():
        raise LockError(f"{LOCK_FILE} does not exist; run the lock command before the test split.")
    lock = json.loads(target.read_text(encoding="utf-8"))
    _require_committed(root)
    if not _is_ancestor(root, lock["commit"]):
        raise LockError(
            f"the locked commit {lock['commit'][:12]} is not in the history of HEAD; "
            "the test split runs only on a descendant of the locked commit."
        )
    current = {
        "preregistration_sha256": file_digest(root / PREREGISTRATION),
        "harness_sha256": tree_digest(root, HARNESS_DIRS),
        "system_sha256": tree_digest(root, SYSTEM_DIRS),
    }
    for name, value in current.items():
        if value != lock[name]:
            raise LockError(f"{name} is {value[:12]} but the lock holds {lock[name][:12]}.")
    return lock


def eval_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["run", job[0], job[1], job[2], job[3]])


def tamper_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["tamper", job[0], job[1], job[2]])


def verify_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["verify", job[0], job[1], job[2], job[3]])


def attack_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["attack", *job])


def authority_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["authority", job[0], job[1], job[2]])


def chain_job_key(job: tuple[Any, ...]) -> str:
    return json.dumps(["chain", job[0], job[1]])


def judge_job_key(identifier: str) -> str:
    return json.dumps(["judge", identifier])


def register_test_jobs(
    root: Path, keys: Iterable[str], command: str, rerun_reason: str | None = None
) -> None:
    registry = root / REGISTRY
    registry.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if registry.is_file():
        for line in registry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.update(json.loads(line)["jobs"])
    wanted = list(keys)
    repeated = [key for key in wanted if key in seen]
    if repeated and not rerun_reason:
        raise LockError(
            f"{len(repeated)} of {len(wanted)} test jobs were already run (first: {repeated[0]}); "
            "the test split is run once per release; pass a rerun reason to record a deviation."
        )
    entry: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "jobs": wanted,
    }
    if repeated:
        entry["rerun_reason"] = rerun_reason
        entry["repeated"] = len(repeated)
    with registry.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
