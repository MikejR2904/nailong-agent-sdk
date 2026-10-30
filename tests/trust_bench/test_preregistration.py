import hashlib

from trust_bench import confirmatory, criteria
from trust_bench.evaluate import ARMS, FAMILIES
from trust_bench.splits import Split, operators_for

from tests.support.paths import REPO_ROOT

TEXT = " ".join(
    (REPO_ROOT / "benchmarks" / "trust" / "PREREGISTRATION.md").read_text(encoding="utf-8").split()
)


def seeds(span):
    return f"{span.start:,} to {span.stop - 1:,}"


def test_every_criterion_in_code_is_a_row_of_the_pre_registration():
    for identifier in criteria.CRITERIA:
        assert f"| {identifier} |" in TEXT or f"| {identifier}." in TEXT, identifier


def test_every_threshold_in_code_is_written_in_the_pre_registration():
    for value in (
        criteria.COMPLETENESS_LOWER,
        criteria.RESOLUTION_LOWER,
        criteria.FALSE_RESOLUTION_UPPER,
        criteria.TAMPER_LOWER,
        criteria.MACRO_TOP1_LOWER,
        criteria.SHORTCUT_CEILING,
        criteria.UNSUPPORTED_UPPER,
        criteria.ESCAPE_UPPER,
        criteria.PUBLISHED_ESCAPE,
        criteria.FALSE_REJECTION_UPPER,
        criteria.CONTROL_LOWER,
        criteria.VIOLATION_UPPER,
        criteria.OVER_ESCALATION_CEILING,
        criteria.PAIRED_LOWER,
        criteria.COVERAGE_LOWER,
        criteria.INSTRUMENT_VIOLATION_LOWER,
    ):
        assert f"{value:g}" in TEXT or f"{value:.2f}" in TEXT, value


def test_the_pre_registration_names_the_locked_seeds_arms_families_and_operators():
    assert seeds(confirmatory.CONFIRMATORY_SEEDS) in TEXT
    assert seeds(confirmatory.CONFIRMATORY_TAMPER_SEEDS) in TEXT
    assert seeds(confirmatory.VALIDATION_SEEDS) in TEXT
    for name in (*FAMILIES, *ARMS):
        assert f"`{name}`" in TEXT, name
    for operator in operators_for(Split.TEST):
        stem = operator.split(":")[0]
        assert stem in TEXT, operator


def test_the_pre_registration_has_no_unfilled_placeholder():
    assert "{{" not in TEXT and "}}" not in TEXT


def test_the_sources_the_thresholds_cite_are_listed_in_the_appendix():
    for identifier in (
        "2609.01931",
        "2606.20634",
        "2607.09996",
        "2608.12635",
        "2609.19844",
        "2607.10059",
        "2608.07899",
    ):
        assert identifier in TEXT, identifier


def test_the_pre_registration_names_the_new_deployments_commands_and_the_judge_pin():
    from trust_bench.authority import allocators

    for name in ("sdk-preapproved", "langgraph-plain", "jev-1.13.0", "TYPESAFE_API_KEY"):
        assert name in TEXT, name
    for command in ("verify", "attack", "authority", "chain", "judge-prepare", "judge-estimate"):
        assert f"`{command}" in TEXT or f"{command} " in TEXT, command
    for command in ("judge-ask", "judge-score", "decision_wrong_form"):
        assert command in TEXT, command
    for allocation in allocators.ARMS:
        assert f"`{allocation}`" in TEXT, allocation


def test_the_pre_registration_states_the_judge_ceilings_the_lock_pins():
    plan = confirmatory.confirmatory_plan()
    assert f"{plan['judge_budget']['max_calls']:,} calls" in TEXT
    assert f"{plan['judge_budget']['max_input_tokens'] // 1_000_000} million input tokens" in TEXT
    assert plan["judge_model"] in TEXT


def test_the_pre_registration_states_the_digest_of_the_frozen_question_wording():
    from trust_bench.judge import specs

    texts = (
        specs.DIAGNOSIS_INSTRUCTIONS,
        specs.ACCEPTANCE_INSTRUCTIONS,
        specs.ALLOCATION_INSTRUCTIONS,
    )
    digest = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()

    assert digest in TEXT


def test_the_frozen_diagnosis_question_is_the_version_the_pre_registration_quotes():
    from trust_bench.judge import specs

    quoted = "| v1 (frozen) | "
    start = TEXT.index(quoted) + len(quoted)
    middle = TEXT[start : TEXT.index(" |", start)]

    assert middle in specs.DIAGNOSIS_INSTRUCTIONS
    assert specs.VERSION == "v1"
