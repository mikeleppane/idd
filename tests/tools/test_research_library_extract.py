"""Tests for the canonical library-name normalization helpers.

Covers `normalize` (lowercase + hyphen-to-underscore) and `dedup`
(order-preserving deduplication after normalization). These helpers
are the canonical bridge between manifest-declared deps, import-scan
results, and BYOD filenames — drift here would silently mismatch
otherwise-equivalent names.
"""

from collections.abc import Iterator

import pytest

from tools.research.library_extract import dedup, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hyphen-Name", "hyphen_name"),
        ("snake_case", "snake_case"),
        ("Mixed-Case_Name", "mixed_case_name"),
        ("UPPER", "upper"),
        ("already_normal", "already_normal"),
        ("multi-hyphen-name", "multi_hyphen_name"),
    ],
)
def test_normalize_canonicalises(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_idempotent() -> None:
    once = normalize("My-Library_Name")
    twice = normalize(once)
    assert once == twice == "my_library_name"


def test_dedup_preserves_first_seen_order() -> None:
    assert dedup(["Httpx", "pytest", "httpx", "PyTest", "rich"]) == [
        "httpx",
        "pytest",
        "rich",
    ]


def test_dedup_normalizes_before_compare() -> None:
    # Hyphenated and underscored forms must collapse to one canonical entry.
    assert dedup(["my-lib", "my_lib", "MY-LIB"]) == ["my_lib"]


def test_dedup_empty_iterable_returns_empty_list() -> None:
    assert dedup([]) == []


def test_dedup_accepts_generator() -> None:
    def _gen() -> Iterator[str]:
        yield "Alpha"
        yield "beta"
        yield "ALPHA"

    assert dedup(_gen()) == ["alpha", "beta"]
