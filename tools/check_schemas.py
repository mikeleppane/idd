"""Validate that every shipped JSON Schema is itself a valid Draft 2020-12 schema.

Also validates each shipped template's frontmatter against its matching schema:
- templates/feature/state.json   → state.schema.json
- templates/feature/SPEC.md      → spec-frontmatter.schema.json
- templates/feature/PLAN.md      → plan-frontmatter.schema.json
- templates/feature/UNDERSTANDING.md → understanding-frontmatter.schema.json
- templates/feature/REVIEW.md    → review-frontmatter.schema.json
- templates/capability/SPEC.md   → capability-spec-frontmatter.schema.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from tools.migrations.registry import (
    file_kind_from_schema_filename,
    schema_version_error,
    schema_version_missing_is_fatal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = REPO_ROOT / "templates"


class _SchemaVersionError(RuntimeError):
    """Raised when a template declares an unsupported schema version."""


# Each entry: (template path relative to TEMPLATES_DIR, schema filename).
# Templates ship with bogus-but-valid placeholder values that pass their schema
# without further substitution; if a template needs runtime fill-in (the
# state.json template's feature_id), do it here.
_TEMPLATE_FRONTMATTER_BINDINGS: tuple[tuple[str, str], ...] = (
    ("feature/SPEC.md", "spec-frontmatter.schema.json"),
    ("feature/PLAN.md", "plan-frontmatter.schema.json"),
    ("feature/UNDERSTANDING.md", "understanding-frontmatter.schema.json"),
    ("feature/REVIEW.md", "review-frontmatter.schema.json"),
    ("capability/SPEC.md", "capability-spec-frontmatter.schema.json"),
    ("constitution/CONSTITUTION.md", "constitution-frontmatter.schema.json"),
    ("changes/proposal.md", "delta-proposal-frontmatter.schema.json"),
)


def _emit(message: str, *, quiet: bool) -> None:
    """Print `message` to stdout unless `quiet` is set."""
    if not quiet:
        print(message)


def _check_schema(path: Path, *, quiet: bool = False) -> None:
    """Validate a single shipped schema against Draft 2020-12 metaschema."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    _emit(f"OK schema {path.name}", quiet=quiet)


def _check_schema_version(path: Path, payload: dict[str, Any], file_kind: str | None) -> None:
    """Validate schema_version on a template; warn unless strict env demands an error."""
    issue = schema_version_error(path, payload, file_kind)
    if issue is None:
        return
    severity, message = issue
    if severity == "missing" and not schema_version_missing_is_fatal():
        warnings.warn(message, category=DeprecationWarning, stacklevel=3)
        return
    raise _SchemaVersionError(message)


def _check_state_template(*, quiet: bool = False) -> None:
    """Validate templates/feature/state.json against state.schema.json."""
    schema = json.loads((SCHEMAS_DIR / "state.schema.json").read_text(encoding="utf-8"))
    template: dict[str, Any] = json.loads(
        (TEMPLATES_DIR / "feature" / "state.json").read_text(encoding="utf-8")
    )
    template["feature_id"] = "2026-05-03-template-check"
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(template)
    _emit("OK template feature/state.json", quiet=quiet)


def _extract_frontmatter(template_path: Path) -> dict[str, Any]:
    body = template_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", body, flags=re.DOTALL)
    if not match:
        raise SystemExit(f"{template_path} missing frontmatter block")
    parsed: dict[str, Any] = yaml.safe_load(match.group(1))
    return parsed


def _check_template_frontmatter(
    template_rel: str,
    schema_filename: str,
    *,
    quiet: bool = False,
) -> None:
    """Validate one template's YAML frontmatter against its schema."""
    template_path = TEMPLATES_DIR / template_rel
    schema = json.loads((SCHEMAS_DIR / schema_filename).read_text(encoding="utf-8"))
    fm = _extract_frontmatter(template_path)
    _check_schema_version(template_path, fm, file_kind_from_schema_filename(schema_filename))

    # SPEC.md template still uses placeholder strings for non-frontmatter-schema
    # fields the validator checks (id/tier/created/capability) — fill them so
    # the format-checker is happy, mirroring the historic behavior.
    if template_rel == "feature/SPEC.md":
        fm["id"] = "2026-05-03-template-check"
        fm["tier"] = "focused"
        fm["created"] = "2026-05-03"
        fm["capability"] = "template-check"

    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(fm)
    _emit(f"OK template {template_rel}", quiet=quiet)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-check-schemas",
        description=(
            "Validate every shipped FORGE JSON Schema against the Draft 2020-12 "
            "metaschema, then validate each shipped template's frontmatter "
            "against its bound schema. Exits 0 when every check passes."
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-item OK lines on stdout; failures still print to stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run all schema and template checks; return 0 on success, 1 on failure.

    Args:
        argv: Optional argv override (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: 0 when every schema and template is valid, 1 on the first
        schema-version, ``SchemaError``, or ``ValidationError`` failure.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    quiet: bool = args.quiet

    try:
        for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
            _check_schema(path, quiet=quiet)
        _check_state_template(quiet=quiet)
        for template_rel, schema_filename in _TEMPLATE_FRONTMATTER_BINDINGS:
            _check_template_frontmatter(template_rel, schema_filename, quiet=quiet)
    except (_SchemaVersionError, jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
