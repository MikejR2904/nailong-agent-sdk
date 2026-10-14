import asyncio
import socket
import threading
import time

import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.base_agent.types import AgentWatchdogPolicy
from nailong_agent_sdk.agent.model import (
    FailoverAgentModel,
    ModelCancellationOutcome,
)
from nailong_agent_sdk.agent.openai_compatible import (
    AsyncJsonHttpTransport,
    HttpxAsyncJsonTransport,
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
    ProviderCancelRequest,
    UrlLibJsonTransport,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import arun, definition, task
from tests.support.holding_provider import HoldingProvider


@pytest.fixture
def provider():
    opened = HoldingProvider()
    yield opened
    opened.stop()


@pytest.fixture
def streaming_provider():
    opened = HoldingProvider(stream=True)
    yield opened
    opened.stop()


class Token:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()


def endpoint(provider, timeout=30):
    return OpenAICompatibleEndpoint(
        base_url=provider.base_url,
        api_key="test-key-not-a-secret",
        timeout_seconds=timeout,
        allow_insecure_http=True,
    )


def cancel_when_received(provider, token):
    def wait():
        provider.received.wait(30)
        token.cancel()

    threading.Thread(target=wait, daemon=True).start()


def run_cancelled(agent, provider, tmp_path=None):
    token = Token()
    cancel_when_received(provider, token)
    started = time.monotonic()
    result = arun(agent.run(task(), token), timeout=60)
    assert time.monotonic() - started < 20, "the run did not stop promptly"
    return result


def cancelled_event(result):
    matching = [event for event in result.events if event.type == "model-turn-cancelled"]
    assert len(matching) == 1, [event.type for event in result.events]
    return matching[0].details


def model_for(provider, transport=None, **options):
    return OpenAICompatibleAgentModel(
        endpoint(provider), provider="fake", model="fake-1", transport=transport, **options
    )


def test_a_blocking_request_is_detached_and_its_late_response_changes_nothing(provider, tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        agent = BaseAgent(
            definition(), model_for(provider, UrlLibJsonTransport()), telemetry=telemetry
        )
        result = run_cancelled(agent, provider)
        assert result.status is AgentRunStatus.CANCELLED
        assert result.reason == "Execution was cancelled while the model turn was running."
        details = cancelled_event(result)
        assert details["logical"] == "cancelled" and details["transport"] == "detached"
        assert details["provider"] == "unsupported" and details["late_response"] == "discarded"
        assert "detached thread" in details["detail"] and "30s" in details["detail"]
        assert not provider.client_closed.wait(0.5)
        before = [event.event_type for event in telemetry.list_events("t1", limit=500)]
        provider.release.set()
        assert provider.responded.wait(10)
        time.sleep(0.3)
        after = [event.event_type for event in telemetry.list_events("t1", limit=500)]
        assert after == before and "agent.terminated" in after
        assert telemetry.chain_break("t1") is None
    finally:
        telemetry.close()


def test_the_async_transport_closes_the_connection_when_the_run_is_cancelled(provider):
    agent = BaseAgent(definition(), model_for(provider, HttpxAsyncJsonTransport()))
    result = run_cancelled(agent, provider)
    assert result.status is AgentRunStatus.CANCELLED
    details = cancelled_event(result)
    assert details["transport"] == "connection-closed" and details["provider"] == "unsupported"
    assert "connection to the provider was closed" in details["detail"]
    assert provider.client_closed.wait(5), "the provider never saw the connection close"
    assert not provider.responded.is_set()


def test_a_cancelled_stream_names_the_request_the_provider_had_issued(streaming_provider):
    deltas = []

    async def on_delta(event):
        deltas.append(event)

    agent = BaseAgent(
        definition(),
        model_for(streaming_provider),
        on_model_stream=on_delta,
    )
    result = run_cancelled(agent, streaming_provider)
    assert result.status is AgentRunStatus.CANCELLED
    details = cancelled_event(result)
    assert details["transport"] == "connection-closed"
    assert details["request_id"] == "chatcmpl-stream-7"
    assert details["provider"] == "unsupported"
    assert streaming_provider.client_closed.wait(5)


def test_a_host_provider_cancellation_is_the_only_way_to_report_confirmed(streaming_provider):
    asked = []

    async def cancel(request: ProviderCancelRequest) -> bool:
        asked.append(request)
        return True

    agent = BaseAgent(
        definition(),
        model_for(streaming_provider, provider_cancel=cancel),
        on_model_stream=lambda event: None,
    )
    result = run_cancelled(agent, streaming_provider)
    details = cancelled_event(result)
    assert details["provider"] == "confirmed" and details["request_id"] == "chatcmpl-stream-7"
    assert "confirmed cancelling request chatcmpl-stream-7" in details["detail"]
    assert [item.request_id for item in asked] == ["chatcmpl-stream-7"]
    assert asked[0].streamed is True and asked[0].task_id == "t1"
    assert (asked[0].provider, asked[0].model) == ("fake", "fake-1")


def test_a_provider_cancellation_that_fails_or_declines_is_requested_not_confirmed(
    streaming_provider,
):
    async def declines(request):
        return False

    agent = BaseAgent(
        definition(),
        model_for(streaming_provider, provider_cancel=declines),
        on_model_stream=lambda event: None,
    )
    details = cancelled_event(run_cancelled(agent, streaming_provider))
    assert details["provider"] == "requested"
    assert "did not confirm cancelling request chatcmpl-stream-7" in details["detail"]


def test_a_provider_cancellation_that_raises_is_reported_by_exception_name(streaming_provider):
    async def broken(request):
        raise RuntimeError("cancel endpoint refused")

    agent = BaseAgent(
        definition(),
        model_for(streaming_provider, provider_cancel=broken),
        on_model_stream=lambda event: None,
    )
    details = cancelled_event(run_cancelled(agent, streaming_provider))
    assert details["provider"] == "requested"
    assert "RuntimeError: cancel endpoint refused" in details["detail"]


def test_a_non_streaming_request_has_no_id_to_cancel_by(provider):
    called = []

    async def cancel(request):
        called.append(request)
        return True

    agent = BaseAgent(
        definition(),
        model_for(provider, HttpxAsyncJsonTransport(), provider_cancel=cancel),
    )
    details = cancelled_event(run_cancelled(agent, provider))
    assert details["provider"] == "unsupported" and details["request_id"] is None
    assert "had not named the request yet" in details["detail"]
    assert called == []


def test_a_model_that_does_not_report_leaves_the_outcome_unreported():
    class Hanging:
        async def next_turn(self, context):
            await asyncio.sleep(60)

    token = Token()
    threading.Timer(0.2, token.cancel).start()
    result = arun(BaseAgent(definition(), Hanging()).run(task(), token), timeout=30)
    details = cancelled_event(result)
    assert details["transport"] == "unreported" and details["provider"] == "unreported"
    assert details["late_response"] == "discarded"


def test_a_failover_model_reports_the_adapter_that_was_cancelled(provider):
    adapter = model_for(provider, HttpxAsyncJsonTransport())
    agent = BaseAgent(definition(), FailoverAgentModel([adapter]))
    details = cancelled_event(run_cancelled(agent, provider))
    assert details["transport"] == "connection-closed"
    assert adapter.cancellation_outcome("t1") is None


def test_an_outcome_is_handed_out_once(provider):
    adapter = model_for(provider, HttpxAsyncJsonTransport())
    agent = BaseAgent(definition(), adapter)
    run_cancelled(agent, provider)
    assert adapter.cancellation_outcome("t1") is None


def test_the_per_turn_timeout_still_ends_a_request_on_either_transport(provider):
    for transport in (UrlLibJsonTransport(), HttpxAsyncJsonTransport()):
        agent = BaseAgent(
            definition(),
            model_for(provider, transport),
            watchdog_policy=AgentWatchdogPolicy(model_turn_timeout_seconds=0.5),
        )
        result = arun(agent.run(task()), timeout=30)
        assert result.status is AgentRunStatus.FAILED
        assert result.failure is not None and result.failure.code == "WATCHDOG_MODEL_TIMEOUT"
        provider.received.clear()


def test_the_async_transport_maps_provider_failures_like_the_blocking_one():
    class Refusing:
        def __init__(self):
            self.server = socket.socket()
            self.server.bind(("127.0.0.1", 0))
            self.server.listen(1)
            self.port = self.server.getsockname()[1]
            threading.Thread(target=self.serve, daemon=True).start()

        def serve(self):
            connection, _ = self.server.accept()
            connection.recv(65536)
            body = b'{"error": {"message": "slow down"}}'
            connection.sendall(
                b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 3\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            connection.close()

    refusing = Refusing()
    transport = HttpxAsyncJsonTransport()
    assert isinstance(transport, AsyncJsonHttpTransport)
    with pytest.raises(TransientProviderError) as raised:
        asyncio.run(
            transport.apost_json(
                f"http://127.0.0.1:{refusing.port}/v1/chat/completions",
                headers={"Authorization": "Bearer abcd-efgh"},
                payload={"model": "m"},
                timeout_seconds=5,
            )
        )
    assert raised.value.code == "OPENAI_COMPATIBLE_HTTP_ERROR"
    assert raised.value.retry_after_seconds == 3
    assert "slow down" in str(raised.value)
    unreachable = socket.socket()
    unreachable.bind(("127.0.0.1", 0))
    port = unreachable.getsockname()[1]
    unreachable.close()
    with pytest.raises(AgentSdkError) as failed:
        asyncio.run(
            transport.apost_json(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                headers={},
                payload={},
                timeout_seconds=2,
            )
        )
    assert failed.value.code in {
        "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
        "OPENAI_COMPATIBLE_TRANSPORT_TIMEOUT",
    }
    assert isinstance(failed.value, TransientProviderError)


def test_the_outcome_details_never_claim_more_than_the_enumerated_levels():
    outcome = ModelCancellationOutcome(transport="detached", provider="unsupported")
    assert outcome.details() == {
        "logical": "cancelled",
        "transport": "detached",
        "provider": "unsupported",
        "request_id": None,
        "late_response": "discarded",
        "detail": "",
    }
