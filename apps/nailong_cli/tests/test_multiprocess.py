from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from nailong_cli.discoveries import DiscoveryStore
from nailong_cli.wiki import Wiki

WORKER = Path(__file__).parent / "support" / "store_workers.py"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "src.py").write_text("X = 1\n", encoding="utf-8")
    return root


def start(mode, project, *args):
    return subprocess.Popen(
        [sys.executable, str(WORKER), mode, str(project), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(processes):
    results = []
    for process in processes:
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, err
        results.append(out.strip())
    return results


def test_processes_writing_different_wiki_pages_at_once_lose_none(project):
    finish([start("wiki-page", project, f"page-{n}") for n in range(6)])
    wiki = Wiki(project / ".nailong" / "wiki", project)
    assert wiki.names() == [f"page-{n}" for n in range(6)]
    index = (wiki._directory / "index.md").read_text(encoding="utf-8")
    assert all(f"[[page-{n}]]" in index for n in range(6))
    assert len(wiki.journal()) == 6


def test_processes_racing_to_update_one_wiki_page_produce_exactly_one_winner(project):
    Wiki(project / ".nailong" / "wiki", project).write("shared", "base", "start")
    results = finish([start("wiki-race", project, f"p{n}") for n in range(6)])
    assert results.count("won") == 1
    assert all("at revision 2, not 1" in item for item in results if item != "won")
    assert Wiki(project / ".nailong" / "wiki", project).revision("shared") == 2


def test_processes_publishing_discoveries_at_once_get_distinct_ids_and_keep_every_claim(project):
    ids = finish([start("discovery", project, f"agent{n}") for n in range(6)])
    assert sorted(ids) == [f"d-{n:04d}" for n in range(1, 7)]
    rows = DiscoveryStore(project / ".nailong" / "discoveries", project).listing("all")
    assert {row["owner"] for row in rows} == {f"agent{n}" for n in range(6)}


def test_a_process_killed_between_the_history_and_the_page_is_repaired_by_the_next_writer(project):
    wiki = Wiki(project / ".nailong" / "wiki", project)
    wiki.write("plan", "version one", "start")
    child = start("wiki-crash", project)
    try:
        assert child.stdout.readline().strip() == "at-crash-point"
    finally:
        child.kill()
        child.wait()
    assert wiki.read("plan")["text"] == "version one\n"
    assert (wiki._directory / ".history" / "plan" / "0002.md").is_file()
    assert (wiki._directory / ".writing").exists() and (wiki._directory / ".lock").exists()
    wiki.write("other", "triggers the repair", "after the crash")
    page = wiki.read("plan")
    assert page["text"] == "version two\n" and page["revision"] == 2
    assert not (wiki._directory / ".writing").exists()
