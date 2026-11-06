from __future__ import annotations

import json
import zipfile

import pytest

from nailong_cli import state_ops
from nailong_cli.state_ops import STATE_VERSION, StateError
from nailong_cli.wiki import Wiki
from tests.conftest import final, run_cli


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "proj" / ".nailong"
    wiki = Wiki(root / "wiki", root.parent)
    wiki.write("auth", "# Auth\nTokens last 15 minutes.", "start")
    wiki.write("auth", "# Auth\nTokens last 15 minutes, refresh a week.", "more", base_revision=1)
    (root / "config.toml").write_text('model = "m"\n', encoding="utf-8")
    state_ops.write_version(root)
    return root


def snapshot(root):
    return {item.relative_to(root).as_posix(): item.read_bytes() for item in state_ops._files(root)}


def test_a_backup_holds_every_state_file_with_its_hash_and_verifies(root, tmp_path):
    info = state_ops.backup(root, tmp_path / "b.zip")
    assert info["files"] == len(snapshot(root)) and info["state_version"] == STATE_VERSION
    assert state_ops.verify(tmp_path / "b.zip") == {"files": info["files"], "state_version": 2}
    with zipfile.ZipFile(tmp_path / "b.zip") as archive:
        names = archive.namelist()
    assert "wiki/auth.md" in names and "wiki/.history/auth/0002.md" in names
    assert "config.toml" in names and state_ops.MANIFEST in names
    assert not any(name.endswith((".lock", ".pid", ".running")) for name in names)


def test_a_backup_never_overwrites_an_existing_file(root, tmp_path):
    state_ops.backup(root, tmp_path / "b.zip")
    with pytest.raises(StateError, match="already exists; choose a new name"):
        state_ops.backup(root, tmp_path / "b.zip")


def test_a_backup_that_was_edited_or_cut_is_called_damaged_and_never_restored(root, tmp_path):
    state_ops.backup(root, tmp_path / "b.zip")
    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(tmp_path / "b.zip") as source, zipfile.ZipFile(forged, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, b"tampered" if name == "wiki/auth.md" else data)
    with pytest.raises(StateError, match="wiki/auth.md: does not match its recorded hash"):
        state_ops.verify(forged)
    before = snapshot(root)
    with pytest.raises(StateError, match="is damaged"):
        state_ops.restore(root, forged)
    assert snapshot(root) == before
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(StateError, match="is not a backup"):
        state_ops.verify(tmp_path / "junk.zip")
    with zipfile.ZipFile(tmp_path / "plain.zip", "w") as plain:
        plain.writestr("a.txt", "x")
    with pytest.raises(StateError, match="has no backup-manifest.json"):
        state_ops.verify(tmp_path / "plain.zip")


def test_restore_puts_the_state_back_and_the_rollback_undoes_the_restore(root, tmp_path):
    state_ops.backup(root, tmp_path / "good.zip")
    good = snapshot(root)
    wiki = Wiki(root / "wiki", root.parent)
    wiki.write("auth", "# Auth\nWRONG.", "bad edit", base_revision=2)
    wiki.write("extra", "added later", "more")
    bad = snapshot(root)
    assert bad != good
    done = state_ops.restore(root, tmp_path / "good.zip", tmp_path / "undo.zip")
    assert done["restored"] == len(good) and done["rollback"] == str(tmp_path / "undo.zip")
    assert snapshot(root) == good
    assert Wiki(root / "wiki", root.parent).read("auth")["revision"] == 2
    state_ops.restore(root, tmp_path / "undo.zip", tmp_path / "undo2.zip")
    assert snapshot(root) == bad


def test_a_backup_from_a_newer_program_is_refused_by_version(root, tmp_path):
    state_ops.backup(root, tmp_path / "b.zip")
    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(tmp_path / "b.zip") as source, zipfile.ZipFile(newer, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == state_ops.MANIFEST:
                manifest = json.loads(data)
                manifest["state_version"] = 99
                data = json.dumps(manifest).encode()
            target.writestr(name, data)
    with pytest.raises(StateError, match="state version 99, newer than this program"):
        state_ops.restore(root, newer)


def legacy_state(root):
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "old.md").write_text("old page\n", encoding="utf-8")
    (root / "knowledge").mkdir()
    (root / "knowledge" / "trees.json").write_text("[]", encoding="utf-8")


def test_older_state_is_migrated_after_a_backup_and_the_pages_keep_their_text(tmp_path):
    root = tmp_path / "proj" / ".nailong"
    legacy_state(root)
    assert state_ops.state_version(root) == 1
    info = state_ops.migrate(root)
    assert info["from"] == 1 and info["to"] == STATE_VERSION and info["backup"]
    assert "knowledge index given a revision" in info["steps"]
    assert "wiki page old given revision 1" in info["steps"]
    assert state_ops.verify(__import__("pathlib").Path(info["backup"]))["state_version"] == 1
    wiki = Wiki(root / "wiki", root.parent)
    assert wiki.read("old")["text"] == "old page\n" and wiki.revision("old") == 1
    wiki.write("old", "new text", "update", base_revision=1)
    assert json.loads((root / "knowledge" / "trees.json").read_text(encoding="utf-8")) == {
        "revision": 0,
        "trees": [],
    }
    assert state_ops.migrate(root)["steps"] == []


def test_a_migration_that_fails_puts_the_state_back_exactly(tmp_path, monkeypatch):
    root = tmp_path / "proj" / ".nailong"
    legacy_state(root)
    before = snapshot(root)

    def broken(path):
        (path / "wiki" / "half.md").write_text("half done", encoding="utf-8")
        raise RuntimeError("disk full")

    monkeypatch.setitem(state_ops.MIGRATIONS, 1, broken)
    with pytest.raises(StateError, match="Migration failed at version 1 .*RuntimeError: disk full"):
        state_ops.migrate(root)
    assert snapshot(root) == before
    assert not (root / "wiki" / "half.md").exists()
    assert state_ops.state_version(root) == 1


def test_state_newer_than_the_program_is_not_touched(tmp_path):
    root = tmp_path / "p" / ".nailong"
    state_ops.write_version(root, 99)
    with pytest.raises(StateError, match="version 99, newer than this program understands"):
        state_ops.migrate(root)


def test_the_flags_backup_verify_restore_and_migrate_work_from_the_command_line(
    project, home, tmp_path
):
    run_cli(project, home, [], final("hi"), lines="Hello\n/exit\n", interactive=True)
    file = str(tmp_path / "cli.zip")
    code, out, err, _ = run_cli(project, home, ["--backup", file, "--project", str(project)])
    assert code == 0 and "Saved" in out, err
    code, out, _, _ = run_cli(project, home, ["--verify-backup", file])
    assert code == 0 and "verified, state version 2" in out
    code, out, _, _ = run_cli(project, home, ["--restore", file, "--project", str(project)])
    assert code == 0 and "Restored" in out and "--restore it to undo" in out
    code, out, _, _ = run_cli(project, home, ["--migrate", "--project", str(project)])
    assert code == 0 and "State is at version 2." in out
    code, _, err, _ = run_cli(project, home, ["--verify-backup", str(tmp_path / "missing.zip")])
    assert code != 0 and "is not a file" in err


def test_a_session_on_old_state_says_to_migrate_and_a_new_project_gets_the_version_file(
    project, home
):
    legacy_state(project / ".nailong")
    _, out, _, _ = run_cli(project, home, [], lines="/exit\n", interactive=True)
    assert "agent state here is version 1; run nailong --migrate" in out
    assert not (project / ".nailong" / state_ops.VERSION_FILE).exists()
    fresh_project = project.parent / "fresh"
    (fresh_project / ".nailong").mkdir(parents=True)
    (fresh_project / ".nailong" / "config.toml").write_text(
        (project / ".nailong" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _, out2, _, _ = run_cli(fresh_project, home, [], lines="/exit\n", interactive=True)
    assert "run nailong --migrate" not in out2
    assert (fresh_project / ".nailong" / state_ops.VERSION_FILE).is_file()
