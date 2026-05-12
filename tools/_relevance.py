"""Shared scoring + percentile + cap helper for Constitution articles and lessons.

The helper is generic over any item that exposes:
    - a string id
    - a level/severity string from a closed vocabulary
    - a body_words integer

The caller supplies:
    - a score function (item -> int) — per-domain
    - a level rule map (level -> bucket) — per-domain
    - a max_words cap — per-domain (1153 for articles, 600 for lessons)

The helper applies:
    1. Bucket-based percentile gate (each bucket has its own percentile cutoff).
    2. Cap-respecting trim by ascending score (always-kept items exempt).
    3. RelevanceError raise if always-kept items alone exceed the cap.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

Bucket = Literal["always_kept", "p25_gate", "median_gate"]


class RelevanceError(RuntimeError):
    """Raised when always-kept items alone exceed the word cap."""


@dataclass(frozen=True, kw_only=True)
class RelevanceRule[T]:
    """Per-domain configuration for :func:`score_and_trim`.

    ``level_bucket`` maps each value the item's level attribute can take to a
    :data:`Bucket` label. Items in ``always_kept`` bypass the percentile gate
    and the word cap. Items in ``p25_gate`` are dropped if their score is
    strictly below the 25th percentile of all scores. Items in ``median_gate``
    are dropped if their score is strictly below the median.

    ``score`` returns a non-negative relevance count; higher means more
    relevant. ``level_of`` / ``body_words_of`` / ``id_of`` are accessor
    callables (not getattr lookups) so callers can wrap dataclasses with
    different attribute names without renaming domain types.
    """

    score: Callable[[T], int]
    level_of: Callable[[T], str]
    body_words_of: Callable[[T], int]
    id_of: Callable[[T], str]
    level_bucket: dict[str, Bucket]
    max_words: int


def _percentile(values: list[int], pct: float) -> float:
    """Inclusive linear-interpolation percentile. Returns 0.0 for empty input.

    Production callers pass ``pct in {25, 50}``; the ``hi`` clamp at the end
    is defensive for the ``pct == 100`` boundary (``rank == len-1``) so the
    helper stays robust if a future caller probes the top percentile.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _sort_by_id_num[T](items: Iterable[T], *, id_of: Callable[[T], str]) -> list[T]:
    """Sort items by ``int(id[1:])`` — works for both A<n> and L<n> ids."""
    return sorted(items, key=lambda item: int(id_of(item)[1:]))


def score_and_trim[T](
    items: Iterable[T],
    *,
    rule: RelevanceRule[T],
) -> tuple[list[T], list[str]]:
    """Apply percentile gate + cap trim. Returns ``(kept, dropped_ids)``.

    Behavior contract (load-bearing — the Constitution byte-equal regression
    in ``tests/tools/test_constitution.py`` is the gate):

    1. Empty input -> ``([], [])``.
    2. Score each item once.
    3. Median + 25th percentile over the full score list.
    4. Per item: ``always_kept`` -> keep; ``p25_gate`` AND score < p25 ->
       drop; ``median_gate`` AND score < median -> drop; otherwise keep.
    5. If sum of body_words across kept exceeds ``max_words``, drop
       non-always-kept items by ascending score until under the cap.
       Always-kept items stay regardless of the cap pass.
    6. If always-kept items alone exceed ``max_words``, raise
       :class:`RelevanceError` naming the always-kept ids and the total.
    7. Returned ``kept`` is sorted by ``int(id_of(item)[1:])``; ``dropped``
       is sorted the same way (ids only).

    Args:
        items: Items to score and trim.
        rule: Per-domain configuration; see :class:`RelevanceRule`.

    Returns:
        Tuple of (kept_items, dropped_ids).

    Raises:
        RelevanceError: When always-kept items alone exceed ``max_words``.
    """
    item_list = list(items)
    if not item_list:
        return [], []

    scored: list[tuple[T, int]] = [(item, rule.score(item)) for item in item_list]
    score_by_id: dict[str, int] = {rule.id_of(item): s for item, s in scored}
    scores = [s for _, s in scored]
    median = _percentile(scores, 50)
    p25 = _percentile(scores, 25)

    kept: list[T] = []
    dropped: list[str] = []
    for item, score in scored:
        bucket = rule.level_bucket.get(rule.level_of(item))
        if bucket == "always_kept":
            kept.append(item)
        elif (bucket == "p25_gate" and score < p25) or (bucket == "median_gate" and score < median):
            dropped.append(rule.id_of(item))
        else:
            kept.append(item)

    cumulative = sum(rule.body_words_of(item) for item in kept)
    if cumulative > rule.max_words:
        # Sort so always-kept items rank last (highest priority); within each
        # group, ascending score so the lowest-scoring drop first.
        kept_with_score = sorted(
            ((item, score_by_id[rule.id_of(item)]) for item in kept),
            key=lambda pair: (
                rule.level_bucket.get(rule.level_of(pair[0])) == "always_kept",
                pair[1],
            ),
        )
        kept_after_cap: list[T] = []
        running = 0
        # Iterate descending so highest-priority items are added first.
        for item, _score in reversed(kept_with_score):
            item_words = rule.body_words_of(item)
            if running + item_words <= rule.max_words:
                kept_after_cap.append(item)
                running += item_words
            elif rule.level_bucket.get(rule.level_of(item)) == "always_kept":
                kept_after_cap.append(item)  # always-kept exempt from cap
                running += item_words
            else:
                dropped.append(rule.id_of(item))
        kept = _sort_by_id_num(kept_after_cap, id_of=rule.id_of)

    final_total = sum(rule.body_words_of(item) for item in kept)
    if final_total > rule.max_words:
        always_kept_ids = [
            rule.id_of(item)
            for item in kept
            if rule.level_bucket.get(rule.level_of(item)) == "always_kept"
        ]
        raise RelevanceError(
            f"always-kept items {always_kept_ids} exceed the {rule.max_words}-word "
            f"cap on their own ({final_total} words)"
        )

    return (
        _sort_by_id_num(kept, id_of=rule.id_of),
        sorted(set(dropped), key=lambda x: int(x[1:])),
    )
