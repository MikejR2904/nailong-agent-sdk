import sys
import textwrap

from nailong_agent_sdk.tools.supervisor import CommandTemplate

PY = sys.executable


def template(name, code, *, timeout=30, **extra):
    return CommandTemplate(
        name=name, command=[PY, "-c", textwrap.dedent(code)], timeout_seconds=timeout, **extra
    )
