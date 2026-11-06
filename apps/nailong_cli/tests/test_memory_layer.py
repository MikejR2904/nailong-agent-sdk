from __future__ import annotations

import multiprocessing
import threading

import pytest

from nailong_cli.discoveries import DiscoveryStore
from nailong_cli.knowledge import KnowledgeBase
from nailong_cli.memory_refs import MemoryIndex, actor, may_write_authoritative, parse_ref
from nailong_cli.wiki import Wiki
from tests.conftest import call, final, run_cli


@pytest.fixture
def memory(tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 15\n", encoding="utf-8")
    wiki = Wiki(project / ".nailong" / "wiki", project)
    discoveries = DiscoveryStore(project / ".nailong" / "discoveries", project)
    knowledge = KnowledgeBase(
        project / ".nailong" / "knowledge", {"project": project}, project / ".nailong" / "work"
    )
    index = MemoryIndex(project, wiki, discoveries, knowledge)
    wiki.attach(index)
    return index, wiki, discoveries, knowledge, project


def publish(discoveries, claim="Tokens last 15 minutes.", topic="auth.ttl"):
    return discoveries.publish(
        topic, claim, [{"path": "src/auth.py", "quote": "TOKEN_TTL_MINUTES = 15"}]
    )["id"]


def test_references_have_one_spelling_and_a_bare_path_is_a_file():
    assert str(parse_ref("src/a.py")) == "file:src/a.py"
    assert str(parse_ref("Wiki:Systems/Auth")) == "wiki:systems/auth"
    assert str(parse_ref("discovery:D-0001")) == "discovery:d-0001"
    assert actor("main") == "agent:main" and actor("user") == "user"
    assert may_write_authoritative("main") and may_write_authoritative("user")
    assert not may_write_authoritative("sub-scout")


def test_a_subagent_cannot_write_authoritative_pages_and_is_pointed_to_discoveries(memory):
    _, wiki, _, _, _ = memory
    with pytest.raises(ValueError, match="agent:sub-scout may not write wiki pages.*discovery"):
        wiki.write("notes", "text", "why", author="sub-scout")
    wiki.write("notes", "text", "why", author="user")
    assert wiki.journal("notes")[0]["actor"] == "user"
    assert wiki.journal("notes")[0]["scope"] == "authoritative"


def test_a_page_resting_on_a_discovery_goes_stale_when_that_discovery_is_replaced(memory):
    index, wiki, discoveries, _, project = memory
    first = publish(discoveries)
    discoveries.accept(first, "checked")
    wiki.write("auth", "Tokens last 15 minutes.", "from d", sources=[f"discovery:{first}"])
    assert wiki.read("auth")["stale"] is False
    second = publish(discoveries, "Tokens last an hour.")
    assert discoveries.get(second)["conflicts_with"] == [first]
    assert wiki.read("auth")["stale"] is False
    discoveries.accept(second, "the doc is newer", [first])
    page = wiki.read("auth")
    assert page["stale"] is True
    assert page["sources"] == [{"path": f"discovery:{first}", "status": "changed"}]
    assert index.dependents(f"discovery:{first}") == ["auth"]
    assert index.describe(f"discovery:{first}")["cited_by"] == ["auth"]
    assert index.stale_report()["wiki"] == [f"auth: discovery:{first} (changed)"]


def test_a_page_resting_on_a_page_or_a_file_goes_stale_through_the_chain(memory):
    index, wiki, _, _, project = memory
    wiki.write("base", "Base facts.", "start", sources=["src/auth.py"])
    wiki.write("summary", "Built on base.", "start", sources=["wiki:base"])
    assert index.stale_report() == {}
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 60\n", encoding="utf-8")
    assert wiki.read("base")["stale"] is True and wiki.read("summary")["stale"] is False
    wiki.write(
        "base", "Base facts, rechecked.", "re-read", base_revision=1, sources=["src/auth.py"]
    )
    assert wiki.read("base")["stale"] is False and wiki.read("summary")["stale"] is True
    assert index.dependents("wiki:base") == ["summary"]


def test_citing_what_does_not_exist_or_cannot_be_checked_is_refused(memory):
    _, wiki, _, _, _ = memory
    with pytest.raises(ValueError, match='Source "discovery:d-0009" does not exist'):
        wiki.write("p", "x", "y", sources=["discovery:d-0009"])
    with pytest.raises(ValueError, match='Source "wiki:ghost" does not exist'):
        wiki.write("p", "x", "y", sources=["wiki:ghost"])
    loose = Wiki(wiki._directory.parent / "loose")
    with pytest.raises(ValueError, match="not attached to the project's memory"):
        loose.write("p", "x", "y", sources=["wiki:other"])


def test_the_memory_status_view_says_scope_owner_and_stale(memory):
    index, _, discoveries, _, project = memory
    first = publish(discoveries)
    assert index.describe(f"discovery:{first}")["scope"] == "shared"
    discoveries.accept(first, "ok")
    assert discoveries.listing("accepted")[0]["scope"] == "authoritative"
    (project / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")
    info = index.describe(f"discovery:{first}")
    assert info["stale"] is True and info["owner"] == "main" and info["kind"] == "discovery"
    assert index.describe("file:src/missing.py")["exists"] is False


def test_the_knowledge_base_keeps_every_update_when_two_writers_add_at_once(memory):
    _, _, _, knowledge, project = memory
    for number in range(6):
        (project / f"doc{number}.md").write_text(f"document number {number}\n", encoding="utf-8")
    other = KnowledgeBase(
        project / ".nailong" / "knowledge", {"project": project}, project / ".nailong" / "work"
    )
    barrier = threading.Barrier(6)

    def add(number: int) -> None:
        barrier.wait()
        (knowledge if number % 2 else other).add(f"doc{number}.md")

    threads = [threading.Thread(target=add, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(tree.relative_path for tree in knowledge.trees()) == [
        f"doc{n}.md" for n in range(6)
    ]
    assert knowledge.revision == other.revision == 6


def _add_in_another_process(project: str, name: str) -> None:
    from pathlib import Path

    base = Path(project)
    KnowledgeBase(
        base / ".nailong" / "knowledge", {"project": base}, base / ".nailong" / "work"
    ).add(name)


def test_the_knowledge_base_survives_writers_in_separate_processes(memory):
    _, _, _, knowledge, project = memory
    for number in range(4):
        (project / f"p{number}.md").write_text(f"process document {number}\n", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=_add_in_another_process, args=(str(project), f"p{n}.md"))
        for n in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0
    assert sorted(tree.relative_path for tree in knowledge.trees()) == [
        f"p{n}.md" for n in range(4)
    ]
    assert knowledge.revision == 4


def test_a_changed_source_is_flagged_on_search_and_never_passed_off_as_current(memory):
    _, _, _, knowledge, project = memory
    (project / "guide.md").write_text(
        "Rotate the signing key every ninety days.\n", encoding="utf-8"
    )
    knowledge.add("guide.md")
    assert knowledge.search("signing key")[0].status == "current"
    (project / "guide.md").write_text("Rotate the signing key yearly.\n", encoding="utf-8")
    hit = knowledge.search("signing key")[0]
    assert hit.status == "changed" and "ninety" in hit.text
    (project / "guide.md").unlink()
    assert knowledge.search("signing key")[0].status == "missing"
    assert knowledge.source_fingerprint("guide.md") is not None
    assert knowledge.source_fingerprint("nothing.md") is None


def test_an_older_knowledge_file_without_a_revision_still_loads(memory):
    _, _, _, knowledge, project = memory
    (project / "old.md").write_text("legacy text here\n", encoding="utf-8")
    knowledge.add("old.md")
    import json

    data = json.loads((knowledge._file).read_text(encoding="utf-8"))
    knowledge._file.write_text(json.dumps(data["trees"]), encoding="utf-8")
    fresh = KnowledgeBase(
        project / ".nailong" / "knowledge", {"project": project}, project / ".nailong" / "work"
    )
    assert fresh.revision == 0 and [t.relative_path for t in fresh.trees()] == ["old.md"]
    fresh.add("old.md")
    assert fresh.revision == 1


def test_memory_command_and_tool_show_stale_items_and_dependents(project, home):
    (project / "spec.md").write_text("limit is 5\n", encoding="utf-8")
    code, out, err, setup = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("wiki_write", page="limits", text="Limit is 5.", why="spec", sources=["spec.md"]),
        call("memory_status", ref="file:spec.md"),
        final("done"),
        lines="Record\n/stale\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "Nothing in the project's memory is stale." in out
    seen = " ".join(str(item) for item in setup.model.calls[2].observations)
    assert "limits" in seen and "cited_by" in seen
    (project / "spec.md").write_text("limit is 9\n", encoding="utf-8")
    _, later, _, _ = run_cli(
        project, home, [], lines="/stale\n/stale wiki:limits\n/exit\n", interactive=True
    )
    assert "stale wiki: limits: spec.md (changed)" in later
    assert "kind=wiki" in later and "scope=authoritative" in later
