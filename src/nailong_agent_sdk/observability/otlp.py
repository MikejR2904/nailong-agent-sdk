# Copyright (c) 2026 David Michael Indraputra

"""OTLP/HTTP JSON export of recorded runs: spans, log records and metric points.

Export reads the telemetry ledger after the fact. Nothing here runs on the write path, so a
collector that is slow or down cannot delay an agent, and an export can be repeated.
"""

from __future__ import annotations

import gzip
import http.client
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError, redact_secrets
from ..foundations.text import scrub_surrogates
from ..foundations.version import PACKAGE_NAME, package_version
from .telemetry_models import (
    MetricAvailability,
    MetricDefinition,
    MetricObservation,
    TelemetryEvent,
    TelemetrySeverity,
)
from .telemetry_store import TelemetryStore
from .trace_context import graph_run_span_id, graph_run_trace_id, resource_attributes

OtlpSignal = Literal["traces", "logs", "metrics"]
SIGNALS: tuple[OtlpSignal, ...] = ("traces", "logs", "metrics")

PROFILE_EVENT_TYPE = "agent.profile-completed"
GRAPH_EXECUTED_EVENT_TYPE = "graph.executed"
_ATTRIBUTE_PREFIX = "nailong."
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NANOSECONDS_PER_SECOND = 1_000_000_000
_SPAN_KIND_INTERNAL = 1
_SPAN_KIND_CLIENT = 3
_STATUS_UNSET = 0
_STATUS_OK = 1
_STATUS_ERROR = 2
_TEMPORALITY_DELTA = 1
_LOG_PAYLOAD_CHARS = 8_192
_LOG_PAYLOAD_OMITTED_KEYS = frozenset({"spans", "resource"})
_SEVERITY_NUMBERS = {
    TelemetrySeverity.DEBUG: 5,
    TelemetrySeverity.INFO: 9,
    TelemetrySeverity.WARNING: 13,
    TelemetrySeverity.ERROR: 17,
}
_METRIC_UNITS = {
    "milliseconds": "ms",
    "seconds": "s",
    "bytes": "By",
    "ratio": "1",
    "utility": "1",
    "count": "{count}",
    "tokens": "{token}",
}
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_MAX_BACKOFF_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_ERROR_BODY_CHARS = 300
_CONTEXT_ATTRIBUTES = (
    "run_id",
    "task_id",
    "agent_id",
    "node_id",
    "controller_id",
    "project_id",
    "experiment_id",
    "cohort_id",
    "attempt_id",
    "stage",
    "environment_id",
)


class OtlpExportReport(StrictModel):
    """What one export sent: counts per signal and what the collector reported as rejected."""

    runs: int = Field(ge=0)
    requests: int = Field(ge=0)
    spans: int = Field(ge=0)
    spans_omitted: int = Field(ge=0)
    span_links: int = Field(default=0, ge=0)
    parents_not_exported: int = Field(default=0, ge=0)
    log_records: int = Field(ge=0)
    metric_points: int = Field(ge=0)
    unavailable_metrics_skipped: int = Field(ge=0)
    rejected: dict[str, int] = Field(default_factory=dict)
    rejection_messages: list[str] = Field(default_factory=list)


class OtlpSendResult(StrictModel):
    status: int
    attempts: int = Field(ge=1)
    rejected: int = Field(default=0, ge=0)
    error_message: str | None = None


@dataclass(frozen=True)
class OtlpRunPayloads:
    """The OTLP/JSON request bodies of one telemetry run and how much they carry."""

    run_id: str
    traces: dict[str, Any] | None = None
    logs: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    spans: int = 0
    spans_omitted: int = 0
    span_links: int = 0
    parents_not_exported: int = 0
    log_records: int = 0
    metric_points: int = 0
    unavailable_metrics_skipped: int = 0

    def by_signal(self) -> dict[OtlpSignal, dict[str, Any]]:
        found: dict[OtlpSignal, dict[str, Any] | None] = {
            "traces": self.traces,
            "logs": self.logs,
            "metrics": self.metrics,
        }
        return {signal: body for signal, body in found.items() if body is not None}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class OtlpHttpExporter:
    """POST OTLP/HTTP JSON bodies to a collector with the retry rules of the OTLP specification.

    429, 502, 503 and 504 are retried with exponential backoff (honouring ``Retry-After``),
    every other failure is final, a partial success is reported and never retried, and a
    redirect is refused so request headers never leave the configured host.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
        compress: bool = False,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"OTLP endpoint must be an absolute http(s) URL, got {endpoint!r}.")
        if parsed.username or parsed.password:
            raise ValueError(
                "OTLP endpoint must not embed credentials; pass them as headers instead."
            )
        if timeout_seconds <= 0:
            raise ValueError(f"OTLP timeout_seconds must be positive, got {timeout_seconds!r}.")
        if max_attempts < 1:
            raise ValueError(f"OTLP max_attempts must be at least 1, got {max_attempts!r}.")
        self._base = endpoint.rstrip("/")
        self._default_headers = dict(headers or {})
        self._timeout = timeout_seconds
        self._compress = compress
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def url_for(self, signal: OtlpSignal) -> str:
        return f"{self._base}/v1/{signal}"

    def send(self, signal: OtlpSignal, payload: Mapping[str, Any]) -> OtlpSendResult:
        url = self.url_for(signal)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._compress:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        headers.update(self._default_headers)
        failure = ""
        for attempt in range(1, self._max_attempts + 1):
            retry_after: float | None = None
            try:
                request = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with self._opener.open(request, timeout=self._timeout) as response:
                    content = response.read(_MAX_RESPONSE_BYTES)
                    result = _parsed_response(response.status, content, attempt)
                if result.error_message:
                    result = result.model_copy(
                        update={"error_message": self._scrub(result.error_message)}
                    )
                return result
            except urllib.error.HTTPError as error:
                failure = self._http_failure(error)
                if error.code not in _RETRYABLE_STATUS_CODES:
                    raise self._error(url, signal, failure, attempt, error.code) from error
                retry_after = _retry_after_seconds(error.headers.get("Retry-After"))
            except (OSError, http.client.HTTPException) as error:
                reason = getattr(error, "reason", error)
                failure = f"{type(reason).__name__}: {self._scrub(str(reason))}"
            if attempt < self._max_attempts:
                delay = min(_MAX_BACKOFF_SECONDS, 0.5 * 2 ** (attempt - 1))
                self._sleep(max(delay, min(retry_after or 0.0, _MAX_BACKOFF_SECONDS)))
        raise self._error(url, signal, failure, self._max_attempts, None)

    def _http_failure(self, error: urllib.error.HTTPError) -> str:
        if 300 <= error.code < 400:
            return (
                f"HTTP {error.code} redirect to {error.headers.get('Location')!r}, which is "
                "refused so credentials stay on the configured host"
            )
        try:
            body = error.read(_ERROR_BODY_CHARS * 4).decode("utf-8", "replace")
        except Exception:
            body = ""
        detail = self._scrub(body)
        return f"HTTP {error.code}" + (f": {detail}" if detail else "")

    def _scrub(self, text: str) -> str:
        secrets: set[str] = set()
        for value in self._default_headers.values():
            secrets.add(value)
            secrets.add(value.partition(" ")[2].strip())
        scrubbed = text
        for secret in sorted(secrets, key=len, reverse=True):
            if len(secret) >= 4:
                scrubbed = scrubbed.replace(secret, "[REDACTED]")
        return " ".join(str(redact_secrets(scrubbed)).split())[:_ERROR_BODY_CHARS]

    @staticmethod
    def _error(
        url: str, signal: str, failure: str, attempts: int, status: int | None
    ) -> AgentSdkError:
        return AgentSdkError(
            "OTLP_EXPORT_FAILED",
            f"OTLP {signal} export to {url} failed after {attempts} attempt(s): {failure}.",
            {"url": url, "signal": signal, "status": status, "attempts": attempts},
        )


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _parsed_response(status: int, body: bytes, attempts: int) -> OtlpSendResult:
    plain = OtlpSendResult(status=status, attempts=attempts)
    try:
        decoded = json.loads(body) if body.strip() else None
    except ValueError:
        return plain
    partial = decoded.get("partialSuccess") if isinstance(decoded, dict) else None
    if not isinstance(partial, dict):
        return plain
    rejected = 0
    for name in ("rejectedSpans", "rejectedLogRecords", "rejectedDataPoints"):
        try:
            rejected += int(partial.get(name) or 0)
        except (TypeError, ValueError):
            continue
    message = partial.get("errorMessage")
    return OtlpSendResult(
        status=status,
        attempts=attempts,
        rejected=rejected,
        error_message=message if isinstance(message, str) and message else None,
    )


def _unix_nano(timestamp: str) -> int:
    moment = datetime.fromisoformat(timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delta = moment - _EPOCH
    return (
        delta.days * 86_400 * _NANOSECONDS_PER_SECOND
        + delta.seconds * _NANOSECONDS_PER_SECOND
        + delta.microseconds * 1_000
    )


def _any_value(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value} if math.isfinite(value) else {"stringValue": repr(value)}
    if isinstance(value, str):
        return {"stringValue": scrub_surrogates(value)}
    if isinstance(value, Mapping):
        return {"kvlistValue": {"values": _key_values(value)}}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [item for v in value if (item := _any_value(v))]}}
    return {"stringValue": str(value)}


def _key_values(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    converted = (
        (scrub_surrogates(str(key)), _any_value(value)) for key, value in attributes.items()
    )
    return [{"key": key, "value": value} for key, value in converted if value is not None]


def _scope() -> dict[str, str]:
    return {"name": PACKAGE_NAME, "version": package_version()}


def _context_attributes(event: TelemetryEvent) -> dict[str, str]:
    context = event.context.model_dump(mode="json")
    return {
        f"{_ATTRIBUTE_PREFIX}{name}": context[name]
        for name in _CONTEXT_ATTRIBUTES
        if context.get(name) is not None
    }


def _span_name(span: Mapping[str, Any]) -> str:
    attributes = span.get("attributes") or {}
    kind = span.get("kind")
    name = str(span.get("name", ""))
    if kind == "run":
        agent = attributes.get("gen_ai.agent.name")
        return f"invoke_agent {agent}" if agent else "invoke_agent"
    if kind == "model-turn":
        model = attributes.get("gen_ai.request.model")
        return f"chat {model}" if model else "chat"
    if kind == "tool":
        return f"execute_tool {attributes.get('gen_ai.tool.name') or name}"
    if kind == "verification":
        return f"verification {name}"
    return name


def _status(outcome: str | None) -> dict[str, Any]:
    if outcome == "completed":
        return {"code": _STATUS_OK}
    if outcome in {"failed", "timed-out"}:
        return {"code": _STATUS_ERROR, "message": outcome}
    return {"code": _STATUS_UNSET}


def _profile_span(
    span: Mapping[str, Any],
    trace_id: str,
    root_attributes: Mapping[str, Any],
    links: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    start = _unix_nano(span["started_at_utc"])
    outcome = str(span.get("status", ""))
    attributes: dict[str, Any] = dict(span.get("attributes") or {})
    attributes[f"{_ATTRIBUTE_PREFIX}span.kind"] = span.get("kind")
    attributes[f"{_ATTRIBUTE_PREFIX}span.status"] = outcome
    if outcome in {"failed", "timed-out"}:
        attributes["error.type"] = outcome
    if span.get("kind") == "run":
        attributes.update(root_attributes)
    otlp: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span["span_id"],
        "name": _span_name(span),
        "kind": _SPAN_KIND_CLIENT if span.get("kind") == "model-turn" else _SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + int(span.get("duration_ns", 0))),
        "attributes": _key_values(attributes),
        "status": _status(outcome),
    }
    if span.get("parent_span_id"):
        otlp["parentSpanId"] = span["parent_span_id"]
    if links and span.get("kind") == "run":
        otlp["links"] = list(links)
    return otlp


def _graph_run_span(run_id: str, events: Sequence[TelemetryEvent]) -> dict[str, Any] | None:
    trace_id = graph_run_trace_id(run_id)
    span_id = graph_run_span_id(run_id)
    owned = [
        event
        for event in events
        if event.context.trace_id == trace_id and event.context.span_id == span_id
    ]
    if not owned:
        return None
    executions = [event for event in owned if event.event_type == GRAPH_EXECUTED_EVENT_TYPE]
    start = min(_unix_nano(event.occurred_at_utc) for event in owned)
    if executions:
        end = max(_unix_nano(event.occurred_at_utc) for event in executions)
        basis = "dispatch-to-last-execution-pass"
    else:
        end = max(_unix_nano(event.occurred_at_utc) for event in owned)
        basis = "controller-event-window"
    outcome = executions[-1].status if executions else None
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_workflow",
        f"{_ATTRIBUTE_PREFIX}graph.run_id": run_id,
        f"{_ATTRIBUTE_PREFIX}span.kind": "graph-run",
        f"{_ATTRIBUTE_PREFIX}controller_id": owned[0].context.controller_id,
        f"{_ATTRIBUTE_PREFIX}span.status": outcome,
        f"{_ATTRIBUTE_PREFIX}window.basis": basis,
        f"{_ATTRIBUTE_PREFIX}graph.execution_passes": len(executions),
        f"{_ATTRIBUTE_PREFIX}graph.controller_events": len(owned),
    }
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "invoke_workflow",
        "kind": _SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(max(start, end)),
        "attributes": _key_values(attributes),
        "status": _status(outcome),
    }


def _log_record(event: TelemetryEvent) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in event.payload.items()
        if event.event_type != PROFILE_EVENT_TYPE or key not in _LOG_PAYLOAD_OMITTED_KEYS
    }
    attributes: dict[str, Any] = {
        f"{_ATTRIBUTE_PREFIX}event.status": event.status,
        f"{_ATTRIBUTE_PREFIX}event.sequence": event.sequence,
        f"{_ATTRIBUTE_PREFIX}event.authority": event.authority.value,
        f"{_ATTRIBUTE_PREFIX}actor.kind": event.actor.kind,
        f"{_ATTRIBUTE_PREFIX}actor.identifier": event.actor.identifier,
        f"{_ATTRIBUTE_PREFIX}actor.role": event.actor.role,
        **_context_attributes(event),
    }
    if payload:
        text = json.dumps(payload, sort_keys=True, default=str)
        if len(text) > _LOG_PAYLOAD_CHARS:
            attributes[f"{_ATTRIBUTE_PREFIX}payload.truncated"] = True
            text = text[:_LOG_PAYLOAD_CHARS]
        attributes[f"{_ATTRIBUTE_PREFIX}payload"] = text
    moment = str(_unix_nano(event.occurred_at_utc))
    record: dict[str, Any] = {
        "timeUnixNano": moment,
        "observedTimeUnixNano": moment,
        "severityNumber": _SEVERITY_NUMBERS[event.severity],
        "severityText": event.severity.value.upper(),
        "body": {"stringValue": event.event_type},
        "attributes": _key_values(attributes),
        "eventName": event.event_type,
    }
    if event.context.trace_id:
        record["traceId"] = event.context.trace_id
    if event.context.span_id:
        record["spanId"] = event.context.span_id
    return record


def _point_attributes(observation: MetricObservation) -> dict[str, Any]:
    attributes: dict[str, Any] = {f"{_ATTRIBUTE_PREFIX}run_id": observation.run_id}
    for key, value in (observation.context or {}).items():
        if key == "details" and isinstance(value, Mapping):
            attributes.update({f"{_ATTRIBUTE_PREFIX}{name}": item for name, item in value.items()})
        else:
            attributes[f"{_ATTRIBUTE_PREFIX}{key}"] = value
    return attributes


def _metrics(
    observations: Sequence[MetricObservation],
    definitions: Mapping[str, MetricDefinition],
    run_start: int,
) -> tuple[list[dict[str, Any]], int, int]:
    grouped: dict[str, list[MetricObservation]] = {}
    skipped = 0
    for observation in observations:
        if (
            observation.availability is not MetricAvailability.AVAILABLE
            or observation.value is None
        ):
            skipped += 1
            continue
        grouped.setdefault(observation.metric_id, []).append(observation)
    metrics: list[dict[str, Any]] = []
    points = 0
    for metric_id, items in sorted(grouped.items()):
        definition = definitions.get(metric_id)
        instrument = (definition.instrument if definition else None) or "gauge"
        previous = run_start
        data_points: list[dict[str, Any]] = []
        for item in sorted(items, key=lambda entry: entry.observed_at_utc):
            at = _unix_nano(item.observed_at_utc)
            value = float(item.value or 0.0)
            point: dict[str, Any] = {
                "attributes": _key_values(_point_attributes(item)),
                "timeUnixNano": str(at),
            }
            if instrument == "histogram":
                point.update(
                    startTimeUnixNano=str(min(previous, at)),
                    count="1",
                    sum=value,
                    min=value,
                    max=value,
                    bucketCounts=[],
                    explicitBounds=[],
                )
            else:
                point["asDouble"] = value
                if instrument in {"counter", "up_down_counter"}:
                    point["startTimeUnixNano"] = str(min(previous, at))
            previous = at
            data_points.append(point)
        points += len(data_points)
        entry: dict[str, Any] = {
            "name": metric_id,
            "unit": _METRIC_UNITS.get(items[0].unit, items[0].unit),
        }
        if definition is not None:
            entry["description"] = definition.name
        if instrument == "histogram":
            entry["histogram"] = {
                "dataPoints": data_points,
                "aggregationTemporality": _TEMPORALITY_DELTA,
            }
        elif instrument in {"counter", "up_down_counter"}:
            entry["sum"] = {
                "dataPoints": data_points,
                "aggregationTemporality": _TEMPORALITY_DELTA,
                "isMonotonic": instrument == "counter",
            }
        else:
            entry["gauge"] = {"dataPoints": data_points}
        metrics.append(entry)
    return metrics, points, skipped


GraphDependencies = Mapping[str, Mapping[str, Sequence[str]]]
NodeLinks = Mapping[tuple[str, str], Sequence[dict[str, Any]]]


def node_root_spans(store: TelemetryStore, run_ids: Sequence[str]) -> dict[tuple[str, str], str]:
    """Map ``(trace id, node id)`` to the span id of that node's latest recorded agent run."""

    latest: dict[tuple[str, str], tuple[str, str]] = {}
    for run_id in run_ids:
        for event in store.iter_events(run_id):
            node_id = event.context.node_id
            trace_id = event.payload.get("trace_id") or event.context.trace_id
            if event.event_type != PROFILE_EVENT_TYPE or not node_id or not trace_id:
                continue
            for span in event.payload.get("spans") or []:
                if span.get("kind") != "run":
                    continue
                key = (trace_id, node_id)
                started = str(span.get("started_at_utc", ""))
                if key not in latest or started >= latest[key][0]:
                    latest[key] = (started, span["span_id"])
    return {key: value[1] for key, value in latest.items()}


def dependency_links(
    roots: Mapping[tuple[str, str], str], graph_dependencies: GraphDependencies
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Span links from each node's root span to the root spans of the nodes it depends on."""

    links: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for graph_run_id, dependencies in graph_dependencies.items():
        trace_id = graph_run_trace_id(graph_run_id)
        for node_id, required in dependencies.items():
            targets = [
                {
                    "traceId": trace_id,
                    "spanId": roots[(trace_id, dependency)],
                    "attributes": _key_values(
                        {
                            f"{_ATTRIBUTE_PREFIX}link.kind": "dependency",
                            f"{_ATTRIBUTE_PREFIX}link.basis": "declared-graph-edge",
                            f"{_ATTRIBUTE_PREFIX}node_id": dependency,
                        }
                    ),
                }
                for dependency in required
                if (trace_id, dependency) in roots
            ]
            if targets and (trace_id, node_id) in roots:
                links[(trace_id, node_id)] = targets
    return links


def exported_span_ids(store: TelemetryStore, run_ids: Sequence[str]) -> set[str]:
    """The span ids an export of these runs carries: their profile spans and workflow spans."""

    found: set[str] = set()
    for run_id in run_ids:
        events = list(store.iter_events(run_id))
        for event in events:
            if event.event_type != PROFILE_EVENT_TYPE:
                continue
            if not (event.payload.get("trace_id") or event.context.trace_id):
                continue
            found.update(span["span_id"] for span in event.payload.get("spans") or [])
        graph_span = _graph_run_span(run_id, events)
        if graph_span is not None:
            found.add(graph_span["spanId"])
    return found


def _mark_unexported_parents(spans: list[dict[str, Any]], available: Collection[str]) -> int:
    marked = 0
    for span in spans:
        parent = span.get("parentSpanId")
        if parent is None or parent in available:
            continue
        span["attributes"] = [
            *span["attributes"],
            {"key": f"{_ATTRIBUTE_PREFIX}parent_span_exported", "value": {"boolValue": False}},
        ]
        marked += 1
    return marked


def build_run_payloads(
    store: TelemetryStore,
    run_id: str,
    *,
    signals: Sequence[OtlpSignal] = SIGNALS,
    links: NodeLinks | None = None,
    exported_spans: Collection[str] | None = None,
) -> OtlpRunPayloads:
    """Return the OTLP/JSON request bodies for one telemetry run, built from its ledger.

    ``exported_spans`` is the set of span ids the whole export carries. A span whose parent is
    not among them keeps its ``parentSpanId`` (the parent may be exported elsewhere) and is
    marked ``nailong.parent_span_exported=false`` so a reader is not led to expect it.
    """

    events = list(store.iter_events(run_id))
    profiles = [event for event in events if event.event_type == PROFILE_EVENT_TYPE]
    recorded = next((p.payload["resource"] for p in profiles if p.payload.get("resource")), None)
    resource = {"attributes": _key_values(recorded or resource_attributes())}
    scope = _scope()
    found: dict[str, Any] = {}

    if "traces" in signals:
        spans: list[dict[str, Any]] = []
        omitted = 0
        linked = 0
        for event in profiles:
            trace_id = event.payload.get("trace_id") or event.context.trace_id
            if not trace_id:
                continue
            kept = event.payload.get("spans") or []
            omitted += max(0, int(event.payload.get("span_count") or 0) - len(kept))
            root_attributes = {
                **_context_attributes(event),
                f"{_ATTRIBUTE_PREFIX}profile.integrity_hash": event.payload.get("integrity_hash"),
            }
            node_links = (links or {}).get((trace_id, event.context.node_id or ""), ())
            linked += len(node_links) * sum(1 for span in kept if span.get("kind") == "run")
            spans.extend(
                _profile_span(span, trace_id, root_attributes, node_links) for span in kept
            )
        graph_span = _graph_run_span(run_id, events)
        if graph_span is not None:
            spans.append(graph_span)
        if spans:
            available = (
                exported_spans if exported_spans is not None else {span["spanId"] for span in spans}
            )
            found.update(
                spans=len(spans),
                spans_omitted=omitted,
                span_links=linked,
                parents_not_exported=_mark_unexported_parents(spans, available),
                traces={
                    "resourceSpans": [
                        {"resource": resource, "scopeSpans": [{"scope": scope, "spans": spans}]}
                    ]
                },
            )

    if "logs" in signals and events:
        records = [_log_record(event) for event in events]
        found.update(
            log_records=len(records),
            logs={
                "resourceLogs": [
                    {"resource": resource, "scopeLogs": [{"scope": scope, "logRecords": records}]}
                ]
            },
        )

    if "metrics" in signals:
        definitions = {item.metric_id: item for item in store.list_metric_definitions()}
        run_start = min((_unix_nano(event.occurred_at_utc) for event in events), default=0)
        metrics, points, skipped = _metrics(store.list_metrics(run_id), definitions, run_start)
        found["unavailable_metrics_skipped"] = skipped
        if metrics:
            found.update(
                metric_points=points,
                metrics={
                    "resourceMetrics": [
                        {
                            "resource": resource,
                            "scopeMetrics": [{"scope": scope, "metrics": metrics}],
                        }
                    ]
                },
            )
    return OtlpRunPayloads(run_id=run_id, **found)


def _links_for(
    store: TelemetryStore, run_ids: Sequence[str], graph_dependencies: GraphDependencies | None
) -> NodeLinks | None:
    if not graph_dependencies:
        return None
    return dependency_links(node_root_spans(store, run_ids), graph_dependencies)


def preview_export(
    store: TelemetryStore,
    run_ids: Sequence[str],
    *,
    signals: Sequence[OtlpSignal] = SIGNALS,
    graph_dependencies: GraphDependencies | None = None,
) -> OtlpExportReport:
    """Report what ``export_runs`` would send for these runs, without sending anything."""

    totals = {
        "spans": 0,
        "spans_omitted": 0,
        "span_links": 0,
        "parents_not_exported": 0,
        "log_records": 0,
        "metric_points": 0,
        "unavailable_metrics_skipped": 0,
    }
    links = _links_for(store, run_ids, graph_dependencies)
    exported = exported_span_ids(store, run_ids) if "traces" in signals else None
    for run_id in run_ids:
        built = build_run_payloads(
            store, run_id, signals=signals, links=links, exported_spans=exported
        )
        for name in totals:
            totals[name] += getattr(built, name)
    return OtlpExportReport(runs=len(run_ids), requests=0, **totals)


def export_runs(
    store: TelemetryStore,
    run_ids: Sequence[str],
    exporter: OtlpHttpExporter,
    *,
    signals: Sequence[OtlpSignal] = SIGNALS,
    graph_dependencies: GraphDependencies | None = None,
) -> OtlpExportReport:
    """Send each run's spans, log records and metric points; one request per run and signal."""

    totals = {
        "spans": 0,
        "spans_omitted": 0,
        "span_links": 0,
        "parents_not_exported": 0,
        "log_records": 0,
        "metric_points": 0,
        "unavailable_metrics_skipped": 0,
    }
    sent = 0
    rejected: dict[str, int] = {}
    messages: list[str] = []
    links = _links_for(store, run_ids, graph_dependencies)
    exported = exported_span_ids(store, run_ids) if "traces" in signals else None
    for run_id in run_ids:
        built = build_run_payloads(
            store, run_id, signals=signals, links=links, exported_spans=exported
        )
        for name in totals:
            totals[name] += getattr(built, name)
        for signal, payload in built.by_signal().items():
            try:
                result = exporter.send(signal, payload)
            except AgentSdkError as error:
                raise AgentSdkError(
                    error.code,
                    f'Export of run "{run_id}" stopped after {sent} request(s): {error.message}',
                    {**(error.details or {}), "run_id": run_id, "requests_sent": sent},
                ) from error
            sent += 1
            if result.rejected:
                rejected[signal] = rejected.get(signal, 0) + result.rejected
            if result.error_message:
                messages.append(f"{run_id} {signal}: {result.error_message}")
    return OtlpExportReport(
        runs=len(run_ids),
        requests=sent,
        rejected=rejected,
        rejection_messages=messages,
        **totals,
    )
