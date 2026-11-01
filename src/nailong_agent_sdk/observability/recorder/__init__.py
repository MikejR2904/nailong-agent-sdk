# Copyright (c) 2026 David Michael Indraputra

"""A recorder that runs in another process and keeps its own signed copy of the ledgers.

The telemetry and audit ledgers live under the run root, where anything with file access can edit
or delete them, and an anchor taken at the end of a run covers whatever state the ledgers were in
by then. A recorder receives every record as it is written, over a socket, in a process the agent
has no handle on. It keeps its own copy, checks that each record chains to the one before, and
signs Merkle tree heads with a key that never leaves it. Later the local ledgers are compared with
its copy: an edit, a deletion or a truncation of anything the recorder already received is found
and the lost records can be read back.

What this separates is the agent from the evidence, not the host from it. The recorder is as
independent as the operating-system account, container or machine it runs in.
"""

from __future__ import annotations

from .cli import (
    main as main,
)
from .client import (
    DEFAULT_CONNECTIONS as DEFAULT_CONNECTIONS,
)
from .client import (
    RecorderClient as RecorderClient,
)
from .client import (
    parse_reply as parse_reply,
)
from .compare import (
    compare_with_recorder as compare_with_recorder,
)
from .core import (
    RUN_GATES as RUN_GATES,
)
from .core import (
    RecorderCore as RecorderCore,
)
from .mirror import (
    MirrorStats as MirrorStats,
)
from .mirror import (
    RecorderMirror as RecorderMirror,
)
from .models import (
    RecordedEntry as RecordedEntry,
)
from .models import (
    RecorderComparison as RecorderComparison,
)
from .models import (
    RecorderSource as RecorderSource,
)
from .models import (
    RecorderVerdict as RecorderVerdict,
)
from .process import (
    RecorderProcess as RecorderProcess,
)
from .process import (
    spawn_recorder as spawn_recorder,
)
from .protocol import (
    APPEND_TOKEN_VARIABLE as APPEND_TOKEN_VARIABLE,
)
from .protocol import (
    KEY_FILE as KEY_FILE,
)
from .protocol import (
    MAX_LINE_BYTES as MAX_LINE_BYTES,
)
from .protocol import (
    MAX_PAGE_ENTRIES as MAX_PAGE_ENTRIES,
)
from .protocol import (
    PAGE_BYTES as PAGE_BYTES,
)
from .protocol import (
    READ_TOKEN_VARIABLE as READ_TOKEN_VARIABLE,
)
from .protocol import (
    RECORDER_SCHEMA as RECORDER_SCHEMA,
)
from .protocol import (
    recorder_error as recorder_error,
)
from .protocol import (
    scoped_append_token as scoped_append_token,
)
from .server import (
    IDLE_TIMEOUT_SECONDS as IDLE_TIMEOUT_SECONDS,
)
from .server import (
    RecorderServer as RecorderServer,
)
from .server import (
    _Handler as _Handler,
)
from .tls import (
    client_ssl_context as client_ssl_context,
)
from .tls import (
    server_ssl_context as server_ssl_context,
)

__all__ = [
    "RECORDER_SCHEMA",
    "MAX_LINE_BYTES",
    "PAGE_BYTES",
    "MAX_PAGE_ENTRIES",
    "APPEND_TOKEN_VARIABLE",
    "READ_TOKEN_VARIABLE",
    "KEY_FILE",
    "scoped_append_token",
    "recorder_error",
    "RecordedEntry",
    "RecorderVerdict",
    "RecorderComparison",
    "RecorderSource",
    "RUN_GATES",
    "RecorderCore",
    "server_ssl_context",
    "client_ssl_context",
    "IDLE_TIMEOUT_SECONDS",
    "RecorderServer",
    "DEFAULT_CONNECTIONS",
    "RecorderClient",
    "parse_reply",
    "MirrorStats",
    "RecorderMirror",
    "compare_with_recorder",
    "RecorderProcess",
    "spawn_recorder",
    "main",
]
