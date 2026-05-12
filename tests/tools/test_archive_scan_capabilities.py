"""Tests for tools.archive.scan_existing_capabilities — capability scan (§5.3.5)."""

from __future__ import annotations

from pathlib import Path

from tools.archive import scan_existing_capabilities

# ---------------------------------------------------------------------------
# Happy path — sorted output
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_sorted_output(tmp_path: Path) -> None:
    """Subdirs with SPEC.md are returned sorted alphabetically."""
    specs = tmp_path / ".forge" / "specs"
    for slug in ("billing", "auth", "users"):
        (specs / slug).mkdir(parents=True)
        (specs / slug / "SPEC.md").touch()

    result = scan_existing_capabilities(tmp_path)

    assert result == ["auth", "billing", "users"]


# ---------------------------------------------------------------------------
# Empty .forge/specs/ directory
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_empty_specs_dir(tmp_path: Path) -> None:
    """An empty .forge/specs/ directory returns an empty list."""
    (tmp_path / ".forge" / "specs").mkdir(parents=True)

    result = scan_existing_capabilities(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# Subdirs without SPEC.md are skipped
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_subdirs_without_spec_md_skipped(tmp_path: Path) -> None:
    """A subdirectory without SPEC.md is treated as non-canonical and excluded."""
    specs = tmp_path / ".forge" / "specs"
    # in-progress has no SPEC.md — should be skipped
    (specs / "in-progress").mkdir(parents=True)
    # shipped has SPEC.md — should be included
    (specs / "shipped").mkdir(parents=True)
    (specs / "shipped" / "SPEC.md").touch()

    result = scan_existing_capabilities(tmp_path)

    assert result == ["shipped"]


# ---------------------------------------------------------------------------
# No .forge/ directory at all
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_no_forge_dir(tmp_path: Path) -> None:
    """When .forge/ does not exist, returns [] without raising."""
    result = scan_existing_capabilities(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# .forge/ exists but no specs/ subdir
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_forge_exists_no_specs_subdir(tmp_path: Path) -> None:
    """When .forge/ exists but .forge/specs/ does not, returns []."""
    (tmp_path / ".forge").mkdir()

    result = scan_existing_capabilities(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# Only top-level subdirs of .forge/specs/ are considered
# ---------------------------------------------------------------------------


def test_scan_existing_capabilities_nested_spec_md_not_promoted(tmp_path: Path) -> None:
    """A SPEC.md nested deeper than .forge/specs/<slug>/ does not promote bar."""
    specs = tmp_path / ".forge" / "specs"
    # foo is a valid top-level capability
    (specs / "foo").mkdir(parents=True)
    (specs / "foo" / "SPEC.md").touch()
    # stray sibling file alongside SPEC.md — should not affect listing
    (specs / "foo" / "PLAN.md").touch()
    # bar is a nested subdir under foo — its SPEC.md does NOT make it a capability
    (specs / "foo" / "bar").mkdir()
    (specs / "foo" / "bar" / "SPEC.md").touch()

    result = scan_existing_capabilities(tmp_path)

    assert result == ["foo"]
    assert "bar" not in result


def test_scan_existing_capabilities_coerces_string_repo_root(tmp_path: Path) -> None:
    """Boundary coercion: a string repo_root must not trip ``AttributeError``.

    Mirrors the pattern locked into ``tools.bdd_detect.detect`` — agent
    callers that pass a string for an annotated ``Path`` parameter must
    not crash four frames deep on ``str / ".forge" / ...``.
    """
    specs = tmp_path / ".forge" / "specs"
    (specs / "auth").mkdir(parents=True)
    (specs / "auth" / "SPEC.md").touch()

    result = scan_existing_capabilities(str(tmp_path))

    assert result == ["auth"]
