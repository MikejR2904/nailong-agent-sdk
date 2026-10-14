from nailong_agent_sdk.agent.model import ModelTurnResponse
from tests.support.agents import TURN_ADAPTER


class UsageTurns:
    def __init__(self, turns, usage):
        self._turns = list(turns)
        self._usage = usage

    async def next_turn(self, context):
        turn = TURN_ADAPTER.validate_python(self._turns.pop(0))
        return ModelTurnResponse(turn=turn, usage=self._usage)


def spans_of(payload):
    (resource_spans,) = payload["resourceSpans"]
    (scope_spans,) = resource_spans["scopeSpans"]
    return scope_spans["spans"]


def attributes_of(item):
    return {entry["key"]: entry["value"] for entry in item["attributes"]}
