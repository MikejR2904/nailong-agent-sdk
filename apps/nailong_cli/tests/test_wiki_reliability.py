from __future__ import annotations

import threading

import pytest

from nailong_cli import wiki as wiki_module
from nailong_cli.wiki import Wiki
from tests.conftest import call, final, run_cli

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


@pytest.fixture
def project_wiki(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return Wiki(project / ".nailong" / "wiki", project), project


def test_updating_a_page_needs_the_revision_you_read(project_wiki):
    wiki, _ = project_wiki
    first = wiki.write("deploys", "Deploys go through staging.", "first")
    assert first["revision"] == 1
    with pytest.raises(ValueError, match="already exists at revision 1.*base_revision=1"):
        wiki.write("deploys", "Overwrite blindly.", "no revision")
    second = wiki.write("deploys", "Staging, then canary.", "refined", base_revision=1)
    assert second["revision"] == 2 and wiki.read("deploys")["revision"] == 2
    with pytest.raises(ValueError, match="at revision 2, not 1: someone changed it"):
        wiki.write("deploys", "Stale writer.", "late", base_revision=1)
    with pytest.raises(ValueError, match="does not exist, so it has no revision"):
        wiki.write("fresh", "x", "y", base_revision=3)


def test_every_revision_is_kept_with_who_wrote_it_and_why(project_wiki):
    wiki, _ = project_wiki
    wiki.write("notes", "one", "start", author="main")
    wiki.write("notes", "two", "fix", base_revision=1, author="user")
    history = wiki.journal("notes")
    assert [(e["revision"], e["author"], e["action"]) for e in history] == [
        (1, "main", "created"),
        (2, "user", "updated"),
    ]
    root = wiki._directory / ".history" / "notes"
    assert (root / "0001.md").read_text(encoding="utf-8") == "one\n"
    assert (root / "0002.md").read_text(encoding="utf-8") == "two\n"
    assert "by user: fix" in (wiki._directory / "log.md").read_text(encoding="utf-8")
    assert wiki.names() == ["notes"]


def test_a_page_that_predates_revisions_keeps_its_old_text_as_revision_one(tmp_path):
    directory = tmp_path / "wiki"
    directory.mkdir()
    (directory / "legacy.md").write_text("old text\n", encoding="utf-8")
    wiki = Wiki(directory)
    assert wiki.revision("legacy") == 1
    wiki.write("legacy", "new text", "update", base_revision=1)
    assert (directory / ".history" / "legacy" / "0001.md").read_text(
        encoding="utf-8"
    ) == "old text\n"
    assert wiki.revision("legacy") == 2


def test_a_crash_after_the_history_is_written_is_repaired_by_the_next_write(
    project_wiki, monkeypatch
):
    wiki, _ = project_wiki
    wiki.write("plan", "version one", "start")
    real = wiki_module._atomic_write
    calls = {"count": 0}

    def crash_on_the_page_itself(path, text):
        calls["count"] += 1
        if path.name == "plan.md":
            raise OSError("power cut")
        real(path, text)

    monkeypatch.setattr(wiki_module, "_atomic_write", crash_on_the_page_itself)
    with pytest.raises(OSError, match="power cut"):
        wiki.write("plan", "version two", "interrupted", base_revision=1)
    monkeypatch.setattr(wiki_module, "_atomic_write", real)
    assert wiki.read("plan")["text"] == "version one\n"
    assert (wiki._directory / ".history" / "plan" / "0002.md").is_file()
    wiki.write("other", "unrelated page", "this write repairs first")
    page = wiki.read("plan")
    assert page["text"] == "version two\n" and page["revision"] == 2
    recovered = [e for e in wiki.journal("plan") if e["action"] == "recovered"]
    assert recovered and recovered[0]["revision"] == 2
    assert "[[plan]]" in (wiki._directory / "index.md").read_text(encoding="utf-8")
    assert not (wiki._directory / ".writing").exists()


def test_repair_can_be_asked_for_and_says_what_it_fixed(project_wiki):
    wiki, _ = project_wiki
    wiki.write("a", "text", "start")
    (wiki._directory / "a.md").unlink()
    (wiki._directory / "index.md").unlink()
    fixed = wiki.repair()
    assert any("page restored to revision 1" in line for line in fixed)
    assert (wiki._directory / "a.md").read_text(encoding="utf-8") == "text\n"
    assert (wiki._directory / "index.md").is_file()


def test_two_writers_with_the_same_base_revision_cannot_both_win(project_wiki):
    wiki, _ = project_wiki
    wiki.write("shared", "base", "start")
    outcomes: list[str] = []
    barrier = threading.Barrier(6)

    def writer(number: int) -> None:
        barrier.wait()
        try:
            wiki.write("shared", f"from writer {number}", "race", base_revision=1)
            outcomes.append("won")
        except ValueError as error:
            outcomes.append(str(error))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("won") == 1
    assert all("at revision 2, not 1" in item for item in outcomes if item != "won")
    assert wiki.revision("shared") == 2 and len(wiki.journal("shared")) == 2


def test_concurrent_writes_to_different_pages_all_land_and_the_index_lists_them(project_wiki):
    wiki, _ = project_wiki
    barrier = threading.Barrier(8)

    def writer(number: int) -> None:
        barrier.wait()
        wiki.write(f"page-{number}", f"# Page {number}", "parallel")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wiki.names()) == 8
    index = (wiki._directory / "index.md").read_text(encoding="utf-8")
    assert all(f"[[page-{n}]]" in index for n in range(8))
    assert len(wiki.journal()) == 8


def test_a_lock_left_by_a_dead_writer_is_taken_over(project_wiki, monkeypatch):
    wiki, _ = project_wiki
    wiki._directory.mkdir(parents=True)
    lock = wiki._directory / ".lock"
    lock.write_text("", encoding="utf-8")
    old = lock.stat().st_mtime - 3600
    import os

    os.utime(lock, (old, old))
    assert wiki.write("after-crash", "fine", "stale lock")["created"] is True
    live = wiki._directory / ".lock"
    live.write_text("", encoding="utf-8")
    monkeypatch.setattr(wiki_module, "LOCK_WAIT_SECONDS", 0.2)
    with pytest.raises(ValueError, match="Another writer holds the wiki lock"):
        wiki.write("blocked", "x", "y")


def test_a_page_reads_as_stale_once_a_file_it_cites_changes_or_goes(project_wiki):
    wiki, project = project_wiki
    (project / "auth.py").write_text("TTL = 15\n", encoding="utf-8")
    wiki.write("auth", "Tokens last 15 minutes.", "from code", sources=["auth.py"])
    assert wiki.read("auth")["stale"] is False and wiki.stale_pages() == {}
    (project / "auth.py").write_text("TTL = 60\n", encoding="utf-8")
    page = wiki.read("auth")
    assert page["stale"] is True and page["sources"] == [{"path": "auth.py", "status": "changed"}]
    assert wiki.stale_pages() == {"auth": ["auth.py (changed)"]}
    (project / "auth.py").unlink()
    assert wiki.read("auth")["sources"][0]["status"] == "missing"
    (project / "auth.py").write_text("TTL = 15\n", encoding="utf-8")
    assert wiki.read("auth")["stale"] is False
    (project / "auth.py").write_text("TTL = 99\n", encoding="utf-8")
    wiki.write("auth", "Tokens last 99.", "re-read", base_revision=1, sources=["auth.py"])
    assert wiki.read("auth")["stale"] is False


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("../outside.txt", "is outside the project"),
        (".env", "credential-style path"),
        ("missing.py", "is not a file in the project"),
    ],
)
def test_sources_must_be_real_project_files_and_never_credentials(project_wiki, source, fragment):
    wiki, project = project_wiki
    (project / ".env").write_text("KEY=secret", encoding="utf-8")
    with pytest.raises(ValueError, match=fragment):
        wiki.write("page", "text", "why", sources=[source])
    with pytest.raises(ValueError, match="at most 10 files"):
        wiki.write("page", "text", "why", sources=["a.py"] * 11)


def test_the_agent_must_read_before_it_updates_and_sees_the_stale_flag(project, home):
    (project / "spec.md").write_text("limit is 5\n", encoding="utf-8")
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("wiki_write", page="limits", text="Limit is 5.", why="from spec", sources=["spec.md"]),
        call("wiki_write", page="limits", text="Limit is 6.", why="no revision"),
        call("wiki_write", page="limits", text="Limit is 6.", why="ok", base_revision=1),
        final("done"),
        lines="Record\n/wiki history limits\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    later = " ".join(str(item) for item in setup.model.calls[2].observations)
    assert "already exists at revision 1" in later
    assert "r1" in out and "r2" in out
    wiki_dir = project / ".nailong" / "wiki"
    assert (wiki_dir / "limits.md").read_text(encoding="utf-8") == "Limit is 6.\n"
    (project / "spec.md").write_text("limit is 7\n", encoding="utf-8")
    _, listing, _, _ = run_cli(project, home, [], lines="/wiki\n/exit\n", interactive=True)
    assert "limits" in listing and "(stale)" in listing
