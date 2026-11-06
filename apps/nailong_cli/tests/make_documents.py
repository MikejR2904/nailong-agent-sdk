"""Small real documents for the tests: a text-layer PDF, a Word file, a workbook."""

from __future__ import annotations

from pathlib import Path


def make_pdf(path: Path, pages: list[str]) -> Path:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1") if text else b""
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>".encode()
        )
        kids.append(page)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    refs = " ".join(f"{kid} 0 R" for kid in kids)
    objects[tree - 1] = f"<< /Type /Pages /Kids [{refs}] /Count {len(kids)} >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{start}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(output))
    return path


def make_docx(path: Path) -> Path:
    from docx import Document

    document = Document()
    document.add_heading("Quarterly review", level=1)
    document.add_paragraph("Revenue grew while costs stayed flat.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Sales"
    table.cell(1, 0).text = "North"
    table.cell(1, 1).text = "420"
    document.save(path)
    return path


def make_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    first = book.active
    first.title = "Budget"
    first.append(["Item", "Cost"])
    first.append(["Servers", 1200])
    second = book.create_sheet("Notes")
    second.append(["Remember the audit"])
    book.save(path)
    return path


def make_pptx(path: Path) -> Path:
    import zipfile

    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"

    def slide(*paragraphs: str, table: tuple[tuple[str, ...], ...] = ()) -> str:
        body = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in paragraphs)
        rows = "".join(
            "<a:tr>"
            + "".join(f"<a:tc><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:tc>" for c in row)
            + "</a:tr>"
            for row in table
        )
        grid = f"<a:tbl>{rows}</a:tbl>" if rows else ""
        return f'<p:sld xmlns:a="{drawing}" xmlns:p="p"><p:txBody>{body}{grid}</p:txBody></p:sld>'

    relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    notes_rel = (
        f'<Relationships xmlns="{relationships}"><Relationship Id="r1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" '
        'Target="../notesSlides/notesSlide1.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/slides/slide2.xml", slide("Results", table=(("Region", "Sales"), ("North", "420")))
        )
        archive.writestr("ppt/slides/slide1.xml", slide("Launch plan", "Ship in March"))
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", notes_rel)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", slide("Mention the budget"))
    return path
