"""Consolidated structural validator for IDD artifacts.

Per M3 spec §5.3.6: shipped checks are structural in P2a (frontmatter, capability
uniqueness, Constitution shape, delta shape, NR placement, repo health). Semantic
checks (scenario↔acceptance, plan task↔acceptance, anchors module-resolve,
deviation cross-ref, Verified Deps registry) ship in P2b.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator, FormatChecker

Severity = Literal["BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO"]
Target = Literal[
    "spec",
    "plan",
    "delta",
    "constitution",
    "ship",
    "health",
    "all",
    "capability-uniqueness",
]
EXIT_NONZERO_SEVERITIES: frozenset[Severity] = frozenset({"BLOCK", "HIGH"})


class ValidationError(RuntimeError):
    """Raised when validator setup fails (not when findings are produced)."""


@dataclass(frozen=True)
class Finding:
    """A single validator finding.

    Attributes:
        severity: see module-level Severity literal. Exit-affecting values are
            in `EXIT_NONZERO_SEVERITIES`; the rest are advisory.
        target: Which validator produced the finding.
        file: Repo-relative path to the file that triggered the finding.
        message: Human-readable description.
    """

    severity: Severity
    target: str
    file: Path
    message: str


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMAS_DIR = _REPO_ROOT / "schemas"

_FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_ARTICLE_HEADER = re.compile(r"## Article (\d+) — .+ \[(CRITICAL|SHOULD|MAY)\]")
_ARTICLE_BLOCK = re.compile(
    r"(?ms)^## Article (\d+) — [^\n]+ \[(?:CRITICAL|SHOULD|MAY)\][ \t]*$"
    r"(?P<body>.*?)"
    r"(?=^## Article \d+ — |\Z)"
)
_RULE_FIELD = re.compile(r"^\*\*Rule:\*\*", re.MULTILINE)
_EXCEPTION_FIELD = re.compile(r"^\*\*Exception:\*\*", re.MULTILINE)

_CONSTITUTION_ARTICLE_WARN_THRESHOLD = 12
_CONSTITUTION_ARTICLE_BLOCK_THRESHOLD = 16


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


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


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """Return `(frontmatter, body)` or `None` if no parseable frontmatter.

    Returning the body alongside the parsed dict lets callers avoid re-running
    `_FRONTMATTER.match()` (mypy --strict cannot narrow repeated regex calls,
    and the duplication invited the bug fixed by this helper).
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    parsed = yaml.safe_load(match.group(1))
    if not isinstance(parsed, dict):
        return None
    return _coerce_dates(parsed), text[match.end() :]


def _build_validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_constitution(path: Path) -> list[Finding]:
    """Validate `.idd/CONSTITUTION.md` structural shape per M3 spec §5.3.1.

    Checks (in order):
        1. File exists.
        2. Frontmatter present and matches schema.
        3. Each `## Article N — <title> [LEVEL]` header is well-formed.
        4. Article numbers monotonic from 1 (every gap reported, no early break).
        5. Each article body contains a `**Rule:**` AND `**Exception:**` field
           (per-article check, not document-wide).
        6. Article count: WARN at >= 12, BLOCK at >= 16.

    Args:
        path: Path to the Constitution file.

    Returns:
        List of Finding records. Empty list means structurally valid.
    """
    findings: list[Finding] = []
    text = _read_text(path)
    if text is None:
        findings.append(
            Finding("BLOCK", "constitution", path, f"file not found: {path}"),
        )
        return findings

    parsed = _parse_frontmatter(text)
    if parsed is None:
        findings.append(
            Finding("BLOCK", "constitution", path, "missing or malformed frontmatter"),
        )
        return findings
    fm, body = parsed

    schema = _load_schema("constitution-frontmatter.schema.json")
    findings.extend(
        Finding("BLOCK", "constitution", path, f"frontmatter: {err.message}")
        for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path))
    )

    article_lines = [line for line in body.splitlines() if line.startswith("## Article")]
    article_numbers: list[int] = []
    for line in article_lines:
        match = _ARTICLE_HEADER.fullmatch(line.rstrip())
        if not match:
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"malformed article header: {line!r}; "
                    f"expected '## Article N — <title> [CRITICAL|SHOULD|MAY]'",
                ),
            )
            continue
        article_numbers.append(int(match.group(1)))

    for index, number in enumerate(article_numbers, start=1):
        if number != index:
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"article numbers not monotonic: expected {index}, found {number}",
                ),
            )

    for block in _ARTICLE_BLOCK.finditer(body):
        article_no = block.group(1)
        article_body = block.group("body") or ""
        if not _RULE_FIELD.search(article_body):
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"article {article_no} missing **Rule:** field",
                ),
            )
        if not _EXCEPTION_FIELD.search(article_body):
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"article {article_no} missing **Exception:** field",
                ),
            )

    article_count = len(article_numbers)
    if article_count >= _CONSTITUTION_ARTICLE_BLOCK_THRESHOLD:
        findings.append(
            Finding(
                "BLOCK",
                "constitution",
                path,
                f"article count {article_count} exceeds hard cap "
                f"({_CONSTITUTION_ARTICLE_BLOCK_THRESHOLD}); tighten before proceeding",
            ),
        )
    elif article_count >= _CONSTITUTION_ARTICLE_WARN_THRESHOLD:
        findings.append(
            Finding(
                "WARN",
                "constitution",
                path,
                f"article count {article_count} approaches cap "
                f"({_CONSTITUTION_ARTICLE_BLOCK_THRESHOLD}); consider tightening",
            ),
        )

    return findings


def _load_schema(filename: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    return result
