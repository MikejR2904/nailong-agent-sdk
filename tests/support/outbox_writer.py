import sys
import time
from pathlib import Path

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.recorder.mirror import RecorderMirror


class DownClient:
    def append(self, *args, **kwargs):
        raise AgentSdkError("RECORDER_UNREACHABLE", "the recorder is down")

    def close(self):
        return None


mirror = RecorderMirror(DownClient(), asynchronous=True, outbox=Path(sys.argv[1]))
for sequence in (1, 2, 3):
    mirror._send("run-k", "audit", sequence, f"{sequence:064x}", None, None)
print("ready", flush=True)
time.sleep(120)
