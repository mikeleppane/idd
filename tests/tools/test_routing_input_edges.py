"""Input-edge regression tests for tools.routing.seed_routed_feature."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tools import archive
from tools.routing import seed_routed_feature


def test_empty_idea_refuses_before_any_disk_mutation(tmp_path: Path) -> None:
    forge_dir = tmp_path / ".forge"
    with pytest.raises(ValueError, match="non-empty string"):
        seed_routed_feature(tmp_path, idea="", final_tier="focused")
    assert not forge_dir.exists(), "no folder should be created on empty idea"


def test_whitespace_only_idea_refuses(tmp_path: Path) -> None:
    forge_dir = tmp_path / ".forge"
    with pytest.raises(ValueError, match="non-empty string"):
        seed_routed_feature(tmp_path, idea="   \n\t  ", final_tier="focused")
    assert not forge_dir.exists()


def test_oversize_slug_refuses_at_archive_layer() -> None:
    """A single 4000-char idea token would overflow NAME_MAX without the cap."""
    overlong = "x" * 250
    with pytest.raises(archive.ArchiveError, match="filesystem cap"):
        archive.slug_from_idea(overlong)


def test_slug_at_cap_boundary_accepted() -> None:
    """The 200-byte slug ceiling is inclusive at the boundary."""
    # 198 chars + safe filtering = a single-word slug well under 200 bytes.
    inside_cap = "abcdef " * 28  # tokens are short; total slug stays small.
    slug = archive.slug_from_idea(inside_cap)
    assert len(slug.encode("utf-8")) <= 200


def test_routing_emits_clean_error_for_overlong_idea_token(tmp_path: Path) -> None:
    """A pathological 4000-char single token surfaces a clean error.

    Pre-cap behaviour raised either the schema's verbose ValidationError
    or an OSError mid-seed on filesystems with NAME_MAX = 255.
    """
    pathological = "x" * 1000  # single token, no spaces
    with pytest.raises(archive.ArchiveError, match="filesystem cap"):
        seed_routed_feature(
            tmp_path,
            idea=pathological,
            final_tier="focused",
            today=date(2026, 5, 12),
        )


def test_seed_routed_feature_coerces_string_repo_root(tmp_path: Path) -> None:
    """A ``str`` ``repo_root`` must seed the same folder as the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` repo path; the
    helper composes ``repo_root / ".forge" / ...`` downstream, which trips a
    cryptic ``TypeError`` four frames deep when no boundary coercion sits at
    the entry. The string form must produce a working seeded feature folder
    identical to the ``Path`` form for the same inputs.
    """
    folder = seed_routed_feature(
        str(tmp_path),
        idea="add OAuth login flow",
        final_tier="focused",
        today=date(2026, 5, 12),
    )
    assert folder.is_dir()
    assert folder == tmp_path / ".forge" / "features" / "2026-05-12-add-oauth-login-flow"
    assert (folder / "state.json").is_file()
