import json
import ssl

import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.openai_compatible import OpenAICompatibleAgentModel
from nailong_agent_sdk.agent.openai_compatible import transport as transport_module
from nailong_agent_sdk.agent.openai_compatible.transport import (
    HttpxJsonTransport,
    UrlLibJsonTransport,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import arun, definition, final, task
from tests.support.model_context import FAKE_KEY, model_context
from tests.support.openai_provider import chat, endpoint, model_for, run_agent


@pytest.mark.parametrize(
    "item, code, transient",
    [
        (
            {"status": 401, "body": {"error": {"message": "Incorrect API key provided"}}},
            "OPENAI_COMPATIBLE_HTTP_ERROR",
            False,
        ),
        ({"status": 404, "body": b"not found"}, "OPENAI_COMPATIBLE_HTTP_ERROR", False),
        (
            {"status": 400, "body": {"error": {"message": "unsupported parameter: foo"}}},
            "OPENAI_COMPATIBLE_HTTP_ERROR",
            False,
        ),
        (
            {"status": 429, "body": b"slow", "headers": {"Retry-After": "7"}},
            "OPENAI_COMPATIBLE_HTTP_ERROR",
            True,
        ),
        ({"status": 503, "body": b"down"}, "OPENAI_COMPATIBLE_HTTP_ERROR", True),
        (
            {"status": 302, "body": b"", "headers": {"Location": "http://elsewhere/"}},
            "OPENAI_COMPATIBLE_HTTP_ERROR",
            False,
        ),
    ],
)
def test_http_status_mapping_for_both_transports(server, item, code, transient):
    url, state = server
    for transport in (UrlLibJsonTransport(), HttpxJsonTransport()):
        state["script"] = [dict(item)]
        subject = model_for(url, transport)
        with pytest.raises(AgentSdkError) as excinfo:
            arun(subject.next_turn(model_context()))
        assert excinfo.value.code == code
        assert isinstance(excinfo.value, TransientProviderError) is transient, (
            type(transport).__name__,
            excinfo.value,
        )
        assert f"HTTP {item['status']}" in str(excinfo.value)
        if item["status"] == 429:
            assert excinfo.value.retry_after_seconds == 7.0


def test_http_error_message_carries_the_provider_body(server):
    url, state = server
    state["script"] = [
        {"status": 400, "body": {"error": {"message": "unsupported parameter: foo"}}}
    ]
    with pytest.raises(AgentSdkError) as excinfo:
        arun(model_for(url).next_turn(model_context()))
    assert "unsupported parameter: foo" in str(excinfo.value), str(excinfo.value)


@pytest.mark.parametrize("transport_name", ["urllib", "httpx"])
def test_html_ok_response_timeouts_resets_and_dead_hosts(server, transport_name):
    url, state = server
    transport = UrlLibJsonTransport() if transport_name == "urllib" else HttpxJsonTransport()
    state["script"] = [
        {"status": 200, "body": b"<html>captive portal</html>", "content_type": "text/html"}
    ]
    with pytest.raises(AgentSdkError) as html_error:
        arun(model_for(url, transport).next_turn(model_context()))
    assert html_error.value.code == "OPENAI_COMPATIBLE_RESPONSE_INVALID" and not isinstance(
        html_error.value, TransientProviderError
    )
    state["script"] = [{"delay": 2.5, "body": chat(content="{}")["body"]}]
    with pytest.raises(TransientProviderError) as slow:
        arun(model_for(url, transport, timeout=1.0).next_turn(model_context()))
    assert (
        slow.value.code == "OPENAI_COMPATIBLE_TRANSPORT_TIMEOUT"
        and "did not respond within 1.0s" in str(slow.value)
    )
    dead = OpenAICompatibleAgentModel(
        endpoint("http://127.0.0.1:9/v1", timeout=2.0),
        provider="fake",
        model="fake-1",
        transport=transport,
    )
    with pytest.raises(TransientProviderError) as refused:
        arun(dead.next_turn(model_context()))
    assert refused.value.code in {
        "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
        "OPENAI_COMPATIBLE_TRANSPORT_TIMEOUT",
    }


@pytest.mark.parametrize("transport_name", ["urllib", "httpx"])
@pytest.mark.parametrize("failure", ["drop", "short_body"])
def test_connection_failures_after_the_request_are_transient(server, transport_name, failure):
    url, state = server
    transport = UrlLibJsonTransport() if transport_name == "urllib" else HttpxJsonTransport()
    if failure == "drop":
        state["script"] = [{"drop": True}]
    else:
        state["script"] = [{"short_body": True}]
    try:
        arun(model_for(url, transport).next_turn(model_context()))
        outcome = "returned"
    except TransientProviderError as error:
        outcome = f"TransientProviderError:{error.code}"
    except Exception as error:
        outcome = f"{type(error).__name__}"
    assert outcome.startswith("TransientProviderError"), outcome


def test_dropped_connection_is_retried_by_the_agent_loop(server):
    url, state = server
    state["script"] = [{"drop": True}, chat(content=json.dumps(final()))]
    result = run_agent(url, definition(max_iterations=3))
    assert result.status is AgentRunStatus.COMPLETED, result.reason


def test_api_key_with_trailing_newline_is_not_echoed_in_errors(server):
    url, state = server
    state["script"] = [chat(content=json.dumps(final()))]
    subject = BaseAgent(
        definition(),
        OpenAICompatibleAgentModel(
            endpoint(url, key=FAKE_KEY + "\n"), provider="fake", model="fake-1"
        ),
    )
    outcome = arun(subject.run(task()))
    blob = (outcome.reason or "") + json.dumps(
        outcome.failure.model_dump() if outcome.failure else {}
    )
    assert FAKE_KEY not in blob, "the credential appears in the failure text"


@pytest.mark.parametrize(
    "failure",
    [ssl.SSLError("EOF occurred in violation of protocol"), OSError(113, "No route to host")],
)
def test_os_level_failures_while_reading_the_reply_are_transient_for_urllib(
    server, monkeypatch, failure
):
    class Opener:
        def open(self, request, timeout=None):
            raise failure

    monkeypatch.setattr(transport_module, "build_opener", lambda *handlers: Opener())
    with pytest.raises(TransientProviderError) as excinfo:
        arun(model_for(server[0], UrlLibJsonTransport()).next_turn(model_context()))
    assert excinfo.value.code == "OPENAI_COMPATIBLE_TRANSPORT_ERROR"
    assert type(failure).__name__ in str(excinfo.value)


@pytest.mark.parametrize("transport_name", ["urllib", "httpx"])
def test_a_reply_that_is_not_valid_text_is_a_named_response_error(server, transport_name):
    url, state = server
    transport = UrlLibJsonTransport() if transport_name == "urllib" else HttpxJsonTransport()
    state["script"] = [{"status": 200, "body": b"\xff\xfe\x00 not utf-8 json \xc3\x28"}]
    with pytest.raises(AgentSdkError) as excinfo:
        arun(model_for(url, transport).next_turn(model_context()))
    assert excinfo.value.code == "OPENAI_COMPATIBLE_RESPONSE_INVALID"
    assert not isinstance(excinfo.value, TransientProviderError)
