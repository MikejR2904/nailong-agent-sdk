from __future__ import annotations

import pytest

from nailong_cli.knowledge import KnowledgeBase
from nailong_cli.wiki import Wiki
from tests.conftest import call, final, run_cli, tool_names
from tests.make_documents import make_docx, make_pdf

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


@pytest.fixture
def base(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return KnowledgeBase(root / ".nailong" / "knowledge", {"project": root}, root / "work"), root


def test_files_are_split_into_passages_that_say_where_they_came_from(base):
    kb, root = base
    (root / "docs").mkdir()
    (root / "docs" / "auth.md").write_text(
        "# Auth\n\nTokens expire after fifteen minutes.\n\nRefresh tokens last a week.\n",
        encoding="utf-8",
    )
    make_pdf(root / "docs" / "ops.pdf", ["Backups run nightly", "Restores need a ticket"])
    make_docx(root / "docs" / "review.docx")
    result = kb.add("docs")
    assert result["added"] == 3 and result["skipped"] == []
    hit = kb.search("restores ticket")[0]
    assert hit.path == "docs/ops.pdf" and hit.location == "page 2" and "ticket" in hit.text
    assert kb.search("tokens expire")[0].path == "docs/auth.md"
    assert kb.search("tokens expire")[0].location.startswith("lines ")
    assert kb.search("sales north")[0].path == "docs/review.docx"


def test_the_list_shows_when_a_source_changed_or_vanished(base):
    kb, root = base
    (root / "a.md").write_text("alpha text", encoding="utf-8")
    (root / "b.md").write_text("beta text", encoding="utf-8")
    kb.add("a.md")
    kb.add("b.md")
    assert all("current" in line for line in kb.listing())
    (root / "a.md").write_text("alpha changed", encoding="utf-8")
    (root / "b.md").unlink()
    listing = "\n".join(kb.listing())
    assert (
        "a.md: 1 passage(s), changed since added" in listing
        and "b.md: 1 passage(s), missing" in listing
    )
    result = kb.refresh()
    assert result["dropped"] == ["b.md"] and kb.search("alpha changed")[0].path == "a.md"


def test_adding_removing_and_bad_input_are_explained(base):
    kb, root = base
    with pytest.raises(ValueError, match="knowledge base is empty"):
        kb.search("anything")
    (root / "ok.md").write_text("fine text here", encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="not a file or folder inside the project"):
        kb.add("../outside")
    with pytest.raises(ValueError, match="has no files the knowledge base reads"):
        kb.add("image.png")
    kb.add("ok.md")
    assert kb.remove("ok.md") == 1
    with pytest.raises(ValueError, match='Nothing in the knowledge base is named "ok.md"'):
        kb.remove("ok.md")


def test_a_file_that_cannot_be_read_is_skipped_and_named_not_fatal(base):
    kb, root = base
    (root / "good.md").write_text("good text", encoding="utf-8")
    (root / "empty.md").write_text("   \n", encoding="utf-8")
    result = kb.add(".")
    assert result["added"] == 1 and any(
        "empty.md: no text found" in item for item in result["skipped"]
    )


def test_the_agent_searches_the_knowledge_base_and_the_command_manages_it(project, home):
    (project / "guide.md").write_text(
        "The deploy key rotates every ninety days.\n", encoding="utf-8"
    )
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("knowledge_search", query="deploy key rotates"),
        final("done"),
        lines="/kb add guide.md\n/kb\n/kb search deploy key\nWhen does the key rotate?\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "added 1 file(s)" in out and "guide.md: 1 passage(s), current" in out
    assert "guide.md (lines 1-1)" in out
    assert "knowledge_search" in tool_names(setup.model.calls[0])
    seen = str(setup.model.calls[-1].observations)
    assert "ninety days" in seen and "guide.md" in seen


def test_pages_are_written_linked_indexed_and_logged(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    first = wiki.write(
        "auth/tokens", "# Tokens\n\nSee [[auth/sessions]] and [[billing]].", "first notes"
    )
    assert first == {
        "page": "auth/tokens",
        "created": True,
        "revision": 1,
        "broken_links": ["auth/sessions", "billing"],
    }
    wiki.write("auth/sessions", "# Sessions\n\nBack to [[auth/tokens]].", "sessions too")
    again = wiki.write(
        "auth/tokens", "# Tokens\n\nNow only [[auth/sessions]].", "trimmed", base_revision=1
    )
    assert again["created"] is False and again["broken_links"] == []
    page = wiki.read("auth/tokens")
    assert page["links_to"] == ["auth/sessions"] and page["linked_from"] == ["auth/sessions"]
    index = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[auth/tokens]]: Tokens" in index and "[[auth/sessions]]: Sessions" in index
    log = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("\n- ") == 3 and "updated [[auth/tokens]] r2 by main: trimmed" in log


def test_wiki_search_ranks_by_mentions_and_page_names(tmp_path):
    wiki = Wiki(tmp_path)
    wiki.write("deploys", "Deploys go through staging first.", "a")
    wiki.write("misc", "Mentions deploys once.", "b")
    hits = wiki.search("deploys staging")
    assert [hit["page"] for hit in hits] == ["deploys", "misc"]
    with pytest.raises(ValueError, match="at least one word"):
        wiki.search("x")


@pytest.mark.parametrize(
    ("page", "text", "fragment"),
    [
        ("Bad Name", "x", "must be lowercase letters"),
        ("../escape", "x", "must be lowercase letters"),
        ("index", "x", "kept by the wiki itself"),
        ("fine", "  ", "cannot be empty"),
        ("fine", "x" * 20_001, "limit is 20000"),
    ],
)
def test_bad_pages_are_refused_with_the_reason(tmp_path, page, text, fragment):
    with pytest.raises(ValueError, match=fragment):
        Wiki(tmp_path).write(page, text, "why")


def test_reading_a_missing_page_lists_the_pages(tmp_path):
    wiki = Wiki(tmp_path)
    wiki.write("real", "text", "a")
    with pytest.raises(ValueError, match=r'There is no wiki page "ghost"; pages: \[.*real'):
        wiki.read("ghost")


def test_the_agent_keeps_a_wiki_and_planning_mode_cannot_write_to_it(project, home):
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("wiki_write", page="decisions", text="# Decisions\nUse queues.", why="from review"),
        final("done"),
        lines="Record it\n/wiki\n/wiki decisions\n/wiki search queues\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert (project / ".nailong" / "wiki" / "decisions.md").is_file()
    assert "decisions" in out and "Use queues." in out
    _, _, _, planning = run_cli(
        project,
        home,
        ["--permission-mode", "plan"],
        final(),
        lines="Plan\n/exit\n",
        interactive=True,
    )
    names = tool_names(planning.model.calls[0])
    assert "wiki_read" in names and "wiki_search" in names and "wiki_write" not in names
    assert "wiki_write" in tool_names(setup.model.calls[0])
