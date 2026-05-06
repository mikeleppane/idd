"""Frontmatter parsing + schema loading helpers (M3 §5.3.6 D-FRONTMATTER)."""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from ._finding import Finding

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _REPO_ROOT / "schemas"

_FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_FENCE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def _read_text(path: Path) -> str | None:
    r"""Return file contents, or `None` if the path is missing or not a regular file.

    Treats directories, symlinks-to-missing-targets, and other non-file shapes
    as "no readable text" so callers surface a structural BLOCK finding instead
    of crashing the validator with `IsADirectoryError` / `OSError`.

    Uses `utf-8-sig` so a UTF-8 BOM (common from Windows editors) is stripped
    transparently rather than breaking the `^---\n` frontmatter regex.
    """
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig")


def _coerce_dates(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert PyYAML-decoded `datetime.date` values back to ISO strings.

    PyYAML decodes unquoted dates (`created: 2026-01-01`) into `datetime.date`,
    but the shipped JSON Schemas declare these fields as `{"type": "string",
    "format": "date"}`. Mirror `tools.lint_frontmatter` to keep validation
    consistent across the two entry points.
    """
    coerced: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
            coerced[key] = value.isoformat()
        else:
            coerced[key] = value
    return coerced


class _FrontmatterParseError(RuntimeError):
    """Raised when the YAML frontmatter block is present but malformed.

    Distinct from "no frontmatter" so callers can surface a precise BLOCK
    finding instead of crashing the CLI on a `yaml.YAMLError` traceback.
    """


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """Return `(frontmatter, body)` or `None` if no parseable frontmatter.

    Returning the body alongside the parsed dict lets callers avoid re-running
    `_FRONTMATTER.match()` (mypy --strict cannot narrow repeated regex calls,
    and the duplication invited the bug fixed by this helper).

    Raises:
        _FrontmatterParseError: when the `---` block exists but the YAML
            inside fails to parse, or decodes to a non-mapping.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise _FrontmatterParseError(f"invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _FrontmatterParseError(
            f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )
    return _coerce_dates(parsed), text[match.end() :]


def _parse_frontmatter_or_finding(
    text: str, target: str, path: Path
) -> tuple[dict[str, Any], str] | Finding:
    """Parse frontmatter; on any structural failure return a single BLOCK Finding.

    Centralizes the missing/malformed-frontmatter branch so each validator
    gets identical error shape and stays crash-free on bad YAML.
    """
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError as exc:
        return Finding("BLOCK", target, path, str(exc))
    if parsed is None:
        return Finding("BLOCK", target, path, "missing or malformed frontmatter")
    return parsed


def _build_validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _load_schema(filename: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    return result


def _strip_code(text: str) -> str:
    """Replace fenced + inline code regions with same-length whitespace.

    NR phrases inside code fences are intentional examples (REVIEW.md prose,
    Constitution article quotations, illustrative bash). Whitespace replacement
    preserves byte offsets so reported line numbers still match the original
    file.
    """
    out = _FENCE_BLOCK.sub(lambda m: " " * len(m.group(0)), text)
    out = _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), out)
    return out
