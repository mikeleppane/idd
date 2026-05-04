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

_DELTA_OP_MARKER = re.compile(r"^[+\-~] (ADD|REMOVE|MODIFY):", re.MULTILINE)


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
    for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding("BLOCK", "constitution", path, f"frontmatter{field}: {err.message}"),
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


def validate_delta(path: Path) -> list[Finding]:
    """Validate `.idd/changes/<id>/proposal.md` structural shape per M3 spec §5.3.5.

    Checks (in order):
        1. File exists.
        2. Frontmatter present and matches delta-proposal schema.
        3. `## Affects` section present.
        4. `## Delta` section present and contains at least one op marker
           (`+ ADD:`, `- REMOVE:`, `~ MODIFY:`).

    Args:
        path: Path to the proposal.md file.

    Returns:
        List of Finding records. Empty list means structurally valid.
    """
    findings: list[Finding] = []
    text = _read_text(path)
    if text is None:
        findings.append(
            Finding("BLOCK", "delta", path, f"file not found: {path}"),
        )
        return findings

    parsed = _parse_frontmatter(text)
    if parsed is None:
        findings.append(
            Finding("BLOCK", "delta", path, "missing or malformed frontmatter"),
        )
        return findings
    fm, body = parsed

    schema = _load_schema("delta-proposal-frontmatter.schema.json")
    for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding("BLOCK", "delta", path, f"frontmatter{field}: {err.message}"),
        )

    framed = "\n" + body
    if "\n## Affects" not in framed:
        findings.append(
            Finding("BLOCK", "delta", path, "missing required '## Affects' section"),
        )

    if "\n## Delta" not in framed:
        findings.append(
            Finding("BLOCK", "delta", path, "missing required '## Delta' section"),
        )
    elif not _DELTA_OP_MARKER.search(body):
        findings.append(
            Finding(
                "BLOCK",
                "delta",
                path,
                "## Delta section has no operator markers; "
                "expected '+ ADD:', '- REMOVE:', or '~ MODIFY:'",
            ),
        )

    return findings


_NR_PHRASE = re.compile(r"\b(SHALL NOT|MUST NOT)\b")
_NR_SECTION = re.compile(r"^# Negative Requirements\s*$", re.MULTILINE)
_FENCE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


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


def validate_negative_requirements(path: Path) -> list[Finding]:
    """Validate `# Negative Requirements` placement per M3 spec §7.1 NR rules.

    Checks:
        1. File exists.
        2. If any `SHALL NOT` / `MUST NOT` sentence appears in SPEC.md (outside
           code fences and inline code), it MUST be inside the
           `# Negative Requirements` section. Occurrences elsewhere BLOCK at
           spec phase exit.

    Args:
        path: Path to the SPEC.md file.

    Returns:
        List of Finding records. Empty list means structurally valid.
    """
    findings: list[Finding] = []
    text = _read_text(path)
    if text is None:
        findings.append(
            Finding("BLOCK", "spec", path, f"file not found: {path}"),
        )
        return findings

    scan = _strip_code(text)
    section_match = _NR_SECTION.search(scan)
    has_section = section_match is not None
    if section_match is not None:
        next_h1 = re.search(
            r"^# [^\n]+$",
            scan[section_match.end() :],
            re.MULTILINE,
        )
        section_start = section_match.start()
        section_end = section_match.end() + next_h1.start() if next_h1 else len(scan)
    else:
        section_start = section_end = -1

    for match in _NR_PHRASE.finditer(scan):
        offset = match.start()
        if has_section and section_start <= offset < section_end:
            continue
        line_no = scan.count("\n", 0, offset) + 1
        phrase = match.group(0)
        if not has_section:
            findings.append(
                Finding(
                    "BLOCK",
                    "spec",
                    path,
                    f"line {line_no}: '{phrase}' phrase used but no "
                    f"'# Negative Requirements' section exists",
                ),
            )
        else:
            findings.append(
                Finding(
                    "BLOCK",
                    "spec",
                    path,
                    f"line {line_no}: '{phrase}' phrase appears outside "
                    f"'# Negative Requirements' section",
                ),
            )

    return findings


def _read_capability_from_spec(spec_path: Path) -> str | None:
    text = _read_text(spec_path)
    if text is None:
        return None
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return None
    fm, _body = parsed
    capability = fm.get("capability")
    if isinstance(capability, str) and capability:
        return capability
    return None


def _iter_archived_feature_specs(features_root: Path) -> list[Path]:
    """Return SPEC.md paths under `features/archive/...`, any depth."""
    archive_root = features_root / "archive"
    if not archive_root.is_dir():
        return []
    return sorted(p for p in archive_root.rglob("SPEC.md") if p.is_file())


def _collect_canonical_capabilities(idd_root: Path) -> dict[str, list[Path]]:
    """Return {capability: [SPEC.md, ...]} for all canonical specs under .idd/specs/."""
    result: dict[str, list[Path]] = {}
    specs_root = idd_root / "specs"
    if not specs_root.is_dir():
        return result
    for entry in sorted(specs_root.iterdir()):
        if not entry.is_dir():
            continue
        cap = _read_capability_from_spec(entry / "SPEC.md")
        if cap is not None:
            result.setdefault(cap, []).append(entry / "SPEC.md")
    return result


def _collect_feature_capabilities(
    idd_root: Path,
) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """Return ({active cap: paths}, {archived cap: paths}) under .idd/features/."""
    active: dict[str, list[Path]] = {}
    archived: dict[str, list[Path]] = {}
    features_root = idd_root / "features"
    if not features_root.is_dir():
        return active, archived
    for entry in sorted(features_root.iterdir()):
        if not entry.is_dir() or entry.name == "archive":
            continue
        cap = _read_capability_from_spec(entry / "SPEC.md")
        if cap is not None:
            active.setdefault(cap, []).append(entry / "SPEC.md")
    for spec in _iter_archived_feature_specs(features_root):
        cap = _read_capability_from_spec(spec)
        if cap is not None:
            archived.setdefault(cap, []).append(spec)
    return active, archived


def validate_capability_uniqueness(repo_root: Path) -> list[Finding]:
    """Detect capability slug collisions per M3 spec §5.3.6 D-HEALTH.

    See locked semantics in the implementation plan. Severity HIGH (matches the
    spec's HIGH classification for collision; non-zero exit per `EXIT_NONZERO_SEVERITIES`).

    Args:
        repo_root: Repository root containing the .idd/ tree.

    Returns:
        List of Finding records. Empty list means no collisions on the active
        surface.
    """
    findings: list[Finding] = []
    idd_root = repo_root / ".idd"
    if not idd_root.is_dir():
        return findings

    canonical = _collect_canonical_capabilities(idd_root)
    active, archived = _collect_feature_capabilities(idd_root)

    def _emit(cap: str, paths: list[Path]) -> None:
        joined = ", ".join(str(p.relative_to(repo_root)) for p in paths)
        findings.append(
            Finding(
                "HIGH",
                "capability-uniqueness",
                repo_root,
                f"capability slug {cap!r} declared by multiple sources: {joined}",
            ),
        )

    for cap, paths in canonical.items():
        if len(paths) > 1:
            _emit(cap, paths)

    for cap, paths in active.items():
        sources = list(paths)
        sources.extend(canonical.get(cap, []))
        sources.extend(archived.get(cap, []))
        if len(sources) > 1:
            _emit(cap, sources)

    return findings


def _load_schema(filename: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    return result
