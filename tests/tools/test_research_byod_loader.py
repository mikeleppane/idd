"""Tests for the BYOD (bring-your-own-docs) loader.

Covers happy-path read, paragraph-boundary truncation when the body
exceeds the rough token budget, UTC mtime + staleness boundary
behaviour, unreadable (non-UTF-8) handling, and missing-path raising.
The loader never spawns a subprocess and never inspects git metadata —
it reads the file directly via `pathlib`.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.research.byod_loader import ByodLoadError, LoadResult, load


def _set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_load_happy_path_returns_body_and_freshness(tmp_path: Path) -> None:
    doc = tmp_path / "httpx.md"
    doc.write_text("# httpx\n\nSome content.\n", encoding="utf-8")
    now = datetime.now(UTC)
    _set_mtime(doc, now)

    result = load(doc, now=now)

    assert isinstance(result, LoadResult)
    assert result.body == "# httpx\n\nSome content.\n"
    assert result.truncated is False
    assert result.error is None
    assert result.stale is False
    assert result.mtime.tzinfo is UTC


def test_load_truncates_at_paragraph_boundary(tmp_path: Path) -> None:
    doc = tmp_path / "big.md"
    para = "x" * 4000  # three paragraphs ~12,008 chars total with separators
    body = "\n\n".join([para, para, para, "tail-para"])
    doc.write_text(body, encoding="utf-8")

    result = load(doc, max_chars=8500)

    assert result.truncated is True
    # Truncated chunk must end at a paragraph boundary, not mid-word.
    assert result.body.endswith(para)
    assert "tail-para" not in result.body
    assert len(result.body) <= 8500


def test_load_truncates_to_empty_when_no_boundary_within_budget(
    tmp_path: Path,
) -> None:
    # Single paragraph longer than max_chars has no boundary to truncate at:
    # the loader returns an empty body but flags truncation.
    doc = tmp_path / "single.md"
    doc.write_text("z" * 5000, encoding="utf-8")

    result = load(doc, max_chars=1000)

    assert result.truncated is True
    assert result.body == ""


def test_load_staleness_boundary_at_threshold_is_fresh(tmp_path: Path) -> None:
    doc = tmp_path / "fresh.md"
    doc.write_text("body", encoding="utf-8")
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    boundary = now - timedelta(days=90)
    _set_mtime(doc, boundary)

    result = load(doc, stale_after_days=90, now=now)

    assert result.stale is False


def test_load_staleness_boundary_one_second_earlier_is_stale(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "old.md"
    doc.write_text("body", encoding="utf-8")
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    older = now - timedelta(days=90, seconds=1)
    _set_mtime(doc, older)

    result = load(doc, stale_after_days=90, now=now)

    assert result.stale is True


def test_load_unreadable_bytes_yield_error_marker(tmp_path: Path) -> None:
    doc = tmp_path / "binary.md"
    doc.write_bytes(b"\x80\x81\x82\xff")

    result = load(doc)

    assert result.error == "UNREADABLE"
    assert result.body == ""
    assert result.truncated is False


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ByodLoadError):
        load(tmp_path / "nope.md")


def test_load_default_now_uses_utc(tmp_path: Path) -> None:
    doc = tmp_path / "ts.md"
    doc.write_text("body", encoding="utf-8")
    # No `now` passed — loader must use UTC, not naive local time.
    result = load(doc)
    assert result.mtime.tzinfo is UTC
    # Newly-created file is not stale by default 90-day window.
    assert result.stale is False
