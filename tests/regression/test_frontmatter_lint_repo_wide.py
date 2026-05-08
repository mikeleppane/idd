"""Lint frontmatter across the WHOLE command + skill surface.

The `tools.lint_frontmatter` linter is normally invoked via Makefile against
curated file lists. New surfaces (e.g. P3 added `commands/amend-constitution.md`
and `skills/forge-constitution/SKILL.md`) shipped with frontmatter that failed
the linter's "Use when ..." rule because no test exercised the linter against
the discovered set. This regression closes that gap by globbing every
`commands/*.md` and `skills/*/SKILL.md` and asserting the linter passes on
each.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.lint_frontmatter import validate_file

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "frontmatter.schema.json"
COMMAND_FILES = sorted(REPO_ROOT.glob("commands/*.md"))
SKILL_FILES = sorted(REPO_ROOT.glob("skills/*/SKILL.md"))


@pytest.mark.parametrize("path", COMMAND_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_command_frontmatter_lints_clean(path: Path) -> None:
    errors = validate_file(path, SCHEMA_PATH)
    assert errors == [], "frontmatter lint failures:\n" + "\n".join(errors)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_skill_frontmatter_lints_clean(path: Path) -> None:
    errors = validate_file(path, SCHEMA_PATH)
    assert errors == [], "frontmatter lint failures:\n" + "\n".join(errors)


def test_lint_inputs_non_empty() -> None:
    """Guard against the test silently passing if the glob ever matches nothing."""
    assert COMMAND_FILES, "no commands/*.md files found — glob misconfigured?"
    assert SKILL_FILES, "no skills/*/SKILL.md files found — glob misconfigured?"
