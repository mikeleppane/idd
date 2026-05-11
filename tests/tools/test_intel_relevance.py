"""Tests for the shared percentile + cap helper in tools._relevance.

The helper is the load-bearing piece behind both ``tools.constitution.filter_articles``
and ``tools.intel.lessons.load_and_filter``. These tests pin the contract
the Constitution byte-equal regression relies on (see
``tests/tools/test_constitution.py``), plus the boundary cases lessons
needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tools._relevance import RelevanceError, RelevanceRule, score_and_trim


@dataclass(frozen=True, kw_only=True)
class _Item:
    """Minimal stand-in for both Article and Lesson in tests."""

    id: str
    level: str
    body_words: int
    score: int


def _make_rule(
    *,
    max_words: int = 10_000,
    level_bucket: dict[str, str] | None = None,
) -> RelevanceRule[_Item]:
    return RelevanceRule(
        score=lambda item: item.score,
        level_of=lambda item: item.level,
        body_words_of=lambda item: item.body_words,
        id_of=lambda item: item.id,
        level_bucket=level_bucket  # type: ignore[arg-type]
        or {"CRITICAL": "always_kept", "HIGH": "p25_gate", "MEDIUM": "median_gate"},
        max_words=max_words,
    )


def test_score_and_trim_empty_input_returns_empty_pair() -> None:
    items: list[_Item] = []
    kept, dropped = score_and_trim(items, rule=_make_rule())
    assert kept == []
    assert dropped == []


def test_score_and_trim_keeps_all_always_kept_items() -> None:
    items = [
        _Item(id="A1", level="CRITICAL", body_words=10, score=0),
        _Item(id="A2", level="CRITICAL", body_words=10, score=0),
        _Item(id="A3", level="CRITICAL", body_words=10, score=0),
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule())
    assert [item.id for item in kept] == ["A1", "A2", "A3"]
    assert dropped == []


def test_score_and_trim_drops_median_gate_items_below_median() -> None:
    items = [
        _Item(id="A1", level="CRITICAL", body_words=10, score=10),
        _Item(id="A2", level="MEDIUM", body_words=10, score=5),
        _Item(id="A3", level="MEDIUM", body_words=10, score=0),  # below median
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule())
    kept_ids = {item.id for item in kept}
    assert "A1" in kept_ids
    assert "A2" in kept_ids
    assert "A3" not in kept_ids
    assert dropped == ["A3"]


def test_score_and_trim_drops_p25_gate_items_below_p25() -> None:
    items = [
        _Item(id="A1", level="HIGH", body_words=10, score=10),
        _Item(id="A2", level="HIGH", body_words=10, score=8),
        _Item(id="A3", level="HIGH", body_words=10, score=6),
        _Item(id="A4", level="HIGH", body_words=10, score=0),  # below p25
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule())
    kept_ids = {item.id for item in kept}
    assert "A4" not in kept_ids
    assert "A1" in kept_ids and "A2" in kept_ids and "A3" in kept_ids
    assert dropped == ["A4"]


def test_score_and_trim_caps_by_ascending_score_when_over_cap() -> None:
    items = [
        _Item(id="A1", level="HIGH", body_words=300, score=1),  # lowest score
        _Item(id="A2", level="HIGH", body_words=300, score=5),
        _Item(id="A3", level="HIGH", body_words=300, score=9),
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule(max_words=600))
    kept_ids = [item.id for item in kept]
    assert sum(item.body_words for item in kept) <= 600
    assert "A1" in dropped
    assert "A2" in kept_ids and "A3" in kept_ids


def test_score_and_trim_raises_when_always_kept_alone_exceed_cap() -> None:
    items = [
        _Item(id="A1", level="CRITICAL", body_words=400, score=0),
        _Item(id="A2", level="CRITICAL", body_words=400, score=0),
    ]
    with pytest.raises(RelevanceError, match=r"always-kept items .* exceed"):
        score_and_trim(items, rule=_make_rule(max_words=600))


def test_score_and_trim_returns_kept_sorted_by_numeric_id() -> None:
    items = [
        _Item(id="A11", level="CRITICAL", body_words=10, score=0),
        _Item(id="A2", level="CRITICAL", body_words=10, score=0),
        _Item(id="A1", level="CRITICAL", body_words=10, score=0),
    ]
    kept, _dropped = score_and_trim(items, rule=_make_rule())
    assert [item.id for item in kept] == ["A1", "A2", "A11"]


def test_score_and_trim_returns_dropped_sorted_by_numeric_id() -> None:
    # Median of scores [3,3,3,3,0,0,0,0,10] is 3; MEDIUM items with score < 3
    # drop. Place them in shuffled id order so the test exercises the sort.
    items = [
        _Item(id="A1", level="CRITICAL", body_words=10, score=10),
        _Item(id="A11", level="MEDIUM", body_words=10, score=0),
        _Item(id="A2", level="MEDIUM", body_words=10, score=0),
        _Item(id="A12", level="MEDIUM", body_words=10, score=0),
        _Item(id="A3", level="MEDIUM", body_words=10, score=0),
        _Item(id="A4", level="MEDIUM", body_words=10, score=3),
        _Item(id="A5", level="MEDIUM", body_words=10, score=3),
        _Item(id="A6", level="MEDIUM", body_words=10, score=3),
        _Item(id="A7", level="MEDIUM", body_words=10, score=3),
    ]
    _kept, dropped = score_and_trim(items, rule=_make_rule())
    assert dropped == ["A2", "A3", "A11", "A12"]


def test_score_and_trim_always_kept_survives_cap_pass_even_when_low_priority() -> None:
    items = [
        _Item(id="A1", level="CRITICAL", body_words=400, score=0),
        _Item(id="A2", level="HIGH", body_words=400, score=10),
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule(max_words=500))
    kept_ids = {item.id for item in kept}
    # A1 (CRITICAL) is exempt from the cap and stays; A2 must drop because
    # together they exceed 500 words.
    assert "A1" in kept_ids
    assert "A2" not in kept_ids
    assert dropped == ["A2"]


def test_score_and_trim_matches_constitution_percentile_behavior() -> None:
    """Cross-check the shared helper against the Constitution semantics
    on identical inputs — the original ``filter_articles`` used the strict
    ``<`` form for both percentile gates, and the helper must mirror it.
    """
    items = [
        _Item(id="A1", level="CRITICAL", body_words=10, score=0),
        _Item(id="A2", level="HIGH", body_words=10, score=0),  # p25=0, score=0 -> kept
        _Item(id="A3", level="MEDIUM", body_words=10, score=0),  # median=0, score=0 -> kept
    ]
    kept, dropped = score_and_trim(items, rule=_make_rule())
    assert {item.id for item in kept} == {"A1", "A2", "A3"}
    assert dropped == []
