from __future__ import annotations

import asyncio

import pytest

from nailong_cli.documents import DocumentReader, document_tools
from tests.conftest import call, final, run_cli, tool_names
from tests.make_documents import make_docx, make_pdf, make_xlsx

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


@pytest.fixture
def reader(tmp_path):
    return DocumentReader({"project": tmp_path}, tmp_path / "work")


def test_a_pdf_is_read_a_page_at_a_time_with_its_page_numbers(tmp_path, reader):
    filler = " filler" * 60
    make_pdf(
        tmp_path / "paper.pdf",
        [f"Alpha page text{filler}", f"Beta page text{filler}", f"Gamma page text{filler}"],
    )
    first = reader.read("paper.pdf", 1, 500)
    assert first["format"] == "pdf" and first["total"] == 3 and first["unit"] == "page"
    assert "[page 1]" in first["text"] and "Alpha page text" in first["text"]
    assert first["next"] == 2 and first["sha256"]
    middle = reader.read("paper.pdf", 2, 20_000)
    assert middle["from"] == 2 and middle["to"] == 3 and middle["next"] is None
    assert "Beta page text" in middle["text"] and "Gamma page text" in middle["text"]


def test_a_page_without_a_text_layer_is_called_out(tmp_path, reader):
    make_pdf(tmp_path / "scan.pdf", ["Has text", ""])
    result = reader.read("scan.pdf", 1, 20_000)
    assert result["no_text_layer"] == [2] and "interpret_page" in result["note"]


def test_searching_a_document_ranks_the_pages_that_mention_the_words(tmp_path, reader):
    make_pdf(
        tmp_path / "paper.pdf", ["nothing here", "attention kernels attention", "kernels once"]
    )
    hits = reader.search("paper.pdf", "attention kernels")["hits"]
    assert [hit["page"] for hit in hits] == [2, 3]
    assert "attention" in hits[0]["snippet"]
    with pytest.raises(ValueError, match="at least one word"):
        reader.search("paper.pdf", "a")


def test_word_files_keep_headings_and_tables(tmp_path, reader):
    make_docx(tmp_path / "review.docx")
    text = reader.read("review.docx")["text"]
    assert "# Quarterly review" in text and "Revenue grew" in text
    assert "[table 1]" in text and "North | 420" in text


def test_workbooks_give_one_sheet_per_unit_and_csv_gives_rows(tmp_path, reader):
    make_xlsx(tmp_path / "budget.xlsx")
    sheets = reader.read("budget.xlsx")
    assert sheets["unit"] == "sheet" and sheets["total"] == 2
    assert "Servers\t1200" in sheets["text"] and "Remember the audit" in sheets["text"]
    (tmp_path / "rows.csv").write_text("name,qty\nbolts,40\nnuts,12\n", encoding="utf-8")
    assert "bolts\t40" in reader.read("rows.csv")["text"]


def test_problems_are_named_with_the_file_and_the_reason(tmp_path, reader):
    make_pdf(tmp_path / "p.pdf", ["x"])
    (tmp_path / "notes.txt").write_text("plain", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    with pytest.raises(ValueError, match='There is no file "nope.pdf"'):
        reader.read("nope.pdf")
    with pytest.raises(ValueError, match="not a document type read_document handles"):
        reader.read("notes.txt")
    with pytest.raises(ValueError, match="p.pdf has 1 page"):
        reader.read("p.pdf", 5)
    with pytest.raises(ValueError, match="denied credential path"):
        reader.read(".env")
    outside = tmp_path.parent / "outside.pdf"
    make_pdf(outside, ["secret"])
    with pytest.raises(ValueError, match="There is no file"):
        reader.read(str(outside))


def test_a_directory_added_to_the_session_can_be_read_by_name(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    make_pdf(extra / "ref.pdf", ["from the extra directory"])
    reader = DocumentReader({"project": tmp_path / "p", "extra": extra}, tmp_path / "w")
    assert "from the extra directory" in reader.read("ref.pdf")["text"]


def test_a_page_is_rendered_to_an_image_inside_the_workspace(tmp_path, reader):
    make_pdf(tmp_path / "paper.pdf", ["A diagram page"])
    image = reader.render("paper.pdf", 1)
    assert image.suffix == ".png" and image.is_file() and image.read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "work") in image.parents
    with pytest.raises(ValueError, match="page must be 1 to 1"):
        reader.render("paper.pdf", 2)
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert reader.render("pic.png", 1) == (tmp_path / "pic.png").resolve()


def test_interpret_page_hands_the_rendered_image_and_the_question_to_the_vision_model(
    tmp_path, reader
):
    make_pdf(tmp_path / "paper.pdf", ["Figure 3"])
    seen = []

    async def vision(image, question):
        seen.append((image.name, question))
        return "a bar chart with three bars"

    tools = {item.name: item for item in document_tools(reader, vision)}
    result = asyncio.run(
        tools["interpret_page"].run({"path": "paper.pdf", "question": "What does it show?"})
    )
    assert result.status == "succeeded" and result.output == "a bar chart with three bars"
    assert seen[0][1] == "What does it show?" and seen[0][0].endswith("-p1.png")


def test_the_agent_in_the_cli_reads_a_local_pdf_and_can_search_it(project, home):
    make_pdf(project / "paper.pdf", ["Intro text", "The kernel tiles the matrix"])
    code, out, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("search_document", path="paper.pdf", query="kernel tiles"),
        call("read_document", path="paper.pdf", start=2),
        final("done"),
        lines="Summarise the paper\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    first = setup.model.calls[0]
    assert {"read_document", "search_document", "interpret_page"} <= set(tool_names(first))
    seen = str(setup.model.calls[-1].observations)
    assert "kernel" in seen and "[page 2]" in seen


def test_a_powerpoint_file_is_read_slide_by_slide_with_tables_and_notes(tmp_path):
    from tests.make_documents import make_pptx

    make_pptx(tmp_path / "deck.pptx")
    reader = DocumentReader({"project": tmp_path}, tmp_path)
    result = reader.read("deck.pptx")
    assert result["format"] == "pptx" and result["total"] == 2 and result["unit"] == "slide"
    text = result["text"]
    assert text.index("Launch plan") < text.index("Results")
    assert "Ship in March" in text and "[notes]\nMention the budget" in text
    assert "Region | Sales" in text and "North | 420" in text
    hits = reader.search("deck.pptx", "budget")["hits"]
    assert [hit["page"] if "page" in hit else hit.get("slide") for hit in hits][:1] == [1]


def test_a_broken_powerpoint_file_is_refused_by_name(tmp_path):
    (tmp_path / "bad.pptx").write_bytes(b"not a zip")
    reader = DocumentReader({"project": tmp_path}, tmp_path)
    try:
        reader.read("bad.pptx")
    except ValueError as error:
        assert "not a PowerPoint package" in str(error)
    else:
        raise AssertionError("expected a refusal")


class FakeFetched:
    def __init__(self, body, content_type, url="https://arxiv.org/pdf/2604.01658"):
        self.body, self.content_type, self.url = body, content_type, url
        self.charset, self.status, self.deadline_reached = None, 200, False


def fake_download(monkeypatch, fetched):
    def get(url, tool_name, *, byte_limit_for, deadline_seconds):
        byte_limit_for(fetched.content_type)
        return fetched

    monkeypatch.setattr("nailong_agent_sdk.tools.core.helpers._http_get_public", get)


def test_fetch_document_saves_a_pdf_in_the_workspace_and_the_path_can_be_read(
    tmp_path, monkeypatch
):
    pdf = make_pdf(tmp_path / "source.pdf", ["Abstract of the paper", "Second page"])
    fake_download(monkeypatch, FakeFetched(pdf.read_bytes(), "application/pdf"))
    workspace = tmp_path / ".nailong" / "workspace"
    reader = DocumentReader({"project": tmp_path}, workspace)
    got = reader.fetch("https://arxiv.org/pdf/2604.01658")
    assert got["path"].startswith(".nailong/workspace/downloads/arxiv.org-")
    assert got["path"].endswith(".pdf") and got["bytes"] == pdf.stat().st_size
    assert "Abstract of the paper" in reader.read(got["path"])["text"]


def test_fetch_document_refuses_web_pages_and_fake_pdfs_by_name(tmp_path, monkeypatch):
    reader = DocumentReader({"project": tmp_path}, tmp_path / "ws")
    fake_download(monkeypatch, FakeFetched(b"<html>", "text/html", "https://example.org/x"))
    with pytest.raises(ValueError, match="not a PDF, Word, Excel, PowerPoint or CSV file"):
        reader.fetch("https://example.org/x")
    fake_download(monkeypatch, FakeFetched(b"nope", "application/pdf"))
    with pytest.raises(ValueError, match="does not start with %PDF"):
        reader.fetch("https://example.org/x.pdf")


def test_a_url_given_to_read_document_says_to_use_fetch_document(tmp_path):
    reader = DocumentReader({"project": tmp_path}, tmp_path / "ws")
    with pytest.raises(ValueError, match="Call fetch_document with it first"):
        reader.read("https://arxiv.org/pdf/2604.01658")


def test_fetch_document_is_offered_only_with_web_and_needs_approval(project, home):
    _, _, _, off = run_cli(project, home, [], final(), lines="Hi\n/exit\n", interactive=True)
    assert "fetch_document" not in tool_names(off.model.calls[0])
    _, _, _, on = run_cli(project, home, ["--web"], final(), lines="Hi\n/exit\n", interactive=True)
    assert "fetch_document" in tool_names(on.model.calls[0])
