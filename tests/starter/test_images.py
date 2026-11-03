import base64
import json

import pytest

from nailong_agent_sdk.starter import Agent, anthropic_messages, openai_chat
from nailong_agent_sdk.starter.images import MAX_IMAGE_BYTES, load_image
from tests.support.model_context import FAKE_KEY

ANSWER = {"type": "final", "output": {"status": "complete", "answer": "a red square"}}
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def picture(tmp_path, name="shot.png", data=PIXEL):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_claude_receives_the_picture_as_an_image_block_not_as_text(server, tmp_path):
    url, state = server
    state["script"] = [
        {
            "status": 200,
            "body": {
                "id": "m",
                "type": "message",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(ANSWER)}],
            },
        }
    ]
    setup = anthropic_messages(
        api_key=FAKE_KEY, model="claude-test", base_url=url, allow_insecure_http=True
    )
    with Agent(model=setup, instructions="Describe.", workspace=tmp_path / "w") as agent:
        answer = agent.run_sync("What is in the picture?", images=[picture(tmp_path)])
    assert answer.ok and answer.text == "a red square"
    first = state["requests"][0]["json"]["messages"][0]["content"]
    assert first[0]["type"] == "text" and first[1]["type"] == "image"
    assert first[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(PIXEL).decode("ascii"),
    }
    assert "1 image(s) are attached" in first[0]["text"]
    assert base64.b64encode(PIXEL).decode("ascii") not in first[0]["text"]


def test_openai_style_providers_receive_a_data_url(server, tmp_path):
    url, state = server
    state["script"] = [
        {
            "status": 200,
            "body": {
                "id": "c",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(ANSWER)},
                    }
                ],
            },
        }
    ]
    setup = openai_chat(
        base_url=url + "/v1", api_key=FAKE_KEY, model="vision-model", allow_insecure_http=True
    )
    with Agent(model=setup, instructions="Describe.", workspace=tmp_path / "w") as agent:
        assert agent.run_sync("Look.", images=[picture(tmp_path)]).ok
    content = state["requests"][0]["json"]["messages"][1]["content"]
    assert content[0]["type"] == "text" and content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_without_images_the_request_is_unchanged(server, tmp_path):
    url, state = server
    state["script"] = [
        {
            "status": 200,
            "body": {
                "id": "m",
                "type": "message",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(ANSWER)}],
            },
        }
    ]
    setup = anthropic_messages(
        api_key=FAKE_KEY, model="claude-test", base_url=url, allow_insecure_http=True
    )
    with Agent(model=setup, instructions="x", workspace=tmp_path / "w") as agent:
        agent.run_sync("Hi.")
    first = state["requests"][0]["json"]["messages"][0]["content"]
    assert len(first) == 1 and "attached_images" not in first[0]["text"]


@pytest.mark.parametrize(
    ("name", "size", "fragment"),
    [
        ("notes.txt", 1, "is not a supported image type"),
        ("big.png", MAX_IMAGE_BYTES + 1, "the limit for one image"),
    ],
)
def test_bad_images_are_refused_with_the_reason(tmp_path, name, size, fragment):
    with pytest.raises(ValueError, match=fragment):
        load_image(picture(tmp_path, name, b"x" * size))


def test_missing_files_and_too_many_images_are_named(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_image(tmp_path / "gone.png")
    from nailong_agent_sdk.starter.images import with_images

    with pytest.raises(ValueError, match="the limit is 10"):
        with_images({}, [{"media_type": "image/png", "data": "AA=="}] * 11)
