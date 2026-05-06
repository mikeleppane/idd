"""Delta proposal structural validator (M3 §5.3.6 D-DELTA)."""

from __future__ import annotations

import re
from pathlib import Path

from ._finding import Finding
from ._frontmatter import (
    _build_validator,
    _load_schema,
    _parse_frontmatter_or_finding,
    _read_text,
)

_DELTA_OP_MARKER = re.compile(r"^[+\-~] (ADD|REMOVE|MODIFY):", re.MULTILINE)
_AFFECTS_HEADER = re.compile(r"^## Affects\s*$", re.MULTILINE)
_DELTA_HEADER = re.compile(r"^## Delta\s*$", re.MULTILINE)
_NEXT_H2 = re.compile(r"^## ", re.MULTILINE)


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
