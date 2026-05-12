"""Tests for tools.redaction — shared deny/allow filter (spec §5.3.11)."""

from __future__ import annotations

import dataclasses
from pathlib import PurePosixPath

import pytest

from tools import redaction


def test_empty_payload_returns_empty_result() -> None:
    """(a) Empty payload yields an empty RedactionResult with no findings."""
    result = redaction.filter(redaction.PromptPayload())

    assert result.excluded_files == ()
    assert result.redacted_spans == ()
    assert result.fatal_matches == ()
    assert result.warnings == ()
    assert result.output_text == ""
    assert result.had_denials is False


def test_payload_with_no_matches_passes_through() -> None:
    """(b) When nothing matches, text is unchanged and files are preserved."""
    payload = redaction.PromptPayload(
        text="hello world, no secrets here",
        files=(PurePosixPath("src/lib.py"), PurePosixPath("README.md")),
    )
    config = redaction.RedactionConfig(
        deny_globs=("**/.env*",),
        deny_regex=(r"sk-[A-Za-z0-9]{32}",),
        fatal_regex=(r"-----BEGIN PRIVATE KEY-----",),
    )

    result = redaction.filter(payload, config)

    assert result.output_text == "hello world, no secrets here"
    assert result.excluded_files == ()
    assert result.redacted_spans == ()
    assert result.fatal_matches == ()
    assert result.warnings == ()


def test_default_deny_globs_excludes_dotenv() -> None:
    """(c) Default deny_globs (**/.env*) excludes project/.env."""
    payload = redaction.PromptPayload(files=(PurePosixPath("project/.env"),))

    result = redaction.filter(payload)

    assert result.excluded_files == (PurePosixPath("project/.env"),)
    assert result.had_denials is True


def test_default_deny_globs_excludes_aws_credentials() -> None:
    """(d) Default deny_globs match nested/aws/credentials via two patterns.

    Both ``**/*credentials*`` and ``**/.aws/**`` are spec defaults; the file is
    excluded once even though two patterns hit it.
    """
    payload = redaction.PromptPayload(files=(PurePosixPath("nested/.aws/credentials"),))

    result = redaction.filter(payload)

    assert result.excluded_files == (PurePosixPath("nested/.aws/credentials"),)


def test_deny_regex_replaces_with_opaque_marker() -> None:
    """(e) deny_regex matches replaced with [REDACTED:<idx>], pattern not echoed."""
    payload = redaction.PromptPayload(text="api token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 trailing")
    config = redaction.RedactionConfig(deny_regex=(r"sk-[A-Za-z0-9]{32}",))

    result = redaction.filter(payload, config)

    assert "[REDACTED:0]" in result.output_text
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in result.output_text
    # The user-supplied pattern itself MUST NOT leak into output_text.
    assert "sk-[A-Za-z0-9]" not in result.output_text
    assert len(result.redacted_spans) == 1
    span = result.redacted_spans[0]
    assert span.rule_id == r"sk-[A-Za-z0-9]{32}"
    assert span.sample == "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def test_fatal_regex_does_not_replace_output_text() -> None:
    """(f) fatal_regex matches recorded but output_text retained for diagnostic.

    Overrides deny_regex to an empty tuple so the default inline-secret
    patterns (which would redact the PEM marker) do not contend with the
    fatal-regex contract under test.
    """
    text = "blob -----BEGIN PRIVATE KEY-----\nMIIE..."
    payload = redaction.PromptPayload(text=text)
    config = redaction.RedactionConfig(
        deny_regex=(),
        fatal_regex=(r"-----BEGIN PRIVATE KEY-----",),
    )

    result = redaction.filter(payload, config)

    assert result.output_text == text
    assert len(result.fatal_matches) == 1
    fatal = result.fatal_matches[0]
    assert fatal.rule_id == r"-----BEGIN PRIVATE KEY-----"
    assert fatal.sample == "-----BEGIN PRIVATE KEY-----"
    assert fatal.file is None


def test_allow_globs_overrides_deny_globs() -> None:
    """(g) allow_globs rescues a file that would otherwise hit a deny_glob."""
    payload = redaction.PromptPayload(files=(PurePosixPath("tests/fixtures/.env.example"),))
    config = redaction.RedactionConfig(allow_globs=("tests/fixtures/.env.example",))

    result = redaction.filter(payload, config)

    assert result.excluded_files == ()
    assert result.had_denials is False


def test_gitignore_overlap_with_deny_globs_emits_one_warning() -> None:
    """(h) Same file matched by both gitignore and deny_globs is excluded once + 1 warning."""
    payload = redaction.PromptPayload(files=(PurePosixPath("project/.env"),))
    config = redaction.RedactionConfig(
        gitignore_patterns=("**/.env*",),
    )

    result = redaction.filter(payload, config)

    assert result.excluded_files == (PurePosixPath("project/.env"),)
    assert len(result.warnings) == 1


def test_allow_globs_rescues_file_dropped_by_both_deny_and_gitignore() -> None:
    """(i) allow_globs wins even when both deny_globs AND gitignore would drop the file."""
    payload = redaction.PromptPayload(files=(PurePosixPath("project/.env"),))
    config = redaction.RedactionConfig(
        gitignore_patterns=("**/.env*",),
        allow_globs=("project/.env",),
    )

    result = redaction.filter(payload, config)

    assert result.excluded_files == ()
    assert result.warnings == ()


def test_had_denials_property_semantics() -> None:
    """(j) had_denials is True only for excluded_files / redacted_spans, NOT fatal-only."""
    # No findings → False.
    assert redaction.RedactionResult().had_denials is False

    # excluded only → True.
    res_excl = redaction.RedactionResult(excluded_files=(PurePosixPath("x"),))
    assert res_excl.had_denials is True

    # span only → True.
    span = redaction.Span(start=0, end=1, rule_id="r", sample="x")
    res_span = redaction.RedactionResult(redacted_spans=(span,))
    assert res_span.had_denials is True

    # fatal-only → False (documented nuance: caller refuses on fatal_matches separately).
    fatal = redaction.Match(rule_id="r", sample="x", file=None)
    res_fatal = redaction.RedactionResult(fatal_matches=(fatal,))
    assert res_fatal.had_denials is False


def test_span_offsets_are_into_original_text() -> None:
    """(k) Span.start/end are offsets in the ORIGINAL payload.text, pre-replacement."""
    text = "lead sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA tail sk-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB end"
    payload = redaction.PromptPayload(text=text)
    config = redaction.RedactionConfig(deny_regex=(r"sk-[A-Za-z0-9]{32}",))

    result = redaction.filter(payload, config)

    assert len(result.redacted_spans) == 2
    # Spans must reference original-text offsets, not output_text offsets.
    for span in result.redacted_spans:
        assert text[span.start : span.end] == span.sample
    # First match starts at offset 5 in the original text.
    assert result.redacted_spans[0].start == 5
    assert result.redacted_spans[0].end == 5 + len("sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def test_redaction_result_is_frozen_and_tuple_typed() -> None:
    """(l) RedactionResult dataclass is frozen and uses tuple-typed collections."""
    result = redaction.RedactionResult()

    assert dataclasses.is_dataclass(result)
    # Frozen → mutation raises.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.output_text = "mutated"  # type: ignore[misc]

    # Tuple-typed defenses (immutable) on every collection field.
    assert isinstance(result.excluded_files, tuple)
    assert isinstance(result.redacted_spans, tuple)
    assert isinstance(result.fatal_matches, tuple)
    assert isinstance(result.warnings, tuple)

    # Same property for the input payload + config dataclasses.
    payload = redaction.PromptPayload()
    config = redaction.RedactionConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.text = "mutated"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.deny_globs = ()  # type: ignore[misc]
