from tests.support.agents import FnExecutor, ok, tool

ECHO = tool("echo")


def echo_executor():
    return FnExecutor(lambda t, c: ok({"echo": c.call.arguments}))
