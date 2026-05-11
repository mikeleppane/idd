"""RESEARCH.md shape + citation rule validator (M8 §5.3.2 D-RESEARCH).

Validates a single ``.forge/features/<id>/RESEARCH.md`` artifact against
the research-frontmatter schema, the four required H1 sections, and the
mode-aware citation rule produced by ``tools.research.citations``.
Severity follows the spec: structural problems BLOCK, missing citations
on code-fenced symbols WARN, and degraded mode requires the explicit
"Context7 not available" marker.
"""

from __future__ import annotations

from pathlib import Path

from tools.research import citations

from ._finding import Finding
from ._frontmatter import (
    _build_validator,
    _load_schema,
    _parse_frontmatter_or_finding,
    _read_text,
)

_TARGET = "research"

_REQUIRED_SECTIONS: tuple[str, ...] = (
    "# Codebase findings",
    "# External docs",
    "# Domain notes",
    "# Risks surfaced",
)


def _check_sections(body: str, path: Path) -> list[Finding]:
    return [
        Finding(
            "BLOCK",
            _TARGET,
            path,
            f"missing required section '{header}'",
        )
        for header in _REQUIRED_SECTIONS
        if header not in body
    ]


def validate_research(research_path: Path) -> list[Finding]:
    """Validate a single RESEARCH.md file.

    Checks:
        - File exists.
        - Frontmatter parses + validates against
          ``schemas/research-frontmatter.schema.json``; failures BLOCK.
        - All four required H1 sections present (BLOCK if missing).
        - When ``status == "done"`` and ``research_grounding != "degraded"``:
            - Mode-aware citation rule via ``tools.research.citations.validate``.
            - Each missing citation paragraph emits a WARN finding.
        - When ``research_grounding == "degraded"``: body MUST contain the
          ``_Context7 not available_`` marker; absence BLOCKs.
        - When ``research_grounding == "byod-partial"``: each uncovered
          library emits a WARN.

    Args:
        research_path: Path to the RESEARCH.md file.

    Returns:
        List of Finding records. Empty list means structurally valid.
    """
    findings: list[Finding] = []
    text = _read_text(research_path)
    if text is None:
        findings.append(
            Finding("BLOCK", _TARGET, research_path, f"file not found: {research_path}"),
        )
        return findings

    parsed = _parse_frontmatter_or_finding(text, _TARGET, research_path)
    if isinstance(parsed, Finding):
        findings.append(parsed)
        return findings
    fm, body = parsed

    schema = _load_schema("research-frontmatter.schema.json")
    schema_errors = list(_build_validator(schema).iter_errors(fm))
    for err in sorted(schema_errors, key=lambda e: list(e.path)):
        field = f".{err.path[-1]}" if err.path else ""
        findings.append(
            Finding(
                "BLOCK",
                _TARGET,
                research_path,
                f"frontmatter{field}: {err.message}",
            ),
        )
    if schema_errors:
        # Skip downstream content checks until the frontmatter is sound;
        # the values we'd read (status / research_grounding) may be missing
        # or mistyped and would produce noisy follow-on findings.
        return findings

    status = fm.get("status")
    grounding = fm.get("research_grounding")

    # Status gate: while a research artifact is in progress / skipped the
    # author has not yet promised a complete shape, so the strict section
    # and citation checks would produce premature noise.
    if status != "done":
        return findings

    findings.extend(_check_sections(body, research_path))

    result = citations.validate(body, mode=str(grounding), libraries=())

    if grounding == "degraded":
        if not result.degraded_marker_present:
            findings.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    research_path,
                    "research_grounding=degraded requires the "
                    "'_Context7 not available_' marker in the body",
                ),
            )
        return findings

    findings.extend(
        Finding(
            "WARN",
            _TARGET,
            research_path,
            f"missing citation for code-fenced symbol paragraph: {snippet}",
        )
        for snippet in result.missing_citations
    )

    if grounding == "byod-partial":
        findings.extend(
            Finding(
                "WARN",
                _TARGET,
                research_path,
                f"byod-partial: library {lib!r} not covered by staged BYOD docs",
            )
            for lib in result.byod_partial_uncovered
        )

    return findings
