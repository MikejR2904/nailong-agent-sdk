from nailong_agent_sdk.specifications.documents import (
    DocumentFormat,
    DocumentNode,
    DocumentNodeKind,
    DocumentTree,
    SourceRef,
)


def sref(doc="d1", loc="line:1", digest="a" * 64, fmt=DocumentFormat.MD, path="d1.md"):
    return SourceRef(
        document_id=doc, relative_path=path, source_hash=digest, format=fmt, location=loc
    )


def node(
    node_id, content, kind=DocumentNodeKind.TEXT, doc="d1", loc=None, digest="a" * 64, **extra
):
    return DocumentNode(
        node_id=node_id,
        kind=kind,
        source=sref(doc=doc, loc=loc or f"line:{node_id}", digest=digest),
        content=content,
        **extra,
    )


def tree(doc_id="d1", nodes=(), category="functional", digest="a" * 64):
    return DocumentTree(
        document_id=doc_id,
        category=category,
        title=doc_id,
        format=DocumentFormat.MD,
        relative_path=f"{doc_id}.md",
        source_hash=digest,
        nodes=list(nodes),
    )
