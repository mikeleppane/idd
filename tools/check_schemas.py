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

import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = REPO_ROOT / "templates"

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
)


def _check_schema(path: Path) -> None:
    """Validate a single shipped schema against Draft 2020-12 metaschema."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    print(f"OK schema {path.name}")


def _check_state_template() -> None:
    """Validate templates/feature/state.json against state.schema.json."""
    schema = json.loads((SCHEMAS_DIR / "state.schema.json").read_text(encoding="utf-8"))
    template: dict[str, Any] = json.loads(
        (TEMPLATES_DIR / "feature" / "state.json").read_text(encoding="utf-8")
    )
    template["feature_id"] = "2026-05-03-template-check"
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(template)
    print("OK template feature/state.json")


def _extract_frontmatter(template_path: Path) -> dict[str, Any]:
    body = template_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", body, flags=re.DOTALL)
    if not match:
        raise SystemExit(f"{template_path} missing frontmatter block")
    parsed: dict[str, Any] = yaml.safe_load(match.group(1))
    return parsed


def _check_template_frontmatter(template_rel: str, schema_filename: str) -> None:
    """Validate one template's YAML frontmatter against its schema."""
    template_path = TEMPLATES_DIR / template_rel
    schema = json.loads((SCHEMAS_DIR / schema_filename).read_text(encoding="utf-8"))
    fm = _extract_frontmatter(template_path)

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
    print(f"OK template {template_rel}")


def main() -> int:
    """Run all schema and template checks; return 0 on success, 1 on failure."""
    try:
        for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
            _check_schema(path)
        _check_state_template()
        for template_rel, schema_filename in _TEMPLATE_FRONTMATTER_BINDINGS:
            _check_template_frontmatter(template_rel, schema_filename)
    except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
