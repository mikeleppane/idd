"""Idempotency: two passes through tools.redaction return identical results."""

from __future__ import annotations

from pathlib import PurePosixPath

from tools import redaction


def test_redact_twice_is_idempotent() -> None:
    """A second pass over already-redacted output produces identical result.

    The opaque ``[REDACTED:N]`` marker MUST NOT itself match the user's
    ``deny_regex``; excluded files stay excluded; warning counts do not grow.
    """
    payload = redaction.PromptPayload(
        text="key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 done",
        files=(
            PurePosixPath("project/.env"),
            PurePosixPath("src/main.py"),
        ),
    )
    config = redaction.RedactionConfig(
        deny_regex=(r"sk-[A-Za-z0-9]{32}",),
        gitignore_patterns=("**/.env*",),
    )

    first = redaction.filter(payload, config)

    # Feed first.output_text + the same file inventory back through the filter.
    second_payload = redaction.PromptPayload(text=first.output_text, files=payload.files)
    second = redaction.filter(second_payload, config)

    assert second.output_text == first.output_text
    assert second.excluded_files == first.excluded_files
    assert second.redacted_spans == ()  # marker did not re-match
    assert second.fatal_matches == ()
    assert len(second.warnings) == len(first.warnings)
