"""Tests for tools.archive.slug_from_idea — D-6 slug semantics."""

from __future__ import annotations

import pytest

from tools.archive import ArchiveError, slug_from_idea

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_slug_from_idea_ascii_happy_path() -> None:
    assert slug_from_idea("Add user login flow") == "add-user-login-flow"


# ---------------------------------------------------------------------------
# Unicode / multilingual
# ---------------------------------------------------------------------------


def test_slug_from_idea_umlaut_input_normalizes_via_nfkd() -> None:
    # M8: Unicode normalization via NFKD + ascii-ignore preserves the
    # non-English speller's intent. ``ö → o`` (NFKD decomposes to o +
    # combining diaeresis, the diaeresis is stripped). ``ß`` does not
    # decompose under NFKD and is dropped entirely so "Größe" → "grosse"
    # but "größe" stays at "grosse" (the trailing ß is dropped); use
    # the umlaut-only test to assert the diaeresis path.
    assert slug_from_idea("Über Käufer") == "uber-kaufer"


def test_slug_from_idea_accented_latin_normalizes_to_ascii() -> None:
    # M8: accented Latin survives NFKD: ``café`` → ``cafe``.
    assert slug_from_idea("café au lait") == "cafe-au-lait"


def test_slug_from_idea_cjk_input_raises_archive_error() -> None:
    # M8: NFKD of CJK characters does not decompose them to ASCII so
    # they remain stripped by the existing ``[^a-z0-9 ]`` cleanup. Empty
    # slug still raises the empty-error path.
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("日本語入力")
    assert "日本語入力" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Stopwords only / empty
# ---------------------------------------------------------------------------


def test_slug_from_idea_only_stopwords_raises_archive_error() -> None:
    """L2: input had tokens but all were filtered as stopwords/too-short.
    The error message must differentiate from the empty-input path."""
    text = "the of a in"
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea(text)
    msg = str(exc_info.value)
    assert text in msg
    assert "all tokens filtered as stopwords or too short" in msg, (
        "L2: stopwords-only input must surface the differentiated error"
    )


def test_slug_from_idea_empty_string_raises_archive_error() -> None:
    """L2: actually-empty input keeps the legacy 'is empty:' phrasing
    so callers grepping for either message stay backward-compatible.
    """
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("")
    msg = str(exc_info.value)
    assert msg.startswith("slug computed from idea is empty:")
    # The 'tokens filtered' diagnostic must NOT appear when input was actually
    # empty — that wording is reserved for the all-tokens-filtered path.
    assert "all tokens filtered" not in msg


# ---------------------------------------------------------------------------
# Short tokens (1-char and 2-char survivors) — both must raise
# ---------------------------------------------------------------------------


def test_slug_from_idea_single_char_token_raises_archive_error() -> None:
    # Single-char token is dropped at step 5; result is empty.
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("x")
    assert "x" in str(exc_info.value)


def test_slug_from_idea_two_char_survivor_raises_archive_error() -> None:
    # "AI" produces slug "ai" (2 chars); does not match the 3-char minimum.
    # Both the computed slug and the original input must appear in the message.
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("AI")
    msg = str(exc_info.value)
    assert "ai" in msg
    assert "AI" in msg


# ---------------------------------------------------------------------------
# All-distinct content words ≤ max_words
# ---------------------------------------------------------------------------


def test_slug_from_idea_all_distinct_words_joins_all() -> None:
    assert slug_from_idea("alpha beta gamma") == "alpha-beta-gamma"


# ---------------------------------------------------------------------------
# Duplicate tokens — preserve insertion order, deduplicate
# ---------------------------------------------------------------------------


def test_slug_from_idea_duplicate_tokens_are_deduplicated_preserving_order() -> None:
    assert slug_from_idea("foo bar foo") == "foo-bar"


# ---------------------------------------------------------------------------
# Stopwords interleaved
# ---------------------------------------------------------------------------


def test_slug_from_idea_stopwords_interleaved_are_dropped() -> None:
    assert slug_from_idea("the alpha and beta with gamma") == "alpha-beta-gamma"


# ---------------------------------------------------------------------------
# Mixed punctuation
# ---------------------------------------------------------------------------


def test_slug_from_idea_mixed_punctuation_handled() -> None:
    # Colon and exclamation mark are replaced with spaces; hyphen is also
    # replaced with space (step 2 replaces all [^a-z0-9 ]).
    # Tokens: feature, flag, rollout, v2.
    result = slug_from_idea("feature-flag: rollout v2!")
    assert result == "feature-flag-rollout-v2"


# ---------------------------------------------------------------------------
# Hyphen in input — treated as punctuation, becomes token separator
# ---------------------------------------------------------------------------


def test_slug_from_idea_hyphen_in_input_becomes_separator() -> None:
    # "foo-bar baz" splits into three tokens: foo, bar, baz.
    assert slug_from_idea("foo-bar baz") == "foo-bar-baz"


# ---------------------------------------------------------------------------
# max_words override
# ---------------------------------------------------------------------------


def test_slug_from_idea_max_words_override_caps_token_count() -> None:
    result = slug_from_idea("alpha beta gamma delta epsilon zeta", max_words=2)
    assert result == "alpha-beta"


def test_slug_from_idea_max_words_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match=r"max_words must be >= 1, got 0"):
        slug_from_idea("alpha beta gamma", max_words=0)


def test_slug_from_idea_max_words_negative_raises_value_error() -> None:
    with pytest.raises(ValueError, match=r"max_words must be >= 1, got -1"):
        slug_from_idea("alpha beta gamma", max_words=-1)


# ---------------------------------------------------------------------------
# Long input — capped at default 5
# ---------------------------------------------------------------------------


def test_slug_from_idea_long_input_capped_at_five_words() -> None:
    result = slug_from_idea("alpha beta gamma delta epsilon zeta eta theta")
    assert result == "alpha-beta-gamma-delta-epsilon"


# ---------------------------------------------------------------------------
# Error-message provenance — offending input appears verbatim
# ---------------------------------------------------------------------------


def test_slug_from_idea_error_message_contains_verbatim_input_for_empty() -> None:
    text = "   "  # whitespace only; token list is empty after split
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea(text)
    assert text in str(exc_info.value)


def test_slug_from_idea_error_message_contains_verbatim_input_for_stopwords_only() -> None:
    text = "for the and"
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea(text)
    assert text in str(exc_info.value)
