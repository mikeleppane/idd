"""Smoke-level assertion that all M2 templates ship with the plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

M2_TEMPLATES = [
    REPO_ROOT / "templates" / "feature" / "UNDERSTANDING.md",
    REPO_ROOT / "templates" / "feature" / "REVIEW.md",
    REPO_ROOT / "templates" / "capability" / "SPEC.md",
]


@pytest.mark.parametrize(
    "template", M2_TEMPLATES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_template_exists_and_has_frontmatter(template: Path) -> None:
    assert template.is_file(), f"missing template: {template}"
    text = template.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"template {template} missing YAML frontmatter"
    assert text.count("---\n") >= 2, f"template {template} frontmatter not closed"
