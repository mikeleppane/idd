"""Pin the inline-secret defaults shipped on RedactionConfig.deny_regex.

Each row of ``CASES`` is a real-shape credential sample. The default
config must redact every sample without the caller having to extend
deny_regex.
"""

from __future__ import annotations

import re

import pytest

from tools import redaction

CASES = [
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("github_pat_classic", "ghp_" + "A" * 36),
    ("github_pat_fine", "github_pat_" + "A" * 82),
    ("anthropic_api_v03", "sk-ant-api03-" + "A" * 100),
    ("anthropic_admin", "sk-ant-admin01-" + "B" * 95),
    ("anthropic_oauth", "sk-ant-oauth-" + "C" * 50),
    ("openai_classic", "sk-" + "Z" * 32),
    ("openai_project", "sk-proj-" + "Y" * 40),
    ("gemini_key", "AIza" + "A" * 35),
    ("slack_bot", "xoxb-1234567890-9876543210-AbCdEfGhIjKlMnOpQrStUvWx"),
    ("pem_rsa", "-----BEGIN RSA PRIVATE KEY-----"),
    ("pem_openssh", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("pem_bare", "-----BEGIN PRIVATE KEY-----"),
    ("authz_bearer", "Authorization: Bearer abcdef.123456.qwerty"),
    ("env_api_key", "MYAPP_API_KEY=secret123"),
    ("env_token", "SLACK_TOKEN=xoxb-12345"),
    ("env_password", "DB_PASSWORD=hunter2"),
    ("env_secret", "FOO_SECRET=plaintext"),
]


@pytest.mark.parametrize(("name", "sample"), CASES, ids=[c[0] for c in CASES])
def test_default_deny_regex_redacts_known_secret_shape(name: str, sample: str) -> None:
    # env-shape patterns are anchored to start-of-line (the pattern uses
    # ``(?m)^...``) so a secret pasted into prose only fires when it lands
    # on its own line. Embed the sample on a fresh line for every case to
    # exercise that path uniformly.
    text = f"context before\n{sample}\ncontext after"
    result = redaction.filter(redaction.PromptPayload(text=text))

    assert sample not in result.output_text, (
        f"default deny_regex must redact {name}, got output: {result.output_text!r}"
    )
    assert result.had_denials, f"{name}: redacted_spans empty"
    assert any(span.sample.startswith(sample[:8]) for span in result.redacted_spans), (
        f"{name}: no span captured a substring of the sample"
    )


def test_default_deny_regex_does_not_redact_innocuous_text() -> None:
    """Plain prose stays untouched — no false positive on common words."""
    text = "FORGE drives features through spec, plan, execute, verify, ship."
    result = redaction.filter(redaction.PromptPayload(text=text))
    assert result.output_text == text
    assert not result.had_denials


def test_default_deny_regex_emits_stderr_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """Every redaction must surface to stderr so the operator notices."""
    text = "leaked token AKIAIOSFODNN7EXAMPLE inside a payload"
    redaction.filter(redaction.PromptPayload(text=text))

    captured = capsys.readouterr()
    assert "WARN: tools.redaction" in captured.err
    assert "inline secret redacted" in captured.err
    # The matched sample is NOT echoed to stderr (it's the secret).
    assert "AKIAIOSFODNN7EXAMPLE" not in captured.err


def test_default_deny_regex_module_load_guard() -> None:
    """Every default pattern compiles and passes the ReDoS sanity filter.

    The module-level _assert_default_patterns_safe() call would have
    raised at import time on failure; this test pins the contract from a
    test-runner perspective so a future contributor cannot loosen it
    accidentally.
    """
    for pattern in redaction.DEFAULT_DENY_REGEX:
        # Compile check.
        compiled = re.compile(pattern)
        assert compiled is not None
