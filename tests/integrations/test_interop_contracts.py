import time

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.integrations._utils import assert_sanitized_interop_value, canonical_digest
from nailong_agent_sdk.integrations.contracts import (
    InteropOperationStatus,
    InteropReceipt,
    InteropRunEnvelope,
)
from nailong_agent_sdk.integrations.receipts import (
    InMemoryInteropReceiptStore,
    TelemetryInteropReceiptSink,
)
from nailong_agent_sdk.observability.telemetry_models import TelemetryContext
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.paths import STUB_ROOT


@pytest.fixture(autouse=True)
def typesafe_stub(monkeypatch):
    monkeypatch.syspath_prepend(str(STUB_ROOT))


def test_sanitizer_key_variants_limits_and_types():
    forbidden = [
        "api_key",
        "apiKey",
        "API-KEY",
        "Authorization",
        "x_secret_thing",
        "refreshToken",
        "Message",
        "messages",
        "password",
        "client-secret",
        "TOOL_RESULTS",
        "audit log",
        "secretary",
    ]
    for key in forbidden:
        with pytest.raises(ValueError, match="Unsafe key"):
            assert_sanitized_interop_value({key: 1})
    for key in ("token_count", "tokens", "summary", "description", "model", "note"):
        assert_sanitized_interop_value({key: 1})
    deep = value = {}
    for _ in range(16):
        value["k"] = {}
        value = value["k"]
    assert_sanitized_interop_value(deep)
    value["k"] = {}
    with pytest.raises(ValueError, match="nesting depth"):
        assert_sanitized_interop_value(deep)
    assert_sanitized_interop_value([0] * 256)
    with pytest.raises(ValueError, match="list exceeds"):
        assert_sanitized_interop_value([0] * 257)
    assert_sanitized_interop_value({str(i): i for i in range(128)})
    with pytest.raises(ValueError, match="object exceeds"):
        assert_sanitized_interop_value({str(i): i for i in range(129)})
    assert_sanitized_interop_value("x" * 16_384)
    with pytest.raises(ValueError, match="string exceeds"):
        assert_sanitized_interop_value("x" * 16_385)
    for bad in ((1, 2), {1, 2}, b"bytes", object(), time.gmtime()):
        with pytest.raises(TypeError):
            assert_sanitized_interop_value({"v": bad})


def test_sanitizer_does_not_inspect_values_for_credentials():
    for value in (
        "Bearer sk-live-FAKE0000000000000000000000000000",
        "password=hunter2",
        "api_key: abcdef123456",
        "see sk-" + "a" * 24,
    ):
        with pytest.raises(ValueError, match=r"string at \$\.note contains credential-shaped text"):
            assert_sanitized_interop_value({"note": value})
    with pytest.raises(ValueError, match=r"string at \$\.items\[1\] contains credential-shaped"):
        assert_sanitized_interop_value({"items": ["fine", "token=abcd1234efgh"]})
    for value in ("the token budget is 4000", "tokens: 12", "Passwords are rotated weekly"):
        assert_sanitized_interop_value({"note": value})


def test_envelope_digest_rules_and_determinism():
    envelope = InteropRunEnvelope(
        run_id="r", remaining_turn_budget=3, projection={"b": 1, "a": [1, 2]}
    )
    same = InteropRunEnvelope(run_id="r", remaining_turn_budget=3, projection={"a": [1, 2], "b": 1})
    assert (
        envelope.projection_digest
        == same.projection_digest
        == canonical_digest({"a": [1, 2], "b": 1})
    )
    with pytest.raises(ValidationError, match="does not match"):
        InteropRunEnvelope(
            run_id="r", remaining_turn_budget=3, projection={"a": 1}, projection_digest="0" * 64
        )
    with pytest.raises(ValidationError):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=3, projection={"api_key": "x"})
    with pytest.raises(ValidationError):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=-1)
    with pytest.raises(ValidationError, match="got tuple at \\$\\.v"):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=1, projection={"v": (1, 2)})


def test_receipt_sinks(tmp_path):
    receipt = InteropReceipt(
        provider="p",
        operation="op",
        status=InteropOperationStatus.FAILED,
        run_id="run-1",
        projection_digest="a" * 64,
    )
    memory = InMemoryInteropReceiptStore()
    memory(receipt)
    assert memory.receipts == [receipt]
    store = TelemetryStore(tmp_path)
    try:
        sink = TelemetryInteropReceiptSink(store, lambda r: TelemetryContext(run_id=r.run_id))
        sink(receipt)
        events = store.list_events("run-1")
        assert (
            events[0].event_type == "interop.external_operation"
            and events[0].severity.value == "warning"
        )
        assert events[0].payload["projection_digest"] == "a" * 64
    finally:
        store.close()
