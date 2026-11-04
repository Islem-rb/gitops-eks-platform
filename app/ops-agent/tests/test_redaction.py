import pytest

from agent.redaction import MASK, redact, redact_text

SECRETS = [
    ("postgresql://app:hunter2@postgres:5432/app", "hunter2"),
    ("redis://:s3cret@redis:6379/0", "s3cret"),
    ('FATAL: password "hunter2" failed', None),
    ("password=hunter2 host=db", "hunter2"),
    ("api_key: abc123def", "abc123def"),
    ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
    ("token=abc123", "abc123"),
    ("https://discord.com/api/webhooks/123/abcDEF", "abcDEF"),
    ("key AKIAABCDEFGHIJKLMNOP used", "AKIAABCDEFGHIJKLMNOP"),
    ("sk-ant-api03-abc_DEF-123", "abc_DEF"),
    ("ghp_abcdefghijklmnopqrstuvwxyz", "ghp_abcdefghij"),
    ("eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl", "eyJzdWIiOiIx"),
    ("contact jane.doe@example.com now", "jane.doe@example.com"),
    ("DETAIL:  Key (payload)=(my private data) already exists.", "my private data"),
]


@pytest.mark.parametrize("text,secret", [s for s in SECRETS if s[1]])
def test_secret_shapes_are_removed(text, secret):
    assert secret not in redact_text(text)


def test_host_stays_because_it_helps_diagnosis():
    assert redact_text("postgresql://app:hunter2@postgres:5432/app") == (
        f"postgresql://{MASK}@postgres:5432/app"
    )


def test_diagnostic_text_is_left_alone():
    line = 'INFO: 10.0.0.1:1234 - "GET /jobs HTTP/1.1" 500 Internal Server Error'
    assert redact_text(line) == line
    assert redact_text("password authentication failed for user") == (
        "password authentication failed for user"
    )


def test_sensitive_keys_are_masked_at_any_depth():
    value = {"a": [{"payload": "private", "password": 1, "name": "api"}], "API_KEY": "x"}
    expected = {"a": [{"payload": MASK, "password": MASK, "name": "api"}], "API_KEY": MASK}
    assert redact(value) == expected
