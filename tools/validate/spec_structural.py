"""SPEC structural validators: NR placement, frontmatter, capability uniqueness (M3 §5.3.6)."""

from __future__ import annotations

import re
from pathlib import Path

from ._finding import Finding, ValidationError
from ._frontmatter import (
    _build_validator,
    _FrontmatterParseError,
    _load_schema,
    _parse_frontmatter,
    _parse_frontmatter_or_finding,
    _read_text,
    _schema_version_findings,
    _strip_code,
)

_NR_PHRASE = re.compile(r"\b(SHALL NOT|MUST NOT)\b")
_NR_SECTION = re.compile(r"^# Negative Requirements\s*$", re.MULTILINE)

_CAPABILITY_SPEC_REQUIRED_SECTIONS: frozenset[str] = frozenset(
    {
        "Intent",
        "Scope",
        "Domain",
        "Scenarios",
        "Acceptance Criteria",
        "Negative Requirements",
        "Decisions",
    }
)

_CAPABILITY_SPEC_SECTION_RE = re.compile(
    r"^## (Intent|Scope|Domain|Scenarios|Acceptance Criteria"
    r"|Negative Requirements|Decisions)\s*$",
    re.MULTILINE,
)


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


def validate_capability_spec_sections(path: Path) -> list[Finding]:
    """Validate that all 7 required H2 sections are present in a capability SPEC.

    Checks that each section in `_CAPABILITY_SPEC_REQUIRED_SECTIONS` appears as
    an exact H2 header (``## <Name>``) at column 0.  Order is not enforced; only
    presence.  One BLOCK finding is emitted per missing section.

    Args:
        path: Path to the capability SPEC.md file.

    Returns:
        List of Finding records.  Empty list means all required sections present.
    """
    findings: list[Finding] = []
    text = _read_text(path)
    if text is None:
        findings.append(
            Finding("BLOCK", "spec", path, f"file not found: {path}"),
        )
        return findings

    present = frozenset(_CAPABILITY_SPEC_SECTION_RE.findall(_strip_code(text)))
    missing = _CAPABILITY_SPEC_REQUIRED_SECTIONS - present
    findings.extend(
        Finding(
            "BLOCK",
            "spec",
            path,
            f"missing required '## {name}' section",
        )
        for name in sorted(missing)
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


def _collect_canonical_capabilities(forge_root: Path) -> dict[str, list[Path]]:
    """Return {capability: [SPEC.md, ...]} for all canonical specs under .forge/specs/."""
    result: dict[str, list[Path]] = {}
    specs_root = forge_root / "specs"
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
    forge_root: Path,
) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """Return ({active cap: paths}, {archived cap: paths}) under .forge/features/."""
    active: dict[str, list[Path]] = {}
    archived: dict[str, list[Path]] = {}
    features_root = forge_root / "features"
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
        repo_root: Repository root containing the .forge/ tree.

    Returns:
        List of Finding records. Empty list means no collisions on the active
        surface.
    """
    findings: list[Finding] = []
    forge_root = repo_root / ".forge"
    if not forge_root.is_dir():
        return findings

    canonical = _collect_canonical_capabilities(forge_root)
    active, archived = _collect_feature_capabilities(forge_root)

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

    sv_findings = _schema_version_findings(path, fm, schema_filename, kind)
    if sv_findings:
        # Forward-version or bad-typed schema_version BLOCKS before structural
        # checks run, so the operator sees the actionable migration message
        # instead of a cascade of downstream validation errors.
        findings.extend(sv_findings)
        return findings

    schema = _load_schema(schema_filename)
    for err in sorted(_build_validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding("BLOCK", kind, path, f"frontmatter{field}: {err.message}"),
        )
    return findings
