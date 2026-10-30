import pytest

from nailong_agent_sdk.foundations.errors import (
    assert_no_hidden_reasoning,
    contains_secret_text,
    redact_hidden_reasoning,
    redact_secrets,
    sanitize_failure_details,
)

OPENAI_PROJECT_KEY = "sk-proj-" + "aB3_" * 12
ANTHROPIC_KEY = "sk-ant-api03-" + "Zx9-" * 12
OPENROUTER_KEY = "sk-or-v1-" + "0123456789abcdef" * 4


@pytest.mark.parametrize(
    "secret",
    [
        OPENAI_PROJECT_KEY,
        ANTHROPIC_KEY,
        OPENROUTER_KEY,
        "sk_live_" + "a1B2c3D4e5F6g7H8",
        "ASIA" + "ABCDEFGHIJKLMNOP",
        "github_pat_" + "11ABCDEFG0123456789_abcdefghij",
        "glpat-" + "abcdefghij0123456789",
        "hf_" + "a" * 34,
        "AIza" + "SyA-abcdefghijklmnopqrstuvwxyz01234",
        "npm_" + "a" * 36,
        "fw_" + "abcdefghijklmnopqrstu",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    ],
)
def test_more_credential_shapes_are_masked_in_free_text(secret):
    text = f"before {secret} after"
    redacted = redact_secrets(text)
    assert secret not in redacted
    assert redacted == "before [REDACTED] after"
    assert contains_secret_text(text) is True


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("authorization: basic dXNlcjpwYXNzd29yZA== trailing", "dXNlcjpwYXNzd29yZA"),
        ("Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        (
            '{"authorization": "Digest username=admin response=abcdef0123456789"}',
            "abcdef0123456789",
        ),
        ("postgres://app:hunter2hunter2@db.internal/prod", "hunter2hunter2"),
        ("https://deploy:glue-4ever-pass@git.example.com/org/repo.git", "glue-4ever-pass"),
        ("api_key = 'abcd efgh ijkl'", "ijkl"),
        ('PASSPHRASE: "correct horse battery"', "battery"),
        ("db_passwd=s3cr3t-value", "s3cr3t-value"),
    ],
)
def test_credentials_that_a_single_token_rule_missed_are_masked(text, leaked):
    assert leaked not in redact_secrets(text)


def test_the_text_around_a_masked_url_credential_survives():
    redacted = redact_secrets("connect to postgres://app:hunter2hunter2@db.internal/prod now")
    assert redacted == "connect to postgres://app:[REDACTED]@db.internal/prod now"
    assert redact_secrets("see https://example.com/a:b@c and ftp://host/path") == (
        "see https://example.com/a:b@c and ftp://host/path"
    )


def test_text_that_only_looks_like_a_prefix_is_untouched():
    text = (
        "task-12345678901234567890123 risk-assessment-for-the-clock-tree "
        "the key points follow; ask-me-anything-about-the-design-please"
    )
    assert redact_secrets(text) == text


def test_secrets_inside_tuples_sets_frozensets_and_bytes_are_masked():
    secret = "sk-" + "a" * 30
    value = {
        "command": ("curl", "-H", f"Authorization: Bearer {'b' * 24}"),
        "nested": {"items": (secret, {"deeper": (secret,)})},
        "tags": {secret},
        "frozen": frozenset({secret}),
        "raw": secret.encode("utf-8"),
        "other": [("password: hunter2hunter2",)],
    }
    redacted = redact_secrets(value)
    assert secret not in repr(redacted)
    assert "hunter2hunter2" not in repr(redacted)
    assert "b" * 24 not in repr(redacted)
    assert isinstance(redacted["command"], tuple) and redacted["command"][:2] == ("curl", "-H")
    assert isinstance(redacted["tags"], set) and isinstance(redacted["frozen"], frozenset)
    assert isinstance(redacted["raw"], str)
    assert sanitize_failure_details({"argv": (secret,)})["argv"] == ("[REDACTED]",)


def test_hidden_reasoning_is_found_and_removed_inside_tuples_and_sets():
    with pytest.raises(ValueError, match="scratchpad"):
        assert_no_hidden_reasoning({"calls": ({"scratchpad": "x"},)})
    redacted = redact_hidden_reasoning({"calls": ({"scratchpad": "x", "ok": 1},)})
    assert redacted == {"calls": [{"scratchpad_redacted": "[REDACTED]", "ok": 1}]}
    assert sanitize_failure_details({"calls": ({"chain_of_thought": "x"},)})["calls"] == (
        {"chain_of_thought": "[REDACTED]"},
    )


@pytest.mark.parametrize(
    "key", ["passwd", "passphrase", "db_passphrase", "access_key", "signing_key"]
)
def test_more_secret_looking_keys_are_redacted(key):
    assert redact_secrets({key: "value"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize(
    "key", ["primary_key", "sort_key", "keyboard", "author", "cache_key_count"]
)
def test_ordinary_keys_that_merely_contain_key_are_not_redacted(key):
    assert redact_secrets({key: "value"}) == {key: "value"}


@pytest.mark.parametrize(
    "unit", ["sk-", "eyJ", "sk-eyJ", "glpat-", "://a:", "hf_", "sk_live_", "fw_"]
)
def test_redaction_stays_linear_on_repeated_prefixes(run_py, unit):
    code = f"""
    import time
    from nailong_agent_sdk.foundations.errors import redact_secrets
    for n in (20000, 40000, 80000):
        text = {unit!r} * n
        start = time.perf_counter()
        redact_secrets(text)
        print(n, len(text), round(time.perf_counter() - start, 3))
    """
    result = run_py(code, timeout=300)
    rows = [line.split() for line in result.stdout.strip().splitlines()]
    t1, t2, t3 = (float(row[2]) for row in rows)
    assert t3 < 8 * max(t1, 0.05) or t3 < 1.0, f"timings {t1}, {t2}, {t3} for {unit!r}"
