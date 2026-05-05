"""Consolidated structural validator for IDD artifacts.

Per M3 spec §5.3.6: shipped checks are structural in P2a (frontmatter, capability
uniqueness, Constitution shape, delta shape, NR placement, repo health). Semantic
checks (scenario↔acceptance, plan task↔acceptance, anchors module-resolve,
deviation cross-ref, Verified Deps registry) ship in P2b.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator, FormatChecker

Severity = Literal["BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO"]
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
_AFFECTS_HEADER = re.compile(r"^## Affects\s*$", re.MULTILINE)
_DELTA_HEADER = re.compile(r"^## Delta\s*$", re.MULTILINE)
_NEXT_H2 = re.compile(r"^## ", re.MULTILINE)


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

    parsed = _parse_frontmatter_or_finding(text, "constitution", path)
    if isinstance(parsed, Finding):
        findings.append(parsed)
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

    expected = 1
    for number in article_numbers:
        if number != expected:
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"article numbers not monotonic: expected {expected}, found {number}",
                ),
            )
            expected = number  # resync so each gap fires once, not N times
        expected += 1

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

    parsed = _parse_frontmatter_or_finding(text, "delta", path)
    if isinstance(parsed, Finding):
        findings.append(parsed)
        return findings
    fm, body = parsed

    schema = _load_schema("delta-proposal-frontmatter.schema.json")
    for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding("BLOCK", "delta", path, f"frontmatter{field}: {err.message}"),
        )

    if not _AFFECTS_HEADER.search(body):
        findings.append(
            Finding("BLOCK", "delta", path, "missing required '## Affects' section"),
        )

    delta_match = _DELTA_HEADER.search(body)
    if delta_match is None:
        findings.append(
            Finding("BLOCK", "delta", path, "missing required '## Delta' section"),
        )
    else:
        section_start = delta_match.end()
        next_h2 = _NEXT_H2.search(body, section_start)
        section_end = next_h2.start() if next_h2 else len(body)
        delta_section = body[section_start:section_end]
        if not _DELTA_OP_MARKER.search(delta_section):
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
    """Best-effort capability slug extraction.

    Returns None on missing file, missing/malformed frontmatter, or non-string
    capability. The per-target spec validator (`validate_frontmatter` /
    `validate_negative_requirements`) is responsible for surfacing parse
    errors as findings; this helper stays silent so the health scan keeps
    moving across the rest of the tree.
    """
    text = _read_text(spec_path)
    if text is None:
        return None
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError:
        return None
    if parsed is None:
        return None
    fm, _body = parsed
    capability = fm.get("capability")
    if isinstance(capability, str) and capability:
        return capability
    return None


def _iter_archived_feature_specs(features_root: Path) -> list[Path]:
    """Return SPEC.md paths exactly one level under `features/archive/`.

    Layout is `features/archive/<feature-id>/SPEC.md`; deeper SPEC.md files
    (e.g. inside a `notes/` subfolder under an archived feature) would be
    drafts or stray copies, not authoritative archive entries — exclude them
    so they cannot drive false-positive collisions.
    """
    archive_root = features_root / "archive"
    if not archive_root.is_dir():
        return []
    return sorted(p for p in archive_root.glob("*/SPEC.md") if p.is_file())


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


_FRONTMATTER_SCHEMA_BY_KIND: dict[str, str] = {
    "spec": "spec-frontmatter.schema.json",
    "plan": "plan-frontmatter.schema.json",
    "understanding": "understanding-frontmatter.schema.json",
    "review": "review-frontmatter.schema.json",
    "capability-spec": "capability-spec-frontmatter.schema.json",
    "constitution": "constitution-frontmatter.schema.json",
    "delta": "delta-proposal-frontmatter.schema.json",
}


def validate_frontmatter(path: Path, *, kind: str) -> list[Finding]:
    """Validate a markdown file's frontmatter against the schema for `kind`.

    Args:
        path: Path to the markdown file.
        kind: One of `spec`, `plan`, `understanding`, `review`, `capability-spec`,
            `constitution`, `delta`.

    Returns:
        List of Finding records. Empty list means valid frontmatter.

    Raises:
        ValidationError: when `kind` is not recognized.
    """
    schema_filename = _FRONTMATTER_SCHEMA_BY_KIND.get(kind)
    if schema_filename is None:
        raise ValidationError(
            f"unknown kind {kind!r}; must be one of {tuple(_FRONTMATTER_SCHEMA_BY_KIND)}"
        )

    findings: list[Finding] = []
    text = _read_text(path)
    if text is None:
        findings.append(Finding("BLOCK", kind, path, f"file not found: {path}"))
        return findings

    parsed = _parse_frontmatter_or_finding(text, kind, path)
    if isinstance(parsed, Finding):
        findings.append(parsed)
        return findings
    fm, _body = parsed

    schema = _load_schema(schema_filename)
    for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding("BLOCK", kind, path, f"frontmatter{field}: {err.message}"),
        )
    return findings


def _state_payload(state_path: Path) -> dict[str, Any] | None:
    """Best-effort parse of a state.json. Returns None on any failure."""
    text = _read_text(state_path)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _check_feature_payload(
    entry: Path,
    state_path: Path,
    payload: dict[str, Any],
) -> list[Finding]:
    """Check id match, phase validity, done-not-archived, and orphan for a parsed payload."""
    findings: list[Finding] = []
    recorded_id = payload.get("feature_id")
    if recorded_id != entry.name:
        findings.append(
            Finding(
                "HIGH",
                "health",
                state_path,
                f"folder name {entry.name!r} does not match "
                f"state.json.feature_id {recorded_id!r}; "
                f"manual rename or git mv required",
            ),
        )
        return findings

    current_phase = payload.get("current_phase")
    phases = payload.get("phases")
    if (
        isinstance(current_phase, str)
        and isinstance(phases, dict)
        and current_phase != "done"
        and current_phase not in phases
    ):
        findings.append(
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"current_phase {current_phase!r} not in phases enum "
                f"for feature {entry.name!r}; restore from git history",
            ),
        )
        return findings

    if current_phase == "done":
        findings.append(
            Finding(
                "MEDIUM",
                "health",
                entry,
                f"feature {entry.name!r} is at current_phase=done but not "
                f"archived; run /idd:ship or tools.archive.archive_feature",
            ),
        )
        return findings

    commits = payload.get("commits") or []
    refine_block = phases.get("refine") if isinstance(phases, dict) else None
    extra_files = [
        p
        for p in entry.iterdir()
        if p.name
        not in {
            "state.json",
            "SPEC.md",
            "PLAN.md",
            "UNDERSTANDING.md",
            "REVIEW.md",
            "REVIEW.plan.md",
            "REVIEW.code.md",
            "VERIFICATION.md",
            "decisions.md",
        }
    ]
    if (
        current_phase == "refine"
        and isinstance(refine_block, dict)
        and refine_block.get("status") == "in_progress"
        and not commits
        and not extra_files
    ):
        findings.append(
            Finding(
                "LOW",
                "health",
                entry,
                f"orphan feature folder {entry.name!r} (refine + no commits); "
                f"run tools.archive.cleanup_orphan_feature(<id>) when ready",
            ),
        )
    return findings


def _check_feature_entry(
    entry: Path,
    state_validator: Draft202012Validator,
) -> list[Finding]:
    """Run all per-feature health checks. Returns findings for one feature folder."""
    state_path = entry / "state.json"
    if not state_path.exists():
        return [
            Finding(
                "HIGH",
                "health",
                entry,
                f"feature folder {entry.name!r} is missing state.json; "
                f"re-seed from templates/feature/state.json or archive",
            )
        ]

    payload = _state_payload(state_path)
    if payload is None:
        return [
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"state.json failed to parse for feature {entry.name!r}; restore from git history",
            )
        ]

    schema_errors = sorted(
        state_validator.iter_errors(payload),
        key=lambda e: list(e.path),
    )
    if schema_errors:
        return [
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"state.json fails schema for feature {entry.name!r}: "
                f"{err.message}; restore from git history",
            )
            for err in schema_errors
        ]

    return _check_feature_payload(entry, state_path, payload)


def _check_change_entry(entry: Path, canonical_root: Path) -> list[Finding]:
    """Run all per-change health checks. Returns findings for one change folder."""
    findings: list[Finding] = []
    proposal = entry / "proposal.md"
    if not proposal.exists():
        return findings
    text = _read_text(proposal)
    if text is None:
        return findings
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError:
        return findings
    if parsed is None:
        return findings
    fm, _body = parsed
    affects = fm.get("affects_capability")
    status = fm.get("status")
    if isinstance(affects, str):
        canonical = canonical_root / affects / "SPEC.md"
        if not canonical.exists():
            findings.append(
                Finding(
                    "HIGH",
                    "health",
                    proposal,
                    f"change {entry.name!r} targets non-existent "
                    f"canonical capability {affects!r}; fix affects_capability "
                    f"or drop the change",
                ),
            )
            return findings
    if status == "approved":
        findings.append(
            Finding(
                "MEDIUM",
                "health",
                proposal,
                f"change {entry.name!r} is approved but not merged; "
                f"run /idd:ship --change {entry.name}",
            ),
        )
    return findings


def _check_canonical_entry(entry: Path) -> list[Finding]:
    """Run canonical-spec health checks for one slug folder."""
    findings: list[Finding] = []
    spec = entry / "SPEC.md"
    text = _read_text(spec)
    if text is None:
        return findings
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError:
        return findings
    if parsed is None:
        return findings
    fm, _body = parsed
    if not fm.get("evidence"):
        findings.append(
            Finding(
                "LOW",
                "health",
                spec,
                f"canonical spec {entry.name!r} missing 'evidence:' link "
                f"to source archived feature; backfill manually",
            ),
        )
    return findings


def validate_health(repo_root: Path) -> list[Finding]:
    """Repo-wide IDD health scan per M3 spec §5.3.6 D-HEALTH.

    Read-only. Each finding has severity + remediation hint embedded in message.
    Severities mirror the spec table directly:

        | Check                                                         | Severity |
        | Orphan feature folder                                          | LOW      |
        | Feature folder name != state.json.feature_id                   | HIGH     |
        | state.json fails to parse                                      | BLOCK    |
        | state.json fails schema validation                             | BLOCK    |
        | state.json.current_phase not in phases enum                    | BLOCK    |
        | Feature folder missing state.json                              | HIGH     |
        | Feature with current_phase=done not archived                   | MEDIUM   |
        | Capability slug collision                                      | HIGH     |
        | Approved change not merged                                     | MEDIUM   |
        | Change targets non-existent canonical capability               | HIGH     |
        | Canonical SPEC.md missing `evidence:` link                     | LOW      |
        | Constitution article count >=12                                | WARN     |
        | Constitution article count >=16                                | BLOCK    |

    Args:
        repo_root: Repository root containing the .idd/ tree.

    Returns:
        List of Finding records. Empty list means all checks clean.

    Note:
        Findings delegated from `validate_capability_uniqueness` and
        `validate_constitution` carry their source validator's `target` field
        (e.g. ``"capability-uniqueness"``, ``"constitution"``) rather than
        ``"health"``. This preserves provenance so the user knows which
        sub-validator produced each finding when ``/idd:validate --target
        health`` aggregates results.
    """
    findings: list[Finding] = []
    idd_root = repo_root / ".idd"
    if not idd_root.is_dir():
        return findings

    findings.extend(validate_capability_uniqueness(repo_root))

    constitution = idd_root / "CONSTITUTION.md"
    if constitution.exists():
        findings.extend(validate_constitution(constitution))

    state_schema = _load_schema("state.schema.json")
    state_validator = _build_validator(state_schema)

    features_root = idd_root / "features"
    if features_root.is_dir():
        for entry in sorted(features_root.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            findings.extend(_check_feature_entry(entry, state_validator))

    canonical_root = idd_root / "specs"
    changes_root = idd_root / "changes"
    if changes_root.is_dir():
        for entry in sorted(changes_root.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            findings.extend(_check_change_entry(entry, canonical_root))

    if canonical_root.is_dir():
        for entry in sorted(canonical_root.iterdir()):
            if not entry.is_dir():
                continue
            findings.extend(_check_canonical_entry(entry))

    return findings


def _load_schema(filename: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    return result


_PER_FILE_TARGETS: frozenset[str] = frozenset({"spec", "plan", "delta"})
_REPO_WIDE_TARGETS: frozenset[str] = frozenset({"health", "ship", "all"})


def _finding_to_dict(finding: Finding) -> dict[str, str]:
    return {
        "severity": finding.severity,
        "target": finding.target,
        "file": str(finding.file),
        "message": finding.message,
    }


def _dispatch_target(target: str, path: Path | None, repo_root: Path) -> list[Finding]:
    """Run the validator(s) for *target* and return all findings."""
    findings: list[Finding] = []

    if target in _PER_FILE_TARGETS and path is None:
        findings.append(
            Finding(
                "BLOCK",
                target,
                Path(),
                f"--target {target} requires a path argument",
            )
        )
    elif target == "delta" and path is not None:
        findings.extend(validate_delta(path))
    elif target == "spec" and path is not None:
        findings.extend(validate_negative_requirements(path))
        findings.extend(validate_frontmatter(path, kind="spec"))
    elif target == "plan" and path is not None:
        findings.extend(validate_frontmatter(path, kind="plan"))
    elif target == "constitution":
        resolved = path or repo_root / ".idd" / "CONSTITUTION.md"
        findings.extend(validate_constitution(resolved))
    elif target == "ship":
        findings.extend(validate_capability_uniqueness(repo_root))
    elif target in {"health", "all"}:
        # P2a deviation: `all` is staged to `health` only. Per-file fan-out
        # over .idd/specs/, .idd/changes/, .idd/features/ ships in P2b
        # alongside the semantic checks. See commands/validate.md.
        findings.extend(validate_health(repo_root))

    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for /idd:validate. See module-level exit-code contract."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.validate",
        description="IDD structural validator (M3 P2a)",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=[
            "spec",
            "plan",
            "delta",
            "constitution",
            "ship",
            "health",
            "all",
        ],
        help="Which validator to run.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root for repo-wide checks (default: cwd).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Optional path to a single artifact for per-file targets.",
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse already wrote the usage error to stderr.
        return exc.code if isinstance(exc.code, int) else 2

    target = args.target
    findings: list[Finding] = []

    if target in _REPO_WIDE_TARGETS and args.path is not None:
        findings.append(
            Finding(
                "WARN",
                target,
                args.path,
                f"--target {target} ignores positional path argument",
            ),
        )

    findings.extend(_dispatch_target(target, args.path, args.repo_root))

    payload = {
        "target": target,
        "findings": [_finding_to_dict(f) for f in findings],
    }
    print(json.dumps(payload, indent=2))

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary_parts = [f"validate: target={target}", f"findings={len(findings)}"]
    summary_parts.extend(
        f"{sev.lower()}={counts[sev]}"
        for sev in ("BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO")
        if counts.get(sev)
    )
    print(" ".join(summary_parts), file=sys.stderr)

    has_exit_severity = any(f.severity in EXIT_NONZERO_SEVERITIES for f in findings)
    return 1 if has_exit_severity else 0


if __name__ == "__main__":
    raise SystemExit(main())
