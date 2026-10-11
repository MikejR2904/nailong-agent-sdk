import pytest

from nailong_agent_sdk.agent.model import FailoverAgentModel, ModelContext
from nailong_agent_sdk.foundations.contracts import FallbackModelBinding, ModelBinding
from tests.support.agents import arun, final
from tests.support.model_context import model_context

PRIMARY = ModelBinding(
    provider="p",
    model="primary",
    parameters={"temperature": 0},
    fallbacks=[
        FallbackModelBinding(provider="q", model="backup-1", parameters={"top_p": 0.5}),
        FallbackModelBinding(provider="r", model="backup-2"),
    ],
)
BINDINGS = [PRIMARY, *PRIMARY.fallbacks]


class Adapter:
    def __init__(self, fails):
        self.fails = fails
        self.seen = []

    async def next_turn(self, context: ModelContext):
        self.seen.append(context.model_binding)
        if self.fails:
            raise RuntimeError("down")
        return final()


def adapters(*fails):
    return [Adapter(fail) for fail in fails]


def test_each_adapter_receives_its_own_binding_when_bindings_are_given():
    models = adapters(True, True, False)
    failover = FailoverAgentModel(models, bindings=BINDINGS, max_retries_per_model=0)
    arun(failover.next_turn(model_context(binding=PRIMARY)))
    assert [seen[0].model for seen in (m.seen for m in models)] == [
        "primary",
        "backup-1",
        "backup-2",
    ]
    assert models[1].seen[0] == ModelBinding(
        provider="q", model="backup-1", parameters={"top_p": 0.5}
    )
    assert models[2].seen[0].fallbacks == []


def test_the_primary_adapter_keeps_the_context_it_was_given():
    models = adapters(False)
    context = model_context(binding=PRIMARY)
    arun(
        FailoverAgentModel(models, bindings=BINDINGS[:1], max_retries_per_model=0).next_turn(
            context
        )
    )
    assert models[0].seen == [PRIMARY]


def test_without_bindings_every_adapter_sees_the_same_context():
    models = adapters(True, False)
    arun(
        FailoverAgentModel(models, max_retries_per_model=0).next_turn(
            model_context(binding=PRIMARY)
        )
    )
    assert models[0].seen == models[1].seen == [PRIMARY]


def test_a_streaming_attempt_also_rewrites_the_binding():
    class Streaming(Adapter):
        async def stream_turn(self, context, on_delta):
            return await self.next_turn(context)

    models = [Streaming(True), Streaming(False)]
    failover = FailoverAgentModel(models, bindings=BINDINGS[:2], max_retries_per_model=0)
    arun(failover.stream_turn(model_context(binding=PRIMARY), lambda delta: None))
    assert [m.seen[0].model for m in models] == ["primary", "backup-1"]


def test_the_number_of_bindings_must_match_the_number_of_adapters():
    with pytest.raises(ValueError) as raised:
        FailoverAgentModel(adapters(False, False), bindings=BINDINGS[:1])
    assert str(raised.value) == (
        "FailoverAgentModel received 1 binding(s) for 2 adapter(s); pass exactly one binding "
        "per adapter, primary first."
    )
