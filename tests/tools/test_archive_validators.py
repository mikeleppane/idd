"""Tests for archive.py private slug-validator error messages (deep-M2).

Verifies that _validate_change_id, _validate_capability, and _validate_feature_id
include the expected shape hint in the error message so callers understand
what a valid value looks like.
"""

from __future__ import annotations

import pytest

from tools.archive import (
    ArchiveError,
    _validate_capability,
    _validate_change_id,
    _validate_feature_id,
)


def test_validate_change_id_error_includes_expected_shape() -> None:
    with pytest.raises(ArchiveError, match="YYYY-MM-DD"):
        _validate_change_id("foo-bar")


def test_validate_change_id_error_includes_regex_hint() -> None:
    """Error message includes the expected shape hint for change ids."""
    with pytest.raises(ArchiveError, match=r"\^\[a-z0-9\]"):
        _validate_change_id("2026-05-08-UPPER")


def test_validate_capability_error_includes_regex_hint() -> None:
    with pytest.raises(ArchiveError, match=r"\^\[a-z0-9\]"):
        _validate_capability("FOO")


def test_validate_capability_valid_passes() -> None:
    """Valid capability slug does not raise."""
    _validate_capability("my-cap")


def test_validate_feature_id_error_includes_expected_shape() -> None:
    with pytest.raises(ArchiveError, match="YYYY-MM-DD"):
        _validate_feature_id("nope")


def test_validate_feature_id_error_includes_regex_hint() -> None:
    """Error message includes the regex for feature ids."""
    with pytest.raises(ArchiveError, match=r"\^\[a-z0-9\]"):
        _validate_feature_id("2026-05-08-UPPER")


def test_validate_change_id_valid_passes() -> None:
    """Valid change id does not raise."""
    _validate_change_id("2026-05-08-my-change")


def test_validate_feature_id_valid_passes() -> None:
    """Valid feature id does not raise."""
    _validate_feature_id("2026-05-08-my-feature")
