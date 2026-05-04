"""Tests for tools.lint_frontmatter — frontmatter validator for skills/commands."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import lint_frontmatter as lint


def test_parse_extracts_yaml_block_from_top(tmp_path: Path) -> None:
    md = tmp_path / "skill.md"
    md.write_text(
        "---\nname: idd-spec\ndescription: Author a feature SPEC.md. Use when starting a new feature.\n---\n\n# body\n",
        encoding="utf-8",
    )

    result = lint.parse_frontmatter(md)

    assert result == {
        "name": "idd-spec",
        "description": "Author a feature SPEC.md. Use when starting a new feature.",
    }


def test_parse_returns_none_when_no_frontmatter(tmp_path: Path) -> None:
    md = tmp_path / "no-fm.md"
    md.write_text("# just a heading\nno frontmatter here\n", encoding="utf-8")

    assert lint.parse_frontmatter(md) is None


def test_parse_raises_on_unclosed_block(tmp_path: Path) -> None:
    md = tmp_path / "broken.md"
    md.write_text("---\nname: idd-spec\n# missing closing fence\n", encoding="utf-8")

    with pytest.raises(lint.FrontmatterError, match="unclosed"):
        lint.parse_frontmatter(md)


def test_parse_raises_on_invalid_yaml(tmp_path: Path) -> None:
    md = tmp_path / "bad-yaml.md"
    md.write_text("---\nname: : :\n---\n", encoding="utf-8")

    with pytest.raises(lint.FrontmatterError, match="YAML"):
        lint.parse_frontmatter(md)


def test_validate_passes_for_valid_skill_with_use_when(
    tmp_path: Path, schemas_dir: Path
) -> None:
    md = tmp_path / "skills" / "idd-spec" / "SKILL.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "---\nname: idd-spec\ndescription: Author a feature SPEC.md following the IDD template. Use when the user asks to start a new IDD feature or refine an existing spec.\n---\n\nbody\n",
        encoding="utf-8",
    )

    errors = lint.validate_file(md, schemas_dir / "frontmatter.schema.json")

    assert errors == []


def test_validate_rejects_description_missing_use_when_clause(
    tmp_path: Path, schemas_dir: Path
) -> None:
    md = tmp_path / "skills" / "idd-spec" / "SKILL.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "---\nname: idd-spec\ndescription: Author a feature SPEC.md following the IDD template, period and that is all.\n---\n\nbody\n",
        encoding="utf-8",
    )

    errors = lint.validate_file(md, schemas_dir / "frontmatter.schema.json")

    assert any("Use when" in e for e in errors), errors


def test_validate_rejects_description_without_imperative_first_word(
    tmp_path: Path, schemas_dir: Path
) -> None:
    md = tmp_path / "skills" / "idd-spec" / "SKILL.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "---\nname: idd-spec\ndescription: this skill helps with specs eventually. Use when the user starts.\n---\n\nbody\n",
        encoding="utf-8",
    )

    errors = lint.validate_file(md, schemas_dir / "frontmatter.schema.json")

    assert any("imperative" in e.lower() for e in errors), errors


def test_validate_rejects_missing_required(
    tmp_path: Path, schemas_dir: Path
) -> None:
    md = tmp_path / "skill.md"
    md.write_text("---\nname: idd-spec\n---\n\nbody\n", encoding="utf-8")

    errors = lint.validate_file(md, schemas_dir / "frontmatter.schema.json")

    assert any("description" in e for e in errors)


def test_main_returns_zero_when_all_files_valid(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "skills" / "idd-spec" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\nname: idd-spec\ndescription: Author a feature SPEC.md following the IDD template. Use when starting a new IDD feature.\n---\n",
        encoding="utf-8",
    )

    rc = lint.main([
        "--schema", str(schemas_dir / "frontmatter.schema.json"),
        str(target),
    ])

    assert rc == 0


def test_main_returns_nonzero_when_any_file_invalid(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "broken.md"
    target.write_text("---\nname: BadName\n---\n", encoding="utf-8")

    rc = lint.main([
        "--schema", str(schemas_dir / "frontmatter.schema.json"),
        str(target),
    ])

    assert rc == 1
