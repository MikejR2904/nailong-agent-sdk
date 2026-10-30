import json

import pytest

from nailong_agent_sdk.agent.verification import (
    MAX_ALL_OF_MEMBERS,
    MAX_EVIDENCE_CHARS,
    AllOfVerificationGate,
    CallableVerificationGate,
    VerificationContext,
    VerificationDecision,
    VerificationGateRegistry,
    clip_evidence_text,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import canonical_hash, strict_canonical_json
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_models import TelemetryAuthority
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import agent, arun, definition, final, task


def run_gate(registry, gate_id, **kwargs):
    subject, _ = agent(
        [final()], definition_=definition(gate=gate_id), verification_gates=registry, **kwargs
    )
    return arun(subject.run(task("verify-run")))


def completed(result):
    (event,) = [e for e in result.events if e.type == "verification-completed"]
    return event.details


def test_a_plain_bool_gate_is_recorded_with_its_gate_id_and_the_output_digest():
    registry = VerificationGateRegistry()
    registry.register_callable("plain", lambda ctx: True)
    result = run_gate(registry, "plain")
    details = completed(result)
    assert result.status is AgentRunStatus.COMPLETED
    assert details["passed"] is True and details["gate_id"] == "plain"
    assert details["output_digest"] == canonical_hash(result.output)
    assert "verifier_id" not in details and "verifier_kind" not in details


def test_the_built_in_gate_names_itself_and_its_kind():
    details = completed(run_gate(VerificationGateRegistry(), "status-is-complete"))
    assert details["verifier_id"] == "status-is-complete"
    assert details["verifier_kind"] == "deterministic"


def test_a_declared_verifier_is_recorded_with_its_kind_and_evidence():
    registry = VerificationGateRegistry()
    registry.register(
        "tests",
        CallableVerificationGate(
            lambda ctx: VerificationDecision(
                True, evidence={"suite": "unit", "passed": 41, "failed": 0}
            ),
            verifier_id="pytest@8",
            authority=TelemetryAuthority.TOOL,
        ),
    )
    details = completed(run_gate(registry, "tests"))
    assert details["verifier_id"] == "pytest@8" and details["verifier_kind"] == "tool"
    assert details["evidence"] == {"suite": "unit", "passed": 41, "failed": 0}


def test_a_decision_can_declare_its_own_verifier_over_the_gates():
    registry = VerificationGateRegistry()
    registry.register(
        "mixed",
        CallableVerificationGate(
            lambda ctx: VerificationDecision(False, "needs a person", verifier_id="reviewer-7"),
            verifier_id="gate-default",
            authority=TelemetryAuthority.HUMAN,
        ),
    )
    result = run_gate(registry, "mixed")
    details = completed(result)
    assert result.status is AgentRunStatus.FAILED and result.reason == "needs a person"
    assert details["verifier_id"] == "reviewer-7" and details["verifier_kind"] == "human"


def test_the_telemetry_event_carries_the_declared_authority_class(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    audit = AuditTranscriptStore(tmp_path)
    try:
        registry = VerificationGateRegistry()
        registry.register(
            "judge",
            CallableVerificationGate(
                lambda ctx: VerificationDecision(True, evidence={"score": 0.93}),
                verifier_id="llm-judge",
                authority=TelemetryAuthority.MODEL_MEDIATED,
            ),
        )
        run_gate(registry, "judge", telemetry=telemetry, audit_logs=audit)
        (event,) = telemetry.list_events("verify-run", event_types=["agent.verification-completed"])
        assert event.authority is TelemetryAuthority.MODEL_MEDIATED
        assert event.payload["details"]["verifier_id"] == "llm-judge"
        assert event.payload["details"]["evidence"] == {"score": 0.93}
        (entry,) = [
            e for e in audit.list_entries("verify-run") if e.event_type == "verification-completed"
        ]
        assert entry.payload["details"]["gate_id"] == "judge"
        assert entry.payload["details"]["output_digest"]
    finally:
        telemetry.close()


def test_an_undeclared_gate_keeps_the_deterministic_envelope(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        registry = VerificationGateRegistry()
        registry.register_callable("plain", lambda ctx: (True, None))
        run_gate(registry, "plain", telemetry=telemetry)
        (event,) = telemetry.list_events("verify-run", event_types=["agent.verification-completed"])
        assert event.authority is TelemetryAuthority.DETERMINISTIC
        assert "verifier_kind" not in event.payload["details"]
    finally:
        telemetry.close()


def test_a_gate_failure_is_recorded_with_its_gate_and_digest():
    registry = VerificationGateRegistry()
    registry.register_callable("boom", lambda ctx: 1 / 0)
    result = run_gate(registry, "boom")
    details = completed(result)
    assert result.failure.code == "VERIFICATION_GATE_UNEXPECTED_ERROR"
    assert details["passed"] is False and details["gate_id"] == "boom"
    assert details["output_digest"] and details["failure_code"] == result.failure.code


def test_invalid_declared_metadata_is_refused_when_the_gate_is_registered():
    class Blank:
        verifier_id = "  "

        def verify(self, ctx):
            return True

    registry = VerificationGateRegistry()
    with pytest.raises(AgentSdkError) as blank:
        registry.register("blank", Blank())
    assert blank.value.code == "VERIFICATION_GATE_METADATA_INVALID"
    assert 'Verification gate "blank" declares invalid metadata' in str(blank.value)
    assert "verifier_id must be 1 to 128 non-blank characters" in str(blank.value)
    with pytest.raises(AgentSdkError) as unknown:
        registry.register("odd", CallableVerificationGate(lambda ctx: True, authority="oracle"))
    assert 'authority "oracle" is not one of: deterministic' in str(unknown.value)
    registry.register("fine", CallableVerificationGate(lambda ctx: True, authority="human"))
    assert registry.resolve("fine") is not None


def test_metadata_changed_after_registration_still_fails_the_run_naming_the_gate():
    class Mutable:
        verifier_id = "checker"

        def verify(self, ctx):
            return True

    gate = Mutable()
    registry = VerificationGateRegistry()
    registry.register("mutable", gate)
    gate.verifier_id = "  "
    result = run_gate(registry, "mutable")
    assert result.failure.code == "VERIFICATION_GATE_METADATA_INVALID"
    assert 'Verification gate "mutable"' in result.reason and "verifier_id" in result.reason


def test_decision_fields_are_validated_where_they_are_built():
    with pytest.raises(ValueError, match="verifier_id must be 1 to 128 non-blank characters"):
        VerificationDecision(True, verifier_id="")
    with pytest.raises(ValueError, match='authority "oracle" is not one of: .*human'):
        VerificationDecision(True, authority="oracle")
    with pytest.raises(ValueError, match="evidence must be a mapping with string keys"):
        VerificationDecision(True, evidence={1: "x"})
    with pytest.raises(ValueError, match="evidence must be JSON-serializable: TypeError"):
        VerificationDecision(True, evidence={"value": object()})
    with pytest.raises(ValueError, match=f"the limit is {MAX_EVIDENCE_CHARS}"):
        VerificationDecision(True, evidence={"blob": "x" * (MAX_EVIDENCE_CHARS + 1)})


def test_authority_given_as_text_is_coerced_and_evidence_is_copied():
    source = {"items": [1, 2]}
    decision = VerificationDecision(True, authority="human", evidence=source)
    source["items"].append(3)
    assert decision.authority is TelemetryAuthority.HUMAN
    assert decision.evidence == {"items": [1, 2]}


def test_all_of_accepts_only_when_every_member_accepts_and_records_each():
    calls = []

    def first(ctx):
        calls.append("first")
        return VerificationDecision(True, verifier_id="schema-check")

    async def second(ctx):
        calls.append("second")
        return True

    gate = AllOfVerificationGate(
        [CallableVerificationGate(first), CallableVerificationGate(second)], verifier_id="both"
    )
    registry = VerificationGateRegistry()
    registry.register("both", gate)
    result = run_gate(registry, "both")
    details = completed(result)
    assert result.status is AgentRunStatus.COMPLETED and calls == ["first", "second"]
    assert details["verifier_id"] == "both"
    members = details["evidence"]["members"]
    assert [m["verifier_id"] for m in members] == ["schema-check", "member-1"]
    assert details["evidence"]["evaluated"] == 2 and details["evidence"]["total"] == 2


def test_all_of_stops_at_the_first_rejection_by_default():
    calls = []

    def rejecting(ctx):
        calls.append("a")
        return VerificationDecision(False, "numbers do not add up", verifier_id="arith")

    def never(ctx):
        calls.append("b")
        return True

    gate = AllOfVerificationGate(
        [CallableVerificationGate(rejecting), CallableVerificationGate(never)]
    )
    registry = VerificationGateRegistry()
    registry.register("strict", gate)
    result = run_gate(registry, "strict")
    assert result.status is AgentRunStatus.FAILED and calls == ["a"]
    assert result.reason == "arith: numbers do not add up"
    assert completed(result)["evidence"]["evaluated"] == 1


def test_all_of_can_collect_every_rejection():
    gate = AllOfVerificationGate(
        [
            CallableVerificationGate(lambda ctx: (False, "one"), verifier_id="a"),
            CallableVerificationGate(lambda ctx: (False, "two"), verifier_id="b"),
        ],
        stop_at_first_failure=False,
    )
    registry = VerificationGateRegistry()
    registry.register("all", gate)
    result = run_gate(registry, "all")
    assert result.reason == "a: one; b: two"
    assert completed(result)["evidence"]["evaluated"] == 2


def test_the_composite_declares_its_least_deterministic_member():
    gate = AllOfVerificationGate(
        [
            CallableVerificationGate(lambda ctx: True, authority=TelemetryAuthority.DETERMINISTIC),
            CallableVerificationGate(lambda ctx: True, authority=TelemetryAuthority.MODEL_MEDIATED),
        ]
    )
    registry = VerificationGateRegistry()
    registry.register("mixed", gate)
    assert completed(run_gate(registry, "mixed"))["verifier_kind"] == "model-mediated"
    human = AllOfVerificationGate(
        [
            CallableVerificationGate(lambda ctx: True, authority=TelemetryAuthority.MODEL_MEDIATED),
            CallableVerificationGate(lambda ctx: True, authority=TelemetryAuthority.HUMAN),
        ]
    )
    registry.register("with-person", human)
    assert completed(run_gate(registry, "with-person"))["verifier_kind"] == "human"


def test_a_member_that_raises_is_named_by_index_and_verifier():
    registry = VerificationGateRegistry()
    registry.register(
        "composite",
        AllOfVerificationGate(
            [
                CallableVerificationGate(lambda ctx: True, verifier_id="ok"),
                CallableVerificationGate(lambda ctx: 1 / 0, verifier_id="flaky"),
            ],
            verifier_id="composite",
        ),
    )
    result = run_gate(registry, "composite")
    assert result.failure.code == "VERIFICATION_GATE_MEMBER_FAILED"
    assert 'Member 1 ("flaky") of verification gate "composite" raised ZeroDivisionError' in (
        result.reason
    )


def test_registered_gates_can_be_composed_by_id():
    registry = VerificationGateRegistry()
    registry.register_callable("format", lambda ctx: True)
    registry.register_callable("content", lambda ctx: (False, "missing the summary"))
    registry.register_all_of("publishable", ["format", "content"])
    result = run_gate(registry, "publishable")
    assert result.status is AgentRunStatus.FAILED
    assert result.reason == "content: missing the summary"
    details = completed(result)
    assert details["gate_id"] == "publishable" and details["verifier_id"] == "publishable"
    assert [m["verifier_id"] for m in details["evidence"]["members"]] == ["format", "content"]


def test_composing_an_unknown_gate_names_it():
    registry = VerificationGateRegistry()
    registry.register_callable("format", lambda ctx: True)
    with pytest.raises(Exception, match='Unknown verification gate "ghost"'):
        registry.register_all_of("publishable", ["format", "ghost"])


def test_composite_size_is_bounded():
    with pytest.raises(ValueError, match="needs at least one member gate"):
        AllOfVerificationGate([])
    too_many = [CallableVerificationGate(lambda ctx: True)] * (MAX_ALL_OF_MEMBERS + 1)
    with pytest.raises(ValueError, match=f"at most {MAX_ALL_OF_MEMBERS} member gates"):
        AllOfVerificationGate(too_many)


def test_composite_evidence_stays_within_the_limit_for_many_verbose_members():
    members = [
        CallableVerificationGate(
            lambda ctx: (False, "é" * 400), verifier_id=f"member-{index:02d}-" + "x" * 100
        )
        for index in range(MAX_ALL_OF_MEMBERS)
    ]
    gate = AllOfVerificationGate(members, stop_at_first_failure=False)
    decision = arun(
        gate.verify(VerificationContext(output={}, definition=definition(), task=task()))
    )
    assert decision.passed is False
    assert len(decision.evidence["members"]) == MAX_ALL_OF_MEMBERS
    assert len(strict_canonical_json(decision.evidence)) <= MAX_EVIDENCE_CHARS


def test_a_member_declaring_an_unknown_authority_is_named_when_the_composite_is_built():
    ran = []

    def spy(ctx):
        ran.append("first")
        return True

    with pytest.raises(AgentSdkError) as raised:
        AllOfVerificationGate(
            [
                CallableVerificationGate(spy, verifier_id="first"),
                CallableVerificationGate(lambda ctx: True, verifier_id="odd", authority="oracle"),
            ],
            verifier_id="composite",
        )
    assert raised.value.code == "VERIFICATION_GATE_METADATA_INVALID"
    assert 'Member 1 ("odd") of verification gate "composite" declares invalid metadata' in str(
        raised.value
    )
    assert ran == []


def test_a_member_whose_authority_changes_after_the_composite_is_built_is_named_at_run_time():
    member = CallableVerificationGate(lambda ctx: True, verifier_id="odd", authority="human")
    gate = AllOfVerificationGate([member], verifier_id="composite")
    object.__setattr__(member, "authority", "oracle")
    context = VerificationContext(output={}, definition=definition(), task=task())
    with pytest.raises(AgentSdkError) as raised:
        arun(gate.verify(context))
    assert raised.value.code == "VERIFICATION_GATE_METADATA_INVALID"
    assert 'Member 0 ("odd") of verification gate "composite" declares authority' in str(
        raised.value
    )


def test_a_member_declaring_its_authority_as_text_is_coerced():
    gate = AllOfVerificationGate(
        [CallableVerificationGate(lambda ctx: True, verifier_id="reviewer", authority="human")]
    )
    context = VerificationContext(output={}, definition=definition(), task=task())
    decision = arun(gate.verify(context))
    assert decision.authority is TelemetryAuthority.HUMAN
    assert decision.evidence["members"][0]["authority"] == "human"


def test_evidence_text_is_clipped_by_its_escaped_length():
    assert clip_evidence_text("abcdef", 4) == "abcd"
    assert clip_evidence_text("abc", 10) == "abc"
    assert clip_evidence_text("ééé", 12) == "éé"
    assert clip_evidence_text('""""', 5) == '""'


def test_evidence_with_a_number_json_cannot_carry_is_refused():
    with pytest.raises(ValueError, match="evidence must be JSON-serializable: ValueError"):
        VerificationDecision(True, evidence={"score": float("nan")})
    with pytest.raises(ValueError, match="evidence must be JSON-serializable: ValueError"):
        VerificationDecision(True, evidence={"nested": [float("inf")]})


def test_a_composite_refuses_an_invalid_verifier_id_when_it_is_built():
    member = CallableVerificationGate(lambda ctx: True)
    with pytest.raises(ValueError, match="verifier_id must be 1 to 128 non-blank characters"):
        AllOfVerificationGate([member], verifier_id="x" * 129)
    with pytest.raises(ValueError, match="verifier_id must be 1 to 128 non-blank characters"):
        AllOfVerificationGate([member], verifier_id="   ")
    registry = VerificationGateRegistry()
    registry.register_callable("fine", lambda ctx: True)
    with pytest.raises(ValueError, match="verifier_id must be 1 to 128 non-blank characters"):
        registry.register_all_of("g" * 129, ["fine"])


ACCENT = chr(0xE9)
EMPTY_ROW = len('{"b":""}')


def test_evidence_of_exactly_the_limit_is_accepted_and_one_more_character_is_refused():
    exact = {"b": "x" * (MAX_EVIDENCE_CHARS - EMPTY_ROW)}
    assert len(strict_canonical_json(exact)) == MAX_EVIDENCE_CHARS
    assert VerificationDecision(True, evidence=exact).evidence == exact
    over = {"b": "x" * (MAX_EVIDENCE_CHARS - EMPTY_ROW + 1)}
    with pytest.raises(ValueError, match=f"is {MAX_EVIDENCE_CHARS + 1} characters of JSON"):
        VerificationDecision(True, evidence=over)


def test_the_limit_counts_the_escaped_form_so_a_non_ascii_character_costs_six():
    assert len(strict_canonical_json({"b": ACCENT})) - EMPTY_ROW == 6
    fits = {"b": ACCENT * ((MAX_EVIDENCE_CHARS - EMPTY_ROW) // 6)}
    assert VerificationDecision(True, evidence=fits).evidence == fits
    over = {"b": ACCENT * ((MAX_EVIDENCE_CHARS - EMPTY_ROW) // 6 + 1)}
    with pytest.raises(ValueError, match="the limit is 4096"):
        VerificationDecision(True, evidence=over)


def composite_context():
    return VerificationContext(output={}, definition=definition(), task=task())


def test_a_composite_keeps_each_members_own_evidence():
    gate = AllOfVerificationGate(
        [
            CallableVerificationGate(
                lambda ctx: VerificationDecision(True, evidence={"return_code": 0}),
                verifier_id="command:build",
            ),
            CallableVerificationGate(
                lambda ctx: VerificationDecision(
                    True, evidence={"actual": "ab12", "expected": "ab12"}
                ),
                verifier_id="sha256:report",
            ),
            CallableVerificationGate(lambda ctx: True, verifier_id="plain"),
        ]
    )
    decision = arun(gate.verify(composite_context()))
    first, second, third = decision.evidence["members"]
    assert first["evidence"] == {"return_code": 0}
    assert second["evidence"] == {"actual": "ab12", "expected": "ab12"}
    assert "evidence" not in third


def test_member_evidence_that_does_not_fit_is_replaced_by_its_size_and_the_rest_still_fits():
    big = {"blob": "x" * 3_000}
    small = {"return_code": 0}
    gate = AllOfVerificationGate(
        [
            CallableVerificationGate(
                lambda ctx: VerificationDecision(True, evidence=big), verifier_id="big-1"
            ),
            CallableVerificationGate(
                lambda ctx: VerificationDecision(True, evidence=big), verifier_id="big-2"
            ),
            CallableVerificationGate(
                lambda ctx: VerificationDecision(True, evidence=small), verifier_id="small"
            ),
        ]
    )
    decision = arun(gate.verify(composite_context()))
    first, second, third = decision.evidence["members"]
    assert first["evidence"] == big
    encoded = len(json.dumps(big, sort_keys=True, separators=(",", ":")))
    assert second["evidence"] == {"omitted_chars": encoded}
    assert third["evidence"] == small
    assert len(strict_canonical_json(decision.evidence)) <= MAX_EVIDENCE_CHARS
