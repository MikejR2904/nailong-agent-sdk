# Copyright (c) 2026 David Michael Indraputra

"""W3C Trace Context identifiers, the traceparent wire format and the process resource."""

from __future__ import annotations

import os
import re
import secrets
import uuid
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from functools import cache

from ..foundations.version import PACKAGE_NAME, package_version

TRACEPARENT_HEADER = "traceparent"
SERVICE_NAME_VARIABLE = "OTEL_SERVICE_NAME"
_HEX_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_HEX_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_HEX_BYTE = re.compile(r"[0-9a-f]{2}")
_ZERO_TRACE_ID = "0" * 32
_ZERO_SPAN_ID = "0" * 16
_SAMPLED_FLAG = 0x01
_SHOWN_CHARS = 64


@dataclass(frozen=True)
class TraceParent:
    trace_id: str
    parent_span_id: str
    sampled: bool = True


def new_trace_id() -> str:
    while True:
        candidate = secrets.token_hex(16)
        if candidate != _ZERO_TRACE_ID:
            return candidate


def new_span_id() -> str:
    while True:
        candidate = secrets.token_hex(8)
        if candidate != _ZERO_SPAN_ID:
            return candidate


def is_trace_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _HEX_TRACE_ID.fullmatch(value) is not None
        and value != _ZERO_TRACE_ID
    )


def is_span_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _HEX_SPAN_ID.fullmatch(value) is not None
        and value != _ZERO_SPAN_ID
    )


def require_trace_id(value: str, field: str) -> str:
    if not is_trace_id(value):
        raise ValueError(
            f"{field} must be 32 lowercase hex characters and not all zeros (a W3C trace id), "
            f"got {_shown(value)}."
        )
    return value


def require_span_id(value: str, field: str) -> str:
    if not is_span_id(value):
        raise ValueError(
            f"{field} must be 16 lowercase hex characters and not all zeros (a W3C span id), "
            f"got {_shown(value)}."
        )
    return value


def format_traceparent(trace_id: str, span_id: str, *, sampled: bool = True) -> str:
    require_trace_id(trace_id, "trace_id")
    require_span_id(span_id, "span_id")
    return f"00-{trace_id}-{span_id}-{_SAMPLED_FLAG if sampled else 0:02x}"


def parse_traceparent(value: str) -> TraceParent:
    if not isinstance(value, str):
        raise ValueError(f"traceparent must be a string, got {type(value).__name__}.")
    parts = value.strip().split("-")
    if len(parts) < 4:
        raise ValueError(
            "traceparent must have the four fields version-trace_id-parent_id-flags separated "
            f'by "-", got {len(parts)} field(s) in {_shown(value)}.'
        )
    version, trace_id, parent_id, flags = parts[:4]
    if _HEX_BYTE.fullmatch(version) is None:
        raise ValueError(
            f"traceparent version must be 2 lowercase hex characters, got {_shown(version)}."
        )
    if version == "ff":
        raise ValueError('traceparent version "ff" is not allowed.')
    if version == "00" and len(parts) != 4:
        raise ValueError(f"traceparent version 00 takes exactly four fields, got {len(parts)}.")
    if not is_trace_id(trace_id):
        raise ValueError(
            "traceparent trace_id must be 32 lowercase hex characters and not all zeros, "
            f"got {_shown(trace_id)}."
        )
    if not is_span_id(parent_id):
        raise ValueError(
            "traceparent parent_id must be 16 lowercase hex characters and not all zeros, "
            f"got {_shown(parent_id)}."
        )
    if _HEX_BYTE.fullmatch(flags) is None:
        raise ValueError(
            f"traceparent flags must be 2 lowercase hex characters, got {_shown(flags)}."
        )
    return TraceParent(trace_id, parent_id, bool(int(flags, 16) & _SAMPLED_FLAG))


def inject_traceparent(
    carrier: MutableMapping[str, str], trace_id: str, span_id: str, *, sampled: bool = True
) -> None:
    carrier[TRACEPARENT_HEADER] = format_traceparent(trace_id, span_id, sampled=sampled)


def extract_traceparent(carrier: Mapping[str, str]) -> TraceParent | None:
    for key, value in carrier.items():
        if key.lower() == TRACEPARENT_HEADER:
            try:
                return parse_traceparent(value)
            except ValueError:
                return None
    return None


@cache
def _process_resource() -> tuple[tuple[str, str], ...]:
    return (
        ("service.name", os.environ.get(SERVICE_NAME_VARIABLE) or PACKAGE_NAME),
        ("service.instance.id", uuid.uuid4().hex),
        ("telemetry.sdk.name", PACKAGE_NAME),
        ("telemetry.sdk.language", "python"),
        ("telemetry.sdk.version", package_version()),
    )


def resource_attributes() -> dict[str, str]:
    return dict(_process_resource())


def _shown(value: object) -> str:
    text = str(value)
    return repr(text if len(text) <= _SHOWN_CHARS else text[:_SHOWN_CHARS] + "...")
