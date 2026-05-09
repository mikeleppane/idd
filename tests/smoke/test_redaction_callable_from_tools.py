"""Smoke: ``tools.redaction`` is importable and the spec result type is
exposed end-to-end. Plain-text payload with the default config exercises
the no-op (default-deny) path; nothing is redacted, no fatals fire.
"""

from __future__ import annotations

from tools import redaction


def test_redaction_filter_callable_with_plain_text_payload() -> None:
    result = redaction.filter(redaction.PromptPayload(text="hello"))
    assert result.output_text == "hello"
    assert result.had_denials is False
    assert result.fatal_matches == ()
