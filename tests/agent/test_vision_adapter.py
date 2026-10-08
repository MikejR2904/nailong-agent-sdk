import asyncio
import base64
import hashlib
import json

import pytest

from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleEndpoint,
    OpenAICompatibleVisionAdapter,
    SourceVerifiedImageLoader,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.specifications.documents import (
    DocumentFormat,
    DocumentNode,
    DocumentNodeKind,
    SourceRef,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


def image_node(
    path="figure.png",
    data=PNG,
    format_=DocumentFormat.PNG,
    kind=DocumentNodeKind.IMAGE,
    digest=None,
):
    return DocumentNode(
        node_id="image-1",
        kind=kind,
        source=SourceRef(
            document_id="d1",
            relative_path=path,
            source_hash=digest or hashlib.sha256(data).hexdigest(),
            format=format_,
            location="image:1",
        ),
        content={"filename": path},
    )


def endpoint():
    return OpenAICompatibleEndpoint(base_url="https://provider.test/v1", api_key="k" * 12)


class Transport:
    def __init__(self, content):
        self.content = content
        self.requests = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.requests.append((url, headers, payload, timeout_seconds))
        return {"choices": [{"message": {"content": self.content}}]}


def adapter(transport, **options):
    return OpenAICompatibleVisionAdapter(
        endpoint(), model="vision-1", image_loader=lambda node: PNG, transport=transport, **options
    )


def extract(subject, node=None):
    return asyncio.run(subject.extract(node or image_node()))


def test_the_loader_returns_only_bytes_that_match_the_frozen_source_hash(tmp_path):
    (tmp_path / "figure.png").write_bytes(PNG)
    loader = SourceVerifiedImageLoader(str(tmp_path))
    assert loader(image_node()) == PNG
    (tmp_path / "figure.png").write_bytes(PNG + b"tampered")
    with pytest.raises(ValueError, match="hash does not match the frozen document node"):
        loader(image_node())


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (image_node(kind=DocumentNodeKind.TEXT), "accepts only image document nodes"),
        (image_node(format_=DocumentFormat.PDF), "supports only PNG and JPEG documents"),
        (image_node(path="../outside.png"), "escapes the specification root"),
        (image_node(path="missing.png"), "source document does not exist"),
    ],
)
def test_the_loader_refuses_nodes_it_cannot_authenticate(tmp_path, node, message):
    (tmp_path / "figure.png").write_bytes(PNG)
    with pytest.raises(ValueError, match=message):
        SourceVerifiedImageLoader(str(tmp_path))(node)


def test_the_loader_enforces_its_size_limit_and_validates_its_configuration(tmp_path):
    (tmp_path / "figure.png").write_bytes(PNG)
    with pytest.raises(ValueError, match="source exceeds the configured byte limit"):
        SourceVerifiedImageLoader(str(tmp_path), max_image_bytes=10)(image_node())
    with pytest.raises(ValueError, match="max_image_bytes must be positive"):
        SourceVerifiedImageLoader(str(tmp_path), max_image_bytes=0)
    with pytest.raises(ValueError, match="specification_root must be an existing directory"):
        SourceVerifiedImageLoader(str(tmp_path / "nowhere"))


def test_the_adapter_sends_the_image_as_a_data_url_and_returns_the_typed_proposal():
    reply = {"confidence": 0.9, "structure": {"signals": ["clk"]}, "errors": []}
    transport = Transport(json.dumps(reply))
    proposal = extract(adapter(transport, parameters={"temperature": 0}))
    assert (proposal.confidence, proposal.structure, proposal.errors) == (
        0.9,
        reply["structure"],
        [],
    )
    ((url, headers, payload, timeout),) = transport.requests
    assert url == "https://provider.test/v1/chat/completions" and timeout == 30.0
    assert headers["Authorization"].startswith("Bearer ")
    assert payload["model"] == "vision-1" and payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    image = payload["messages"][1]["content"][1]["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


def test_a_reply_that_is_not_json_is_an_error_carrying_the_start_of_the_reply():
    with pytest.raises(AgentSdkError) as raised:
        extract(adapter(Transport("I see a diagram")))
    assert raised.value.code == "OPENAI_COMPATIBLE_VISION_INVALID"
    assert "not valid JSON" in str(raised.value)
    assert raised.value.details["raw_content"] == "I see a diagram"


def test_a_reply_that_breaks_the_proposal_contract_names_each_field():
    reply = json.dumps({"confidence": 3, "structure": "a diagram"})
    with pytest.raises(AgentSdkError) as raised:
        extract(adapter(Transport(reply)))
    assert raised.value.code == "OPENAI_COMPATIBLE_VISION_INVALID"
    assert "confidence" in str(raised.value) and "structure" in str(raised.value)
    assert len(raised.value.details["field_errors"]) == 2


@pytest.mark.parametrize(
    ("loaded", "message"),
    [
        (b"", "must return non-empty bytes"),
        ("text", "must return non-empty bytes"),
        (PNG * 3, "exceeds the configured byte limit"),
    ],
)
def test_the_adapter_checks_what_the_loader_returned(loaded, message):
    subject = OpenAICompatibleVisionAdapter(
        endpoint(),
        model="vision-1",
        image_loader=lambda node: loaded,
        transport=Transport("{}"),
        max_image_bytes=len(PNG),
    )
    with pytest.raises(ValueError, match=message):
        extract(subject)


def test_the_adapter_accepts_only_image_nodes_and_validates_its_configuration():
    subject = adapter(Transport("{}"))
    with pytest.raises(ValueError, match="accepts only image document nodes"):
        extract(subject, image_node(kind=DocumentNodeKind.TEXT))
    with pytest.raises(ValueError, match="vision model identifier must be non-empty"):
        OpenAICompatibleVisionAdapter(endpoint(), model=" ", image_loader=lambda node: PNG)
    with pytest.raises(ValueError, match="max_image_bytes must be positive"):
        OpenAICompatibleVisionAdapter(
            endpoint(), model="vision-1", image_loader=lambda node: PNG, max_image_bytes=0
        )
