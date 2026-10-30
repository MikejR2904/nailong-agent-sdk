def build_pdf(pages):
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_object = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
                % (pages_object, content, font)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_object
    kids = b" ".join(b"%d 0 R" % page for page in page_ids)
    objects[pages_object - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref,
    )
    return bytes(output)
