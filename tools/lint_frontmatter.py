"""Lint markdown frontmatter for IDD skills and commands."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml


class FrontmatterError(RuntimeError):
    """Raised when frontmatter cannot be parsed or fails schema validation."""


_FENCE = "---"


def parse_frontmatter(path: Path) -> dict[str, Any] | None:
    """Parse the YAML frontmatter at the top of a markdown file.

    Args:
        path: Path to the markdown file.

    Returns:
        Parsed frontmatter dict, or None if the file has no frontmatter block.

    Raises:
        FrontmatterError: Block opened but never closed, YAML invalid, or top-level
            value is not a mapping.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if not lines or lines[0].strip() != _FENCE:
        return None

    body: list[str] = []
    closed = False
    for line in lines[1:]:
        if line.strip() == _FENCE:
            closed = True
            break
        body.append(line)

    if not closed:
        raise FrontmatterError(f"{path}: unclosed frontmatter block (missing closing '---')")

    try:
        parsed = yaml.safe_load("\n".join(body))
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"{path}: invalid YAML in frontmatter: {exc}") from exc

    if not isinstance(parsed, dict):
        raise FrontmatterError(
            f"{path}: frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )

    return parsed


_IMPERATIVE_BLOCKLIST = frozenset({
    "this", "the", "a", "an", "skill", "command", "it",
    "helps", "allows", "enables", "lets", "permits",
})
_USE_WHEN_PATTERN = re.compile(r"\bUse (when|after|whenever|before|during)\b", re.IGNORECASE)


def _description_quality_errors(path: Path, description: str) -> list[str]:
    """Return human-readable errors for description quality violations."""
    errors: list[str] = []
    first_word = description.split(maxsplit=1)[0].rstrip(",.;:").lower() if description else ""
    if first_word in _IMPERATIVE_BLOCKLIST or not first_word:
        errors.append(
            f"{path}: description must start with an imperative verb (e.g. 'Author', 'Run', "
            f"'Refuse'); got '{first_word}'"
        )
    if not _USE_WHEN_PATTERN.search(description):
        errors.append(
            f"{path}: description must contain a 'Use when ...' clause to enable ambient triggering"
        )
    return errors


def validate_file(path: Path, schema_path: Path) -> list[str]:
    """Validate a single markdown file's frontmatter against schema + quality bar.

    Args:
        path: Markdown file to lint.
        schema_path: Path to the frontmatter JSON Schema.

    Returns:
        List of human-readable error strings; empty list means valid.
    """
    try:
        fm = parse_frontmatter(path)
    except FrontmatterError as exc:
        return [str(exc)]

    if fm is None:
        return [f"{path}: missing frontmatter block"]

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{path}: {err.message}"
        for err in sorted(validator.iter_errors(fm), key=lambda e: list(e.path))
    ]

    description = fm.get("description")
    if isinstance(description, str):
        errors.extend(_description_quality_errors(path, description))

    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Lint each path; return 0 when all valid, 1 otherwise.

    Args:
        argv: Optional argv override (defaults to sys.argv).

    Returns:
        Exit code: 0 on success, 1 on any validation failure.
    """
    parser = argparse.ArgumentParser(description="Lint IDD skill/command frontmatter.")
    parser.add_argument(
        "--schema", required=True, type=Path,
        help="Path to frontmatter.schema.json.",
    )
    parser.add_argument(
        "paths", nargs="+", type=Path,
        help="Markdown files to lint.",
    )
    args = parser.parse_args(argv)

    rc = 0
    for path in args.paths:
        errors = validate_file(path, args.schema)
        for err in errors:
            print(err, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
