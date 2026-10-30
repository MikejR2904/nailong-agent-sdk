import asyncio
import json

import pytest
import yaml

from nailong_agent_sdk.specifications.documents import (
    DocumentFormat,
    DocumentNodeKind,
    SpecificationDocument,
    SpecificationManifest,
    VisionStatus,
)
from nailong_agent_sdk.specifications.preprocessing import (
    SpecificationPreprocessor,
    keywords_from_task,
)
from nailong_agent_sdk.specifications.vision import (
    ScriptedVisionAdapter,
    UnconfiguredVisionAdapter,
    VisionProposal,
)
from tests.support.document_cases import document, write
from tests.support.extended_paths import resolve_files_in_extended_spelling, windows_only
from tests.support.specs import node, tree


def arun(coro, timeout=60):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


def test_manifest_load_and_errors(tmp_path):
    write(
        tmp_path,
        "specification-manifest.yaml",
        yaml.safe_dump(
            {
                "documents": [
                    {
                        "id": "a",
                        "title": "A",
                        "format": "md",
                        "path": "a.md",
                        "category": "functional",
                    }
                ]
            }
        ),
    )
    pre = SpecificationPreprocessor(tmp_path)
    manifest = pre.load_manifest()
    assert manifest.documents[0].id == "a"
    categorised = {
        "documents": [
            {"id": "f", "title": "F", "format": "md", "path": "f.md", "category": "any-label"},
            {"id": "i", "title": "I", "format": "md", "path": "i.md", "category": "x.y_1"},
        ]
    }
    write(tmp_path, "categorised.yaml", yaml.safe_dump(categorised))
    loaded = pre.load_manifest("categorised.yaml")
    assert {d.id: d.category for d in loaded.documents} == {"f": "any-label", "i": "x.y_1"}
    write(tmp_path, "keyed.yaml", yaml.safe_dump({"functional_spec": {"documents": []}}))
    with pytest.raises(ValueError) as keyed:
        pre.load_manifest("keyed.yaml")
    assert str(keyed.value) == (
        'Specification manifest "keyed.yaml" must contain a "documents" list; '
        "its top-level keys are ['functional_spec']."
    )
    for bad in ("", " ", "has space", "-leading", "x" * 65):
        with pytest.raises(ValueError, match="category"):
            SpecificationDocument(id="x", title="x", format="md", path="x.md", category=bad)
    write(tmp_path, "list.yaml", "- 1\n- 2\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        pre.load_manifest("list.yaml")
    with pytest.raises(ValueError, match="escapes the configured root"):
        pre.load_manifest("../outside.yaml")
    with pytest.raises(ValueError):
        SpecificationDocument(id="x", title="x", format="md", path="../x.md", category="functional")
    for rooted in ("/abs.md", "\\abs.md", "C:abs.md", "C:/abs.md"):
        with pytest.raises(ValueError, match="must be relative"):
            SpecificationDocument(
                id="x", title="x", format="md", path=rooted, category="functional"
            )
    with pytest.raises(ValueError, match="unique"):
        SpecificationManifest(documents=[document("a.md", "md"), document("b.md", "md")])
    empty = write(tmp_path, "empty.yaml", "")
    with pytest.raises(ValueError, match="must be a mapping"):
        pre.load_manifest("empty.yaml")
    assert empty.exists()


@windows_only
def test_a_document_resolving_in_the_extended_spelling_stays_inside_the_root(tmp_path, monkeypatch):
    write(tmp_path, "a.md", "alpha\n")
    pre = SpecificationPreprocessor(tmp_path)
    resolve_files_in_extended_spelling(monkeypatch)
    result = pre.process_document(document("a.md", DocumentFormat.MD))
    assert [n.node_id for n in result.nodes] == ["line-1"]
    with pytest.raises(ValueError, match="escapes the configured root"):
        pre.load_manifest("../outside.yaml")


def test_text_nodes_locations_hashes_and_context(tmp_path):
    write(tmp_path, "a.md", "alpha\n\nbeta\ngamma\n")
    pre = SpecificationPreprocessor(tmp_path)
    result = pre.process_document(document("a.md", DocumentFormat.MD))
    assert [n.node_id for n in result.nodes] == ["line-1", "line-3", "line-4"]
    assert [n.source.location for n in result.nodes] == ["line:1", "line:3", "line:4"]
    assert result.nodes[1].surrounding_text == ["alpha", "gamma"]
    import hashlib

    assert result.source_hash == hashlib.sha256((tmp_path / "a.md").read_bytes()).hexdigest()
    assert all(n.source.source_hash == result.source_hash for n in result.nodes)
    write(tmp_path, "empty.md", "")
    only = pre.process_document(document("empty.md", DocumentFormat.MD))
    assert [n.node_id for n in only.nodes] == ["line-1"] and only.nodes[0].content == ""
    write(tmp_path, "dups.md", "same\nsame\nsame\n")
    pre.process_document(document("dups.md", DocumentFormat.MD))


def test_location_numbers_follow_newlines_not_splitlines(tmp_path):
    write(tmp_path, "a.md", "one\x0ctwo\nthree\u2028four\nfive\n")
    pre = SpecificationPreprocessor(tmp_path)
    result = pre.process_document(document("a.md", DocumentFormat.MD))
    locations = [n.source.location for n in result.nodes]
    assert locations == ["line:1", "line:2", "line:3"], locations


def test_resolve_images_rules(tmp_path):
    img = node(
        "image-1",
        {"filename": "p.png"},
        kind=DocumentNodeKind.IMAGE,
        vision_status=VisionStatus.PENDING,
    )
    text = node("line-1", "text")
    base = tree(nodes=[img, text])
    pre = SpecificationPreprocessor(tmp_path)
    good = ScriptedVisionAdapter([VisionProposal(confidence=0.8, structure={"signals": ["clk"]})])
    resolved = arun(pre.resolve_images(base, good))
    assert resolved.nodes[0].vision_status is VisionStatus.ACCEPTED
    assert resolved.nodes[0].resolved_structure == {"signals": ["clk"]}
    assert resolved.nodes[1] == text
    empty_structure = ScriptedVisionAdapter([VisionProposal(confidence=1.0, structure={})] * 3)
    review = arun(pre.resolve_images(base, empty_structure))
    assert review.nodes[0].vision_status is VisionStatus.REVIEW_REQUIRED
    below = ScriptedVisionAdapter([VisionProposal(confidence=0.79, structure={"a": 1})] * 3)
    assert (
        arun(pre.resolve_images(base, below)).nodes[0].vision_status is VisionStatus.REVIEW_REQUIRED
    )
    retry = ScriptedVisionAdapter(
        [
            VisionProposal(confidence=0.1, structure={"a": 1}),
            VisionProposal(confidence=0.9, structure={"b": 2}),
        ]
    )
    assert arun(pre.resolve_images(base, retry)).nodes[0].resolved_structure == {"b": 2}
    calls = []

    class Counting:
        async def extract(self, n):
            calls.append(1)
            return VisionProposal(confidence=0.0, structure={})

    arun(pre.resolve_images(base, Counting(), max_attempts=5))
    assert len(calls) == 5
    unconfigured = arun(pre.resolve_images(base, UnconfiguredVisionAdapter()))
    assert unconfigured.nodes[0].vision_status is VisionStatus.REVIEW_REQUIRED
    assert unconfigured.nodes[0].resolved_structure is None

    class Raising:
        async def extract(self, n):
            raise RuntimeError("vision backend down")

    with pytest.raises(RuntimeError, match="vision backend down"):
        arun(pre.resolve_images(base, Raising()))


def test_persist_tree_roundtrip(tmp_path):
    write(tmp_path, "a.md", "alpha\nbeta\n")
    pre = SpecificationPreprocessor(tmp_path)
    got = pre.process_document(document("a.md", DocumentFormat.MD))
    target = pre.persist_tree(got)
    assert target == tmp_path / "processed" / "d1.document.yaml"
    loaded = yaml.safe_load(target.read_text("utf-8"))
    assert loaded == json.loads(json.dumps(got.model_dump(mode="json")))


def test_vision_results_are_consumed_downstream(tmp_path):
    from nailong_agent_sdk.specifications.retrieval import specification_retrieval_documents

    img = node(
        "image-1",
        {"filename": "block.png"},
        kind=DocumentNodeKind.IMAGE,
        vision_status=VisionStatus.ACCEPTED,
        resolved_structure={"signals": ["rst_n_unique_token"]},
    )
    documents = specification_retrieval_documents("snap", [tree(nodes=[img])])
    assert any("rst_n_unique_token" in d.text for d in documents)


def test_keywords_from_task_ascii_only():
    assert keywords_from_task("Verify REQ-FUNC-001 and clk_div") == {
        "verify",
        "req-func-001",
        "and",
        "clk_div",
    }
