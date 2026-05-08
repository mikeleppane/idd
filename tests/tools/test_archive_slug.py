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


def test_slug_from_idea_umlaut_input_is_deterministic() -> None:
    # Non-ASCII characters (ö, ü, ä, ß) are stripped; surviving ASCII
    # fragments form the slug: "gr" from "größe", "ufer" from "käufer".
    # "für" contributes only 1-char fragments ("f", "r") that are dropped.
    result = slug_from_idea("Größe für Käufer")
    assert result == "gr-ufer"


def test_slug_from_idea_cjk_input_raises_archive_error() -> None:
    # All CJK characters are outside [a-z0-9 ], stripped entirely; empty slug.
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("日本語入力")
    assert "日本語入力" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Stopwords only / empty
# ---------------------------------------------------------------------------


def test_slug_from_idea_only_stopwords_raises_archive_error() -> None:
    text = "the of a in"
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea(text)
    assert text in str(exc_info.value)


def test_slug_from_idea_empty_string_raises_archive_error() -> None:
    with pytest.raises(ArchiveError) as exc_info:
        slug_from_idea("")
    assert "" in str(exc_info.value)


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
