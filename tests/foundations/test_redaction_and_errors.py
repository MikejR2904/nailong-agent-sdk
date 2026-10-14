import pytest

from nailong_agent_sdk.foundations.errors import (
    AgentSdkError,
    TransientProviderError,
    assert_no_hidden_reasoning,
    redact_secrets,
    sanitize_failure_details,
)


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "API-KEY",
        "apikey",
        "Authorization",
        "password",
        "client_secret",
        "access_token",
        "refresh_token",
        "cookie",
        "credential",
        "private_key",
        "token",
    ],
)
def test_secret_looking_keys_are_redacted(key):
    assert redact_secrets({key: "value"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize(
    "key", ["token_budget", "input_tokens", "max_tokens", "total_tokens", "token_cost"]
)
def test_token_count_keys_are_not_redacted(key):
    assert redact_secrets({key: 5}) == {key: 5}


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "A" * 24,
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_" + "a" * 36,
        "xoxb-1234567890-abcdefghij",
        "Bearer abcdefghijklmnop12345",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_known_credential_shapes_are_masked_in_free_text(secret):
    text = f"before {secret} after"
    redacted = redact_secrets(text)
    assert secret not in redacted
    assert redacted.startswith("before ") and redacted.endswith(" after")


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("FIREWORKS_API_KEY=fw_abcdefghijklmnop1234", "fw_abcdefghijklmnop1234"),
        ('{"api_key": "abcdef123456"}', "abcdef123456"),
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI"),
    ],
)
def test_assignment_style_secrets_are_masked(text, leaked):
    assert leaked not in redact_secrets(text)


def test_non_secret_text_is_untouched():
    text = "The token budget is 4000 tokens; status = ok; key points follow."
    assert redact_secrets(text) == text


def test_sanitize_failure_details_bounds_and_masks():
    out = sanitize_failure_details(
        {"api_key": "x" * 10, "chain_of_thought": "secret plan", "note": "ok"}
    )
    assert (
        out["api_key"] == "[REDACTED]"
        and out["chain_of_thought"] == "[REDACTED]"
        and out["note"] == "ok"
    )
    big = sanitize_failure_details({"blob": "z" * 10_000})
    assert big["truncated"] is True and "content_hash" in big and big["original_chars"] > 2048


def test_assert_no_hidden_reasoning_nested():
    assert_no_hidden_reasoning({"a": [{"b": 1}]})
    with pytest.raises(ValueError):
        assert_no_hidden_reasoning({"a": [{"scratchpad": "x"}]})


def test_error_types():
    error = TransientProviderError("C", "boom", retry_after_seconds=2.0)
    assert (
        str(error) == "boom"
        and isinstance(error, AgentSdkError)
        and error.retry_after_seconds == 2.0
    )


def test_redaction_is_linear_on_adversarial_private_key_markers(run_py):
    code = """
    import time
    from nailong_agent_sdk.foundations.errors import redact_secrets
    for n in (1000, 2000, 4000):
        text = "-----BEGIN PRIVATE KEY-----" * n
        start = time.perf_counter()
        redact_secrets(text)
        print(n, len(text), round(time.perf_counter() - start, 3))
    """
    result = run_py(code, timeout=300)
    rows = [line.split() for line in result.stdout.strip().splitlines()]
    t1, t2, t3 = (float(row[2]) for row in rows)
    assert t3 < 8 * max(t1, 0.01) or t3 < 1.0, f"timings {t1}, {t2}, {t3}"


def test_redaction_is_linear_on_long_identifier_runs(run_py):
    code = """
    import time
    from nailong_agent_sdk.foundations.errors import redact_secrets
    for n in (5000, 10000, 20000):
        text = "password" * n
        start = time.perf_counter()
        redact_secrets(text)
        print(n, len(text), round(time.perf_counter() - start, 3))
    """
    result = run_py(code, timeout=300)
    rows = [line.split() for line in result.stdout.strip().splitlines()]
    t1, t2, t3 = (float(row[2]) for row in rows)
    assert t3 < 8 * max(t1, 0.01) or t3 < 1.0, f"timings {t1}, {t2}, {t3}"
