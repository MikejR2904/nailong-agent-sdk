"""Remember what files looked like before the agent changed them, so a turn can be undone."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from nailong_agent_sdk.starter import ToolEvent

from .apply_patch import patch_paths

TRACKED_TOOLS = frozenset({"write_file", "edit_file", "notebook_edit", "apply_patch"})
COMMAND_TOOLS = frozenset({"run_command", "start_background"})
SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".nailong",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "target",
    }
)
MAX_TREE_FILES = 5_000
MAX_TREE_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
_MANIFEST = "manifest.json"
_TREE = "tree.json"
_BLOBS = "blobs"
_REDO = "redo"


class CheckpointStore:
    def __init__(
        self, project: Path, directory: Path, extra: dict[str, Path] | None = None
    ) -> None:
        self._project = project.resolve()
        self._directory = directory
        self._roots = {"": self._project, **{n: p.resolve() for n, p in (extra or {}).items()}}
        self.turn = 0
        self._tree_turn = 0

    def add_root(self, name: str, path: Path) -> None:
        self._roots[name] = path.resolve()

    def begin_turn(self) -> int:
        shutil.rmtree(self._directory / _REDO, ignore_errors=True)
        existing = [int(path.name.split("-")[1]) for path in self._turns()]
        self.turn = max(existing, default=0) + 1
        return self.turn

    def before_tool(self, event: ToolEvent) -> None:
        if self.turn == 0:
            return None
        if event.tool == "apply_patch":
            for path in patch_paths(str(event.arguments.get("patch", ""))):
                self._snapshot_file(ToolEvent(event.tool, {"path": path}, event.iteration))
        elif event.tool in TRACKED_TOOLS:
            self._snapshot_file(event)
        elif event.tool in COMMAND_TOOLS and self._tree_turn != self.turn:
            self._snapshot_tree()
        return None

    def created(self, turn: int | None = None) -> list[str]:
        """Files this turn made that did not exist before it and still exist."""
        folder = self._directory / f"turn-{turn or self.turn}"
        if not folder.is_dir():
            return []
        found = {key for key, entry in self._read(folder).items() if not entry["existed"]}
        if (folder / _TREE).is_file():
            tree = json.loads((folder / _TREE).read_text(encoding="utf-8"))
            found.update(self._tree_difference(tree)[1])
        return sorted(key for key in found if self._resolve(key).is_file())

    def turns(self) -> list[tuple[int, list[str]]]:
        found = []
        for folder in self._turns():
            names = sorted(self._read(folder))
            if (folder / _TREE).is_file():
                names.append("(whole project, before a command)")
            found.append((int(folder.name.split("-")[1]), names))
        return found

    def plan(self, to_turn: int) -> dict[str, list[str]]:
        restore: set[str] = set()
        delete: set[str] = set()
        for folder in self._turns():
            if int(folder.name.split("-")[1]) < to_turn:
                continue
            restore.update(self._read(folder))
            if (folder / _TREE).is_file():
                tree = json.loads((folder / _TREE).read_text(encoding="utf-8"))
                differs, created = self._tree_difference(tree)
                restore.update(differs)
                delete.update(created)
        return {"restore": sorted(restore), "delete": sorted(delete - restore)}

    def _save_redo(self, to_turn: int) -> None:
        folder = self._directory / _REDO
        shutil.rmtree(folder, ignore_errors=True)
        plan = self.plan(to_turn)
        manifest: dict[str, dict] = {}
        folder.mkdir(parents=True, exist_ok=True)
        for number, key in enumerate(sorted({*plan["restore"], *plan["delete"]}), 1):
            target = self._resolve(key)
            blob = f"{number}.bin"
            if target.is_file():
                (folder / blob).write_bytes(target.read_bytes())
            manifest[key] = {"existed": target.is_file(), "blob": blob}
        (folder / _MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def can_redo(self) -> bool:
        return (self._directory / _REDO / _MANIFEST).is_file()

    def redo(self) -> list[str]:
        folder = self._directory / _REDO
        if not self.can_redo():
            raise ValueError("There is nothing to redo: /redo only follows a /rewind.")
        touched = []
        for key, entry in self._read(folder).items():
            target = self._resolve(key)
            if entry["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((folder / entry["blob"]).read_bytes())
            else:
                target.unlink(missing_ok=True)
            touched.append(key)
        shutil.rmtree(folder)
        return sorted(touched)

    def rewind(self, to_turn: int) -> list[str]:
        self._save_redo(to_turn)
        touched: set[str] = set()
        for folder in reversed(self._turns()):
            if int(folder.name.split("-")[1]) < to_turn:
                continue
            if (folder / _TREE).is_file():
                touched.update(
                    self._restore_tree(json.loads((folder / _TREE).read_text(encoding="utf-8")))
                )
            for key, entry in self._read(folder).items():
                target = self._resolve(key)
                if entry["existed"]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((folder / entry["blob"]).read_bytes())
                else:
                    target.unlink(missing_ok=True)
                touched.add(key)
            shutil.rmtree(folder)
        return sorted(touched)

    def _snapshot_file(self, event: ToolEvent) -> None:
        root_name = str(event.arguments.get("root", ""))
        base = self._roots.get(root_name if root_name != "project" else "")
        if base is None:
            return
        target = (base / str(event.arguments.get("path", ""))).resolve()
        try:
            relative = target.relative_to(base).as_posix()
        except ValueError:
            return
        key = f"{root_name}:{relative}" if root_name and root_name != "project" else relative
        folder = self._directory / f"turn-{self.turn}"
        manifest = self._read(folder)
        if key in manifest:
            return
        blob = f"{len(manifest) + 1}.bin"
        folder.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            (folder / blob).write_bytes(target.read_bytes())
        manifest[key] = {"existed": target.is_file(), "blob": blob}
        (folder / _MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def _snapshot_tree(self) -> None:
        files: dict[str, dict[str, object]] = {}
        total = 0
        blobs = self._directory / _BLOBS
        blobs.mkdir(parents=True, exist_ok=True)
        for relative, path in self._walk():
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                continue
            total += size
            if len(files) >= MAX_TREE_FILES or total > MAX_TREE_BYTES:
                return
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            blob = blobs / digest
            if not blob.exists():
                blob.write_bytes(data)
            files[relative] = {"sha": digest}
        folder = self._directory / f"turn-{self.turn}"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = os.times().elapsed
        (folder / _TREE).write_text(json.dumps({"files": files, "stamp": stamp}), encoding="utf-8")
        self._tree_turn = self.turn

    def _tree_difference(self, tree: dict) -> tuple[list[str], list[str]]:
        known = tree["files"]
        differs: list[str] = []
        created: list[str] = []
        present = dict(self._walk())
        for relative, entry in known.items():
            path = present.get(relative)
            if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha"]:
                differs.append(relative)
        created = [relative for relative in present if relative not in known]
        return differs, created

    def _restore_tree(self, tree: dict) -> list[str]:
        differs, created = self._tree_difference(tree)
        for relative in differs:
            target = self._project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                (self._directory / _BLOBS / tree["files"][relative]["sha"]).read_bytes()
            )
        for relative in created:
            (self._project / relative).unlink(missing_ok=True)
        return [*differs, *created]

    def _walk(self):
        for current, directories, names in os.walk(self._project):
            directories[:] = [d for d in directories if d not in SKIPPED_DIRECTORIES]
            for name in names:
                path = Path(current) / name
                yield path.relative_to(self._project).as_posix(), path

    def _resolve(self, key: str) -> Path:
        if ":" in key:
            root, relative = key.split(":", 1)
            return self._roots[root] / relative
        return self._project / key

    def _turns(self) -> list[Path]:
        if not self._directory.is_dir():
            return []
        found = [p for p in self._directory.glob("turn-*") if p.is_dir()]
        return sorted(found, key=lambda p: int(p.name.split("-")[1]))

    @staticmethod
    def _read(folder: Path) -> dict[str, dict]:
        path = folder / _MANIFEST
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
