from nailong_agent_sdk.specifications.documents import SpecificationDocument


def document(path, fmt, doc_id="d1", category="functional"):
    return SpecificationDocument(id=doc_id, title=doc_id, format=fmt, path=path, category=category)


def write(root, name, content, binary=False):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        target.write_bytes(content)
    else:
        target.write_text(content, "utf-8")
    return target
