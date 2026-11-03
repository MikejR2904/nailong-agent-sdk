import json

import pytest

from nailong_agent_sdk.agent.openai_compatible import OpenAICompatibleEndpoint
from nailong_agent_sdk.starter import OPENAI_COMPATIBLE_BASE_URLS, Agent, openai_chat
from tests.support.openai_provider import chat


def local_agent(tmp_path, url, **options):
    setup = openai_chat(
        base_url=url, api_key=None, model="local-model", allow_insecure_http=True, **options
    )
    return Agent(
        model=setup,
        instructions="Answer briefly.",
        workspace=tmp_path / "workspace",
        max_steps=4,
    )


def test_a_server_that_needs_no_key_is_sent_no_authorization_header(server, tmp_path):
    url, state = server
    state["script"] = [
        chat(
            content=json.dumps({"type": "final", "output": {"status": "complete", "answer": "ok"}})
        )
    ]
    answer = local_agent(tmp_path, url).run_sync("Say ok.")
    assert answer.ok and answer.text == "ok"
    headers = {key.lower() for key in state["requests"][0]["headers"]}
    assert "authorization" not in headers


def test_a_reasoning_block_before_the_answer_is_not_part_of_the_answer(server, tmp_path):
    url, state = server
    reply = "<think>The user wants a greeting. Say hi.</think>\n\nHello from the local model."
    state["script"] = [chat(content=reply)]
    answer = local_agent(tmp_path, url).run_sync("Greet me.")
    assert answer.ok and answer.text == "Hello from the local model."


def test_a_reasoning_block_before_a_json_turn_is_dropped(server, tmp_path):
    url, state = server
    turn = json.dumps({"type": "final", "output": {"status": "complete", "answer": "four"}})
    state["script"] = [chat(content=f"<think>2+2 is 4</think>{turn}")]
    answer = local_agent(tmp_path, url).run_sync("2+2?")
    assert answer.ok and answer.text == "four"


def test_a_reply_that_is_only_reasoning_is_asked_again(server, tmp_path):
    url, state = server
    state["script"] = [
        chat(content="<think>hmm</think>"),
        chat(content="Second try worked."),
    ]
    answer = local_agent(tmp_path, url).run_sync("Anything.")
    assert answer.ok and answer.text == "Second try worked."
    assert len(state["requests"]) == 2


def test_the_key_is_still_required_unless_the_caller_says_there_is_none():
    with pytest.raises(ValueError, match="api_key must be non-empty"):
        OpenAICompatibleEndpoint(base_url="https://example.test/v1", api_key="")
    with pytest.raises(ValueError, match="api_key must be non-empty"):
        openai_chat(base_url="https://example.test/v1", api_key="", model="m")
    openai_chat(base_url="https://example.test/v1", api_key=None, model="m")


def test_the_usual_local_servers_have_shortcuts():
    for name, port in (("ollama", 11434), ("vllm", 8000), ("lmstudio", 1234), ("llamacpp", 8080)):
        assert OPENAI_COMPATIBLE_BASE_URLS[name] == f"http://localhost:{port}/v1"
