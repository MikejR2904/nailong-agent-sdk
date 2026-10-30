import json

import pytest
from trust_bench.judge import budget as budget_module
from trust_bench.judge import client, specs
from trust_bench.judge.budget import Budget, BudgetExceeded
from trust_bench.judge.cache import AnswerCache, cache_key

from nailong_agent_sdk.integrations.jev.models import JevDecisionRequest, JevNoulQuestion
from tests.support.jev_judge import ScriptedProvider

STATE = {"task": "t", "events": [{"decision": "a#0"}]}


def request(state=None, model="jev-1.13.0", spec=None):
    return JevDecisionRequest(
        purpose="verification",
        run_id="r",
        state=state or STATE,
        question_spec=spec or specs.acceptance_spec(),
        model=model,
    )


def test_a_budget_stops_the_call_that_would_pass_its_ceiling_and_says_which_ceiling():
    budget = Budget(max_calls=2, max_input_tokens=1000)
    budget.reserve(400)
    budget.reserve(400)
    with pytest.raises(BudgetExceeded, match="the call ceiling of 2 is reached after 2 calls"):
        budget.reserve(1)
    tokens = Budget(max_calls=9, max_input_tokens=1000)
    tokens.reserve(900)
    with pytest.raises(
        BudgetExceeded, match=r"about 200 input tokens .* ceiling of 1000 \(spent 900\)"
    ):
        tokens.reserve(200)


def test_settling_replaces_the_estimate_with_the_tokens_the_provider_billed():
    budget = Budget(max_calls=5, max_input_tokens=1000)
    budget.reserve(300)
    budget.settle(300, 120)
    assert budget.spent.input_tokens == 120 and budget.spent.calls == 1
    budget.settle(0, None)
    assert budget.spent.input_tokens == 120


def test_a_budget_of_nothing_is_refused_and_the_price_matches_the_published_one():
    with pytest.raises(
        ValueError, match="at least one call and one token, got 0 calls and 5 tokens"
    ):
        Budget(0, 5)
    assert budget_module.dollars(1_000_000) == pytest.approx(0.042)
    assert budget_module.estimate_tokens("x" * 30) == 10


def test_the_cache_key_changes_with_the_model_the_state_and_the_questions():
    base = cache_key(request())
    assert base == cache_key(request())
    assert base != cache_key(request(model="jev-1.14.0"))
    assert base != cache_key(request(state={"task": "other"}))
    assert base != cache_key(request(spec=specs.allocation_spec({"D": "d", "M": "m"})))


def test_a_stored_answer_comes_back_after_a_reload_and_a_damaged_file_is_named(tmp_path):
    path = tmp_path / "cache.jsonl"
    provider = ScriptedProvider()
    judge = client.JevJudge(provider, budget=Budget(5, 10_000), cache=AnswerCache(path))
    judge.ask(specs.acceptance_spec(), STATE)
    reloaded = AnswerCache(path)
    assert len(reloaded) == 1 and reloaded.get(cache_key(request())) is not None
    path.write_text(path.read_text() + "not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2 of the answer cache .* is not a stored answer"):
        AnswerCache(path)


def test_a_repeated_question_is_answered_from_the_cache_without_spending_anything():
    provider = ScriptedProvider()
    budget = Budget(5, 10_000)
    judge = client.JevJudge(provider, budget=budget, cache=AnswerCache())
    first = judge.ask(specs.acceptance_spec(), STATE)
    second = judge.ask(specs.acceptance_spec(), STATE)
    assert not first.cached and second.cached and len(provider.requests) == 1
    assert budget.spent.calls == 1 and first.answers[specs.QUESTION].noul == 0.9


def test_a_failed_call_is_reported_and_not_cached():
    provider = ScriptedProvider(fail=lambda _: True)
    cache = AnswerCache()
    judge = client.JevJudge(provider, budget=Budget(5, 10_000), cache=cache)
    answer = judge.ask(specs.acceptance_spec(), STATE)
    assert not answer.succeeded and answer.reason == "provider-down" and len(cache) == 0


def test_the_judge_refuses_a_call_past_the_budget_before_sending_it():
    provider = ScriptedProvider()
    judge = client.JevJudge(provider, budget=Budget(1, 10_000))
    judge.ask(specs.acceptance_spec(), STATE)
    with pytest.raises(BudgetExceeded):
        judge.ask(specs.acceptance_spec(), {"task": "another question"})
    assert len(provider.requests) == 1


def test_the_pinned_model_is_an_exact_release_and_not_an_alias():
    assert client.MODEL == "jev-1.13.0" and "latest" not in client.MODEL
    provider = ScriptedProvider()
    client.JevJudge(provider, budget=Budget(1, 10_000)).ask(specs.acceptance_spec(), STATE)
    assert provider.requests[0].model == "jev-1.13.0"


def test_the_key_is_read_from_the_environment_first_and_then_only_by_name_from_the_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER_KEY=abc\nTYPESAFE_API_KEY='from-file'\n", encoding="utf-8")
    env = {}
    client.ensure_api_key(env, dotenv)
    assert env == {"TYPESAFE_API_KEY": "from-file"}
    env = {"TYPESAFE_API_KEY": "from-env"}
    client.ensure_api_key(env, dotenv)
    assert env["TYPESAFE_API_KEY"] == "from-env"


def test_a_missing_key_is_named_with_both_places_that_were_searched(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("FIREWORKS_API_KEY=x\n", encoding="utf-8")
    env = {}
    with pytest.raises(client.JudgeUnavailable, match="TYPESAFE_API_KEY is set neither in the env"):
        client.ensure_api_key(env, dotenv)
    assert env == {}
    with pytest.raises(client.JudgeUnavailable, match="neither in the environment nor in"):
        client.ensure_api_key({}, tmp_path / "absent.env")


def test_without_the_sdk_package_the_provider_says_how_to_install_it(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    monkeypatch.setitem(__import__("sys").modules, "typesafe_sdk", None)
    with pytest.raises(
        client.JudgeUnavailable, match=r"typesafe_sdk is not installed.*typesafe-sdk>=0.7"
    ):
        client.build_provider(tmp_path / "receipts.jsonl")


def test_the_receipt_sink_appends_one_digest_only_line_per_call(tmp_path):
    from nailong_agent_sdk.integrations.jev.models import JevDecisionReceipt

    sink = client.JsonlReceiptSink(tmp_path / "r.jsonl")
    receipt = JevDecisionReceipt(
        provider="typesafe-jev",
        operation="jev-verification",
        status="succeeded",
        run_id="r",
        projection_digest="0" * 64,
        question_spec_id="s",
        question_spec_version="v1",
        question_spec_digest="1" * 64,
        policy_version="p",
        policy_outcome="succeeded",
        input_tokens=7,
    )
    sink(receipt)
    sink(receipt)
    lines = (tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["input_tokens"] == 7


def test_the_questions_carry_their_frozen_wording_and_an_exit_for_no_single_cause():
    spec = specs.diagnosis_spec([("a#0", "a#0: read"), ("a#1", "a#1: write")])
    question = spec.questions[specs.QUESTION]
    assert set(question.criteria) == {"a#0", "a#1", "none"}
    assert "Choose 'none'" in question.instructions
    accept = specs.acceptance_spec().questions[specs.QUESTION]
    assert isinstance(accept, JevNoulQuestion) and set(accept.criteria) == {True, False}
    allocate = specs.allocation_spec({"D": "d", "M": "m", "H": "h"}).questions[specs.QUESTION]
    assert set(allocate.criteria) == {"D", "M", "H"} and "default" in allocate.instructions
