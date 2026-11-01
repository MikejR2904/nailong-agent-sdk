# Copyright (c) 2026 David Michael Indraputra

"""The recorder's wire protocol: version, size limits, shared names and the per-run token."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from ...foundations.errors import AgentSdkError

RECORDER_SCHEMA = "agent-sdk-recorder-v2"


MAX_LINE_BYTES = 16_777_216


PAGE_BYTES = 4_194_304


MAX_PAGE_ENTRIES = 1_000


APPEND_TOKEN_VARIABLE = "NAILONG_RECORDER_APPEND_TOKEN"


READ_TOKEN_VARIABLE = "NAILONG_RECORDER_READ_TOKEN"


KEY_FILE = "recorder.key"


SCOPED_TOKEN_CONTEXT = b"nailong-recorder-append-v1:"


def scoped_append_token(secret: str, run_id: str) -> str:
    message = SCOPED_TOKEN_CONTEXT + run_id.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def recorder_error(code: str, message: str, **details: Any) -> AgentSdkError:
    return AgentSdkError(code, message, details)
