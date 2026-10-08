import typing

import pytest

from nailong_agent_sdk.integrations import JevDecisionProvider as ExportedProvider
from nailong_agent_sdk.integrations.jev import JevDecisionProvider as ExportedJevProvider
from nailong_agent_sdk.integrations.jev.advisory import JevAdvisoryVerificationGate
from nailong_agent_sdk.integrations.jev.architecture import JevArchitectureRouter
from nailong_agent_sdk.integrations.jev.decision import TypeSafeJevDecisionEvaluator
from nailong_agent_sdk.integrations.jev.exploration import JevExplorationAdvisor
from nailong_agent_sdk.integrations.jev.models import (
    JevDecisionProvider,
    JevDecisionRequest,
    JevDecisionResult,
)


def test_the_protocol_is_exported_where_the_other_jev_contracts_are():
    assert ExportedProvider is JevDecisionProvider and ExportedJevProvider is JevDecisionProvider


def test_the_protocol_takes_the_jev_request_and_returns_the_jev_result():
    hints = typing.get_type_hints(JevDecisionProvider.evaluate)
    assert hints == {"request": JevDecisionRequest, "return": JevDecisionResult}


def test_the_concrete_evaluator_declares_the_signature_the_protocol_requires():
    required = typing.get_type_hints(JevDecisionProvider.evaluate)
    assert typing.get_type_hints(TypeSafeJevDecisionEvaluator.evaluate) == required


@pytest.mark.parametrize(
    "consumer",
    [JevAdvisoryVerificationGate, JevArchitectureRouter, JevExplorationAdvisor],
)
def test_every_class_that_calls_an_evaluator_declares_that_protocol(consumer):
    assert typing.get_type_hints(consumer.__init__)["evaluator"] is JevDecisionProvider
