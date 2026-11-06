"""Backing up, restoring, checking and migrating the agent state under `.nailong/`.

The state is plain files: the wiki and its history, the discoveries journal, the knowledge
index, the plan journals, the event logs and the project settings. `backup` writes them to one
zip with a manifest of every file's hash and the state version; `verify` checks a backup
against its manifest; `restore` puts it back, first copying whatever it replaces to a rollback
zip so the restore itself can be undone. `migrate` brings older state up to the current
version, taking a backup first.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write, exclusive

STATE_VERSION = 2
VERSION_FILE = "state-version.json"
BACKED_UP = (
    "wiki",
    "discoveries",
    "knowledge",
    "tasks",
    "events",
    "claims",
    "config.toml",
    "rules",
)
SKIPPED_SUFFIXES = (".lock", ".running", ".pid", ".tmp")
MANIFEST = "backup-manifest.json"
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024


class StateError(ValueError):
    pass


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for name in BACKED_UP:
        target = root / name
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found += sorted(
                item
                for item in target.rglob("*")
                if item.is_file() and not item.name.endswith(SKIPPED_SUFFIXES)
            )
    return found


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def state_version(root: Path) -> int:
    path = root / VERSION_FILE
    if not path.is_file():
        legacy = ("wiki", "discoveries", "knowledge", "tasks")
        return 1 if any((root / name).exists() for name in legacy) else STATE_VERSION
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise StateError(f"{path} is not a state version file ({error}).") from None


def write_version(root: Path, version: int = STATE_VERSION) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / VERSION_FILE, json.dumps({"version": version}))


def backup(root: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise StateError(f"{destination} already exists; choose a new name.")
    files = _files(root)
    total = sum(item.stat().st_size for item in files)
    if total > MAX_BACKUP_BYTES:
        raise StateError(f"The state is {total // 1_000_000} MB; the backup limit is 2000 MB.")
    manifest = {
        "state_version": state_version(root),
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            data = item.read_bytes()
            name = item.relative_to(root).as_posix()
            manifest["files"][name] = _digest(data)
            archive.writestr(name, data)
        archive.writestr(MANIFEST, json.dumps(manifest, indent=1))
    temporary.replace(destination)
    return {"files": len(files), "bytes": total, "state_version": manifest["state_version"]}


def verify(backup_file: Path) -> dict[str, Any]:
    """Check every file in a backup against its manifest; raise naming what does not match."""
    if not backup_file.is_file():
        raise StateError(f"{backup_file} is not a file.")
    try:
        archive = zipfile.ZipFile(backup_file)
    except zipfile.BadZipFile as error:
        raise StateError(f"{backup_file} is not a backup ({error}).") from None
    with archive:
        if MANIFEST not in archive.namelist():
            raise StateError(f"{backup_file} has no {MANIFEST}; it is not a nailong backup.")
        manifest = json.loads(archive.read(MANIFEST))
        problems = []
        for name, expected in manifest["files"].items():
            if name not in archive.namelist():
                problems.append(f"{name}: missing")
            elif _digest(archive.read(name)) != expected:
                problems.append(f"{name}: does not match its recorded hash")
        extra = sorted(set(archive.namelist()) - set(manifest["files"]) - {MANIFEST})
        problems += [f"{name}: not in the manifest" for name in extra]
    if problems:
        raise StateError(f"{backup_file} is damaged: " + "; ".join(problems[:8]))
    return {"files": len(manifest["files"]), "state_version": manifest["state_version"]}


def restore(root: Path, backup_file: Path, rollback: Path | None = None) -> dict[str, Any]:
    """Replace the state with a backup; the state it replaces is saved to a rollback zip first."""
    info = verify(backup_file)
    if info["state_version"] > STATE_VERSION:
        raise StateError(
            f"{backup_file} is state version {info['state_version']}, newer than this program "
            f"understands ({STATE_VERSION}); upgrade nailong first."
        )
    root.mkdir(parents=True, exist_ok=True)
    rollback = rollback or root.parent / f"{root.name}-rollback-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with exclusive(root / ".restore.lock", wait=5.0, stale=300.0, what="state restore"):
        saved = backup(root, rollback) if _files(root) else None
        for item in _files(root):
            item.unlink()
        with zipfile.ZipFile(backup_file) as archive:
            for name in archive.namelist():
                if name == MANIFEST:
                    continue
                target = (root / name).resolve()
                if not target.is_relative_to(root.resolve()):
                    raise StateError(f"{backup_file} holds a path outside the state: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        write_version(root, info["state_version"])
    return {"restored": info["files"], "rollback": str(rollback) if saved else None}


def _migrate_1_to_2(root: Path) -> list[str]:
    """Version 2: knowledge index carries a revision; wiki pages gain a revision history."""
    done: list[str] = []
    index = root / "knowledge" / "trees.json"
    if index.is_file():
        data = json.loads(index.read_text(encoding="utf-8"))
        if isinstance(data, list):
            atomic_write(index, json.dumps({"revision": 0, "trees": data}))
            done.append("knowledge index given a revision")
    wiki = root / "wiki"
    if wiki.is_dir():
        for page in sorted(wiki.rglob("*.md")):
            parts = page.relative_to(wiki).parts
            name = page.relative_to(wiki).with_suffix("").as_posix()
            if any(part.startswith(".") for part in parts) or name in {"index", "log"}:
                continue
            first = wiki / ".history" / name / "0001.md"
            if not first.exists():
                atomic_write(first, page.read_text(encoding="utf-8"))
                done.append(f"wiki page {name} given revision 1")
    return done


MIGRATIONS = {1: _migrate_1_to_2}


def migrate(root: Path) -> dict[str, Any]:
    version = state_version(root)
    if version > STATE_VERSION:
        raise StateError(
            f"The state is version {version}, newer than this program understands "
            f"({STATE_VERSION}); upgrade nailong, or restore an older backup."
        )
    if version == STATE_VERSION:
        write_version(root)
        return {"from": version, "to": version, "steps": [], "backup": None}
    saved = root.parent / f"{root.name}-before-migration-{datetime.now():%Y%m%d-%H%M%S}.zip"
    backup(root, saved)
    steps: list[str] = []
    try:
        while version < STATE_VERSION:
            steps += MIGRATIONS[version](root)
            version += 1
            write_version(root, version)
    except Exception as error:
        restore(root, saved)
        raise StateError(
            f"Migration failed at version {version} ({type(error).__name__}: {error}); the state "
            f"was put back from {saved}."
        ) from error
    return {
        "from": state_version_before(saved),
        "to": version,
        "steps": steps,
        "backup": str(saved),
    }


def state_version_before(backup_file: Path) -> int:
    with zipfile.ZipFile(backup_file) as archive:
        return int(json.loads(archive.read(MANIFEST))["state_version"])
