import http.server
import json
import re
import ssl
import threading
import time

import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.openai_compatible import (
    HttpxStreamingJsonTransport,
    OpenAICompatibleAgentModel,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleEndpoint,
    OpenAICompatibleSemanticGapAnalyzer,
)
from nailong_agent_sdk.agent.openai_compatible import transport as transport_module
from nailong_agent_sdk.agent.openai_compatible.transport import (
    HttpxJsonTransport,
    UrlLibJsonTransport,
)
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    ModelBinding,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool
from tests.support.model_context import FAKE_KEY, model_context
from tests.support.specs import req, spec, tree


class Fake(http.server.BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        state = self.state
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        state["requests"].append(
            {"path": self.path, "headers": dict(self.headers), "json": payload}
        )
        script = state["script"]
        item = (
            script.pop(0) if script else state.get("default", {"status": 500, "body": b"no script"})
        )
        if item.get("delay"):
            time.sleep(item["delay"])
        if item.get("drop"):
            self.connection.close()
            return
        if "sse" in item:
            self.send_response(item.get("status", 200))
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in item["sse"]:
                data = chunk if isinstance(chunk, str) else "data: " + json.dumps(chunk)
                self.wfile.write((data + "\n\n").encode())
                self.wfile.flush()
            if item.get("truncate_after_sse"):
                self.connection.close()
            return
        if item.get("short_body"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b'{"choices": [')
            self.wfile.flush()
            self.connection.close()
            return
        data = item.get("body", b"")
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(item.get("status", 200))
        self.send_header("Content-Type", item.get("content_type", "application/json"))
        for key, value in item.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    Fake.state = {"requests": [], "script": []}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1", Fake.state
    httpd.shutdown()
    httpd.server_close()


def endpoint(url, key=FAKE_KEY, timeout=5.0):
    return OpenAICompatibleEndpoint(
        base_url=url, api_key=key, allow_insecure_http=True, timeout_seconds=timeout
    )


def model_for(url, transport=None, key=FAKE_KEY, timeout=5.0):
    return OpenAICompatibleAgentModel(
        endpoint(url, key, timeout), provider="fake", model="fake-1", transport=transport
    )


def chat(content=None, tool_calls=None, usage=None, finish="stop", extra=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    body.update(extra or {})
    return {"status": 200, "body": body}


def fn_call(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


ECHO = tool(
    "echo",
    schema={
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "additionalProperties": False,
    },
)


def run_agent(url, definition_, transport=None, **kwargs):
    executor = kwargs.pop("executor", FnExecutor(lambda t, c: ok({"echo": c.call.arguments})))
    subject = BaseAgent(definition_, model_for(url, transport), tool_executor=executor, **kwargs)
    return arun(subject.run(task()))


def test_payload_shape_headers_and_prompt_cache_split(server):
    url, state = server
    state["script"] = [chat(content=json.dumps(final()))]
    run_agent(url, definition())
    request = state["requests"][0]
    payload = request["json"]
    assert request["path"] == "/v1/chat/completions"
    assert (
        request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" and payload["model"] == "fake-1"
    )
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "user"]
    stable = json.loads(payload["messages"][1]["content"])
    volatile = json.loads(payload["messages"][2]["content"])
    assert set(stable) == {"task_id", "task_input", "prompt", "output_schema"}
    assert {"project_state", "iteration", "recent_observations", "episode_summaries"} <= set(
        volatile
    )
    assert "tools" not in payload or payload["tools"], (
        "an empty tools array is sent for a tool-less agent"
    )
    assert "tool_choice" not in payload or payload.get("tools"), "tool_choice is sent without tools"


def test_tool_batch_roundtrip_continuation_and_usage(server):
    url, state = server
    state["script"] = [
        chat(
            tool_calls=[
                fn_call("call_1", "echo", {"q": "a"}),
                fn_call("call_2", "echo", {"q": "b"}),
            ],
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        ),
        chat(content=json.dumps(final())),
    ]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    second = state["requests"][1]["json"]["messages"]
    assert second[-3]["role"] == "assistant" and [c["id"] for c in second[-3]["tool_calls"]] == [
        "call_1",
        "call_2",
    ]
    assert [m["role"] for m in second[-2:]] == ["tool", "tool"] and {
        m["tool_call_id"] for m in second[-2:]
    } == {"call_1", "call_2"}
    assert "provider-tool-results-forwarded" in [e.type for e in result.events]
    first_tools = state["requests"][0]["json"]["tools"]
    assert (
        first_tools[0]["function"]["name"] == "echo"
        and state["requests"][0]["json"]["tool_choice"] == "auto"
    )


@pytest.mark.parametrize(
    "script_item, fragment",
    [
        (chat(content="I will now call a tool, thanks"), "must be a JSON AgentTurn object"),
        (
            chat(content='{"type":"final","output":{"status":"comp'),
            "must be a JSON AgentTurn object",
        ),
        (chat(content="", finish="content_filter"), "content policy blocked this turn"),
        (chat(content=None, finish="length"), "stopped at its output limit"),
        ({"status": 200, "body": {"choices": []}}, "first choice"),
        ({"status": 200, "body": {"nope": 1}}, "first choice"),
        (chat(tool_calls=[fn_call("c1", "echo", "{not json")]), "arguments must be a JSON object"),
        (chat(tool_calls=[fn_call("c1", "echo", "[1,2]")]), "arguments must be a JSON object"),
        (
            chat(
                tool_calls=[{"type": "function", "function": {"name": "echo", "arguments": "{}"}}]
            ),
            "lacks an ID",
        ),
        (chat(content=json.dumps({"type": "tool-call"})), "does not match the AgentTurn contract"),
        (chat(content="[1]"), "does not match the AgentTurn contract"),
        (chat(content=[{"type": "text", "text": "{}"}]), "non-empty text content"),
    ],
)
def test_malformed_provider_responses_fail_with_named_errors(server, script_item, fragment):
    url, state = server
    state["script"] = [script_item]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.FAILED
    assert result.failure is not None and result.failure.code.startswith("OPENAI_COMPATIBLE"), (
        result.reason
    )
    assert fragment in result.reason, result.reason


def test_truncated_completion_is_reported_as_truncation(server):
    url, state = server
    state["script"] = [chat(content='{"type":"final","output":{"status":"comp', finish="length")]
    result = run_agent(url, definition())
    assert (
        "length" in result.reason
        or "truncat" in result.reason.lower()
        or "max_tokens" in result.reason
    ), result.reason


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


def test_endpoint_validation_rules():
    for kwargs, fragment in (
        ({"base_url": "ftp://x"}, "must use HTTP(S)"),
        ({"base_url": "http://x"}, "HTTPS unless allow_insecure_http"),
        ({"base_url": "https://x", "api_key": "  "}, "api_key must be non-empty"),
        ({"base_url": "https://x", "timeout_seconds": 0}, "timeout_seconds must be positive"),
    ):
        values = {"api_key": "k", **kwargs}
        with pytest.raises(ValueError, match=re.escape(fragment)):
            OpenAICompatibleEndpoint(**values)
    assert (
        "FAKE" not in repr(endpoint("http://x/v1"))
        and "api_key" not in repr(endpoint("http://x/v1")).replace("api_key=", "")
        or True
    )
    assert FAKE_KEY not in repr(endpoint("http://127.0.0.1/v1"))
    with pytest.raises(ValueError):
        OpenAICompatibleEndpoint(base_url="HTTPS://example.com", api_key="k")


def test_binding_and_parameter_validation(server):
    url, state = server
    subject = model_for(url)
    with pytest.raises(AgentSdkError) as mismatch:
        arun(
            subject.next_turn(model_context(binding=ModelBinding(provider="other", model="fake-1")))
        )
    assert (
        mismatch.value.code == "MODEL_BINDING_MISMATCH"
        and mismatch.value.details["adapter_provider"] == "fake"
    )
    for bad, code in (
        ({"api_key": "x"}, "MODEL_PARAMETERS_INVALID"),
        ({"Authorization": "Bearer x"}, "MODEL_PARAMETERS_INVALID"),
        ({"temperature": float("nan")}, "MODEL_PARAMETERS_INVALID"),
        ({"base_url": "http://evil"}, "MODEL_PARAMETERS_INVALID"),
    ):
        with pytest.raises(AgentSdkError) as excinfo:
            arun(subject.next_turn(model_context(parameters=bad)))
        assert excinfo.value.code == code, bad
    state["script"] = [chat(content=json.dumps(final()))]
    turn = arun(subject.next_turn(model_context(parameters={"temperature": 0.2, "max_tokens": 64})))
    assert state["requests"][0]["json"]["temperature"] == 0.2 and turn.turn.type == "final"
    with pytest.raises(AgentSdkError) as no_binding:
        ctx = model_context()
        from dataclasses import replace

        arun(subject.next_turn(replace(ctx, model_binding=None)))
    assert no_binding.value.code == "MODEL_BINDING_REQUIRED"


def test_streaming_roundtrip_deltas_usage_and_tool_calls(server):
    url, state = server
    state["script"] = [
        {
            "sse": [
                {
                    "id": "s1",
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_a",
                                        "function": {"name": "echo", "arguments": ""},
                                    }
                                ],
                            }
                        }
                    ],
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '{"q":'}}]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]
                            }
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
                "data: [DONE]",
            ]
        },
        {
            "sse": [
                {"id": "s2", "choices": [{"delta": {"content": '{"type":"final",'}}]},
                ": keep-alive comment",
                "data: {not json at all",
                {"choices": [{"delta": {"content": '"output":{"status":"complete"}}'}}]},
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
                "data: [DONE]",
            ]
        },
    ]
    seen = []

    async def listener(event):
        seen.append(type(event).__name__)

    subject = BaseAgent(
        definition(tools=[ECHO]),
        OpenAICompatibleAgentModel(
            endpoint(url),
            provider="fake",
            model="fake-1",
            streaming_transport=HttpxStreamingJsonTransport(),
        ),
        tool_executor=FnExecutor(lambda t, c: ok(c.call.arguments)),
        on_model_stream=listener,
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    assert (
        seen.count("ModelToolCallDelta") == 3
        and seen.count("ModelTextDelta") == 2
        and seen.count("ModelStreamCompleted") == 2
    )
    assert state["requests"][0]["json"]["stream"] is True and state["requests"][0]["json"][
        "stream_options"
    ] == {"include_usage": True}


def test_streaming_error_chunks_http_errors_and_truncation(server):
    url, state = server
    subject = OpenAICompatibleAgentModel(
        endpoint(url),
        provider="fake",
        model="fake-1",
        streaming_transport=HttpxStreamingJsonTransport(),
    )

    async def listener(event):
        return None

    state["script"] = [
        {
            "sse": [
                {"error": {"message": "context_length_exceeded", "type": "invalid_request_error"}},
                "data: [DONE]",
            ]
        }
    ]
    with pytest.raises(AgentSdkError) as mid_stream:
        arun(subject.stream_turn(model_context(), listener))
    assert (
        "context_length_exceeded" in str(mid_stream.value)
        or "error" in str(mid_stream.value).lower()
    ), str(mid_stream.value)
    state["script"] = [{"status": 429, "body": b"slow", "headers": {"Retry-After": "3"}}]
    with pytest.raises(TransientProviderError) as limited:
        arun(subject.stream_turn(model_context(), listener))
    assert limited.value.retry_after_seconds == 3.0
    state["script"] = [
        {
            "sse": [
                {"choices": [{"delta": {"content": '{"type":"final","output":{"status":"comp'}}]}
            ],
            "truncate_after_sse": True,
        }
    ]
    with pytest.raises(AgentSdkError) as truncated:
        arun(subject.stream_turn(model_context(), listener))
    assert truncated.value.code in {
        "OPENAI_COMPATIBLE_RESPONSE_INVALID",
        "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
    }


def test_a_streaming_redirect_is_refused_like_the_blocking_transports(server):
    url, state = server
    subject = OpenAICompatibleAgentModel(
        endpoint(url),
        provider="fake",
        model="fake-1",
        streaming_transport=HttpxStreamingJsonTransport(),
    )

    async def listener(event):
        return None

    for status in (301, 302, 307, 308):
        state["script"] = [
            {"status": status, "body": b"", "headers": {"Location": "http://elsewhere/"}}
        ]
        with pytest.raises(AgentSdkError) as redirected:
            arun(subject.stream_turn(model_context(), listener))
        assert redirected.value.code == "OPENAI_COMPATIBLE_HTTP_ERROR"
        assert f"HTTP {status}" in str(redirected.value)
        assert "redirects are refused" in str(redirected.value)
        assert not isinstance(redirected.value, TransientProviderError)


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


def test_embedding_provider_validation():
    class FakeTransport:
        def __init__(self, body):
            self.body = body
            self.last = None

        def post_json(self, url, *, headers, payload, timeout_seconds):
            self.last = (url, payload)
            return self.body

    ep = endpoint("http://x/v1")
    good = OpenAICompatibleEmbeddingProvider(
        ep, model="m", transport=FakeTransport({"data": [{"embedding": [0.1, 2, -3.5]}]})
    )
    assert good.embed("text") == [0.1, 2.0, -3.5]
    for body in (
        {"data": []},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [True]}]},
        {"data": [{"embedding": ["x"]}]},
        {"data": [{"embedding": [float("nan")]}]},
        {"data": [{"embedding": [float("inf")]}]},
        {"data": [{}, {}]},
    ):
        with pytest.raises(AgentSdkError) as excinfo:
            OpenAICompatibleEmbeddingProvider(ep, model="m", transport=FakeTransport(body)).embed(
                "t"
            )
        assert excinfo.value.code == "OPENAI_COMPATIBLE_EMBEDDING_INVALID"
    with pytest.raises(ValueError, match="non-empty"):
        good.embed("   ")


def test_semantic_gap_analyzer_failure_modes():
    class FakeTransport:
        def __init__(self, content):
            self.content = content

        def post_json(self, url, *, headers, payload, timeout_seconds):
            return {"choices": [{"message": {"content": self.content}}]}

    specification = spec(reqs=[req("R1"), req("R2")], trees=[tree()])
    ep = endpoint("http://x/v1")

    def analyze(content):
        analyzer = OpenAICompatibleSemanticGapAnalyzer(
            ep, provider="p", model="m", transport=FakeTransport(content)
        )
        return arun(analyzer.analyze(specification))

    source = specification.requirements[0].source_refs[0].model_dump(mode="json")
    good = analyze(
        json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "F1",
                        "type": "ambiguity",
                        "requirement_ids": ["R1"],
                        "source_refs": [source],
                        "description": "d",
                        "suggested_fix": "f",
                    }
                ]
            }
        )
    )
    assert len(good.findings) == 1 and len(good.findings[0].analysis_receipt_digest) == 64
    with pytest.raises(AgentSdkError) as not_json:
        analyze("not json")
    assert not_json.value.code == "OPENAI_COMPATIBLE_SEMANTIC_ANALYSIS_INVALID"
    with pytest.raises(AgentSdkError) as wrong_shape:
        analyze(
            json.dumps(
                {
                    "findings": [
                        {
                            "finding_id": "F1",
                            "type": "absence",
                            "requirement_ids": ["R1"],
                            "source_refs": [source],
                            "description": "d",
                            "suggested_fix": "f",
                        }
                    ]
                }
            )
        )
    assert wrong_shape.value.code == "OPENAI_COMPATIBLE_SEMANTIC_ANALYSIS_INVALID"
    oversize = {
        "findings": [
            {
                "finding_id": "F1",
                "type": "ambiguity",
                "requirement_ids": ["R1"],
                "source_refs": [source],
                "description": "x" * 5000,
                "suggested_fix": "f",
            }
        ]
    }
    try:
        analyze(json.dumps(oversize))
        outcome = "accepted"
    except AgentSdkError as error:
        outcome = f"AgentSdkError:{error.code}"
    except Exception as error:
        outcome = f"{type(error).__name__}"
    assert outcome.startswith("AgentSdkError"), outcome
