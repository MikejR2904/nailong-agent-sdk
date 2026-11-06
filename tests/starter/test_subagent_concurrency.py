from nailong_agent_sdk.foundations.contracts import ToolConcurrency

from .test_starter_agent import agent_for, final, scripted


def test_a_read_only_agent_used_as_a_tool_can_run_beside_others_and_a_writing_one_cannot(tmp_path):
    helper = agent_for(tmp_path, scripted(final("x")))
    read_only = helper.as_tool(name="agent_reader", description="Reads.", read_only=True)
    writer = helper.as_tool(name="agent_writer", description="Writes.", read_only=False)
    assert read_only.definition.concurrency is ToolConcurrency.PARALLEL_SAFE
    assert writer.definition.concurrency is ToolConcurrency.SERIAL
