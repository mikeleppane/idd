"""Overlap-handling lock for tools.redaction.filter.

Two ``deny_regex`` patterns whose matches overlap on the same substring must
emit a single ``[REDACTED:<idx>]`` marker covering the union of the ranges,
not one marker per pattern. Without this, right-to-left replacement using
original-text offsets corrupts ``output_text`` (a later match's tail or
trailing context can survive an earlier match's replacement, because the
earlier slice references a string that has already been mutated).

``redacted_spans`` retains every individual match for caller-side audit;
markers reference the merged interval set.
"""

from __future__ import annotations

from tools import redaction


def test_partial_overlap_emits_single_marker_and_preserves_trailing_text() -> None:
    """Reviewer reproducer: two deny_regex patterns overlap on the same
    secret. Pre-fix output truncated trailing context; post-fix retains it.
    """
    text = "token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 end"
    config = redaction.RedactionConfig(
        deny_regex=(r"sk-[A-Za-z0-9]{32}", r"[A-Za-z0-9]{32}"),
    )

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    assert result.output_text == "token [REDACTED:0] end"
    # Both individual matches are still recorded in the audit trail.
    assert len(result.redacted_spans) == 2


def test_partial_overlap_with_left_anchored_match_does_not_corrupt_output() -> None:
    """Inverse-tail overlap: pattern A starts earlier and extends into
    pattern B's range. Pre-fix output had a stray ``]`` from the clobbered
    marker; post-fix is clean.
    """
    text = "API_KEY=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 next"
    config = redaction.RedactionConfig(
        deny_regex=(
            r"sk-[A-Za-z0-9]{32}",
            r"API_KEY=sk-[A-Za-z0-9]{8}",
        ),
    )

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    assert result.output_text == "[REDACTED:0] next"
    # No stray closing bracket; user-supplied patterns must not echo into output.
    assert "]]" not in result.output_text
    assert "API_KEY=" not in result.output_text
    assert len(result.redacted_spans) == 2


def test_fully_contained_match_collapses_into_outer_match() -> None:
    """Inner match is wholly contained by an outer match — one merged marker."""
    text = "lead OUTER-inner-OUTER tail"
    config = redaction.RedactionConfig(
        deny_regex=(r"OUTER-inner-OUTER", r"inner"),
    )

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    assert result.output_text == "lead [REDACTED:0] tail"
    assert len(result.redacted_spans) == 2


def test_three_patterns_chain_overlap_collapse_into_single_marker() -> None:
    """Three patterns form a transitive overlap chain; all collapse into one."""
    text = "ABCDEFGHIJ"
    config = redaction.RedactionConfig(
        deny_regex=(r"ABCDE", r"CDEFG", r"EFGHI"),
    )

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    # Merged interval covers offsets 0..9 (A through I); the trailing 'J' is
    # not matched by any pattern and must survive.
    assert result.output_text == "[REDACTED:0]J"
    assert len(result.redacted_spans) == 3


def test_disjoint_matches_still_emit_distinct_markers() -> None:
    """Sanity: when ranges do not overlap, each match gets its own marker."""
    text = "sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA gap sk-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    config = redaction.RedactionConfig(deny_regex=(r"sk-[A-Za-z0-9]{32}",))

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    assert result.output_text == "[REDACTED:0] gap [REDACTED:1]"
    assert len(result.redacted_spans) == 2


def test_marker_index_is_stable_under_overlap_collapse() -> None:
    """Two distinct overlap groups: marker indices are 0 and 1, in order."""
    text = "OUTER-inner-OUTER mid SECOND-inside-SECOND"
    config = redaction.RedactionConfig(
        deny_regex=(
            r"OUTER-inner-OUTER",
            r"inner",
            r"SECOND-inside-SECOND",
            r"inside",
        ),
    )

    result = redaction.filter(redaction.PromptPayload(text=text), config)

    assert result.output_text == "[REDACTED:0] mid [REDACTED:1]"
    # Four individual matches recorded across two merged groups.
    assert len(result.redacted_spans) == 4
