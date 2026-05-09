"""Domain glossary cross-reference validator.

Cross-references DOMAIN.md glossary rows against domain-flavoured terms used
in SPEC.md ``# Intent`` and ``# Scenarios`` sections. Surfaces orphan terms,
duplicate glossary rows, unused glossary entries, undefined-context
annotations, and malformed table rows.

The validator is a pure function. No subprocess, no I/O beyond reading the
SPEC.md / DOMAIN.md / state.json files inside ``.forge/features/<id>/``.

Findings (severity → code → meaning):

- ``BLOCK`` ``domain_glossary:feature_missing``: feature directory absent.
- ``BLOCK`` ``domain_glossary:domain_md_missing``: full-tier feature lacks
  DOMAIN.md. Focused / standard tiers skip the check.
- ``BLOCK`` ``domain_glossary:malformed_glossary_row``: glossary table row
  has fewer than four cells or an empty Term cell.
- ``BLOCK`` ``domain_glossary:duplicate_term``: same term appears twice in
  the glossary (case-insensitive after stripping context annotation).
- ``BLOCK`` ``domain_glossary:orphan_term``: SPEC term not defined in the
  glossary.
- ``MEDIUM`` ``domain_glossary:unused_glossary_entry``: glossary term not
  referenced by SPEC ``# Intent`` or ``# Scenarios``.
- ``LOW`` ``domain_glossary:undefined_context``: ``[term](context: <ctx-id>)``
  annotation references a context that no other glossary row anchors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from ._feature_layout import SPEC_FILENAME, STATE_FILENAME
from ._finding import Finding, Severity
from ._frontmatter import _read_text

TARGET = "domain_glossary"

DOMAIN_FILENAME = "DOMAIN.md"

_GLOSSARY_REQUIRED_COLUMNS = 4
_CONTEXT_PLACEHOLDERS: frozenset[str] = frozenset({"—", "-", ""})
# DOMAIN.md frontmatter status lifecycle (per docs/plans/...m7...md P1.1):
#   draft / ready  → BLOCK findings demote to advisory (MEDIUM)
#   locked         → BLOCK findings stay BLOCK
# Unknown / absent status is treated as ``draft`` (most permissive) so
# first-pass authoring is never gated by a half-written DOMAIN.md.
_LOCKED_STATUS = "locked"
_ADVISORY_STATUSES: frozenset[str] = frozenset({"draft", "ready"})

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_GLOSSARY_BLOCK = re.compile(r"(?ms)^# Glossary\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_INTENT_BLOCK = re.compile(r"(?ms)^# Intent\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_SCENARIOS_BLOCK = re.compile(r"(?ms)^# Scenarios\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_BACKTICK_TERM = re.compile(r"`([^`\n]+)`")
_CONTEXT_ANNOTATION = re.compile(r"^\[(?P<term>[^\]]+)\]\(context:\s*(?P<ctx>[^)]+)\)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*[:\-]+\s*(?:\|\s*[:\-]+\s*)+\|?\s*$")

_SEVERITY_RANK: dict[Severity, int] = {
    "BLOCK": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "WARN": 4,
    "INFO": 5,
}


def _load_state_tier(state_path: Path) -> str | None:
    text = _read_text(state_path)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    tier = payload.get("tier")
    return tier if isinstance(tier, str) else None


def _split_row(line: str) -> list[str] | None:
    """Split a markdown table row into trimmed cells.

    Returns None when the line is not a table row (no leading/trailing pipe
    pattern). Header separator rows are filtered upstream.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    # Strip leading + trailing pipe to avoid empty edge cells.
    inner = stripped.strip("|")
    return [cell.strip() for cell in inner.split("|")]


def _parse_term_cell(cell: str) -> tuple[str, str | None]:
    """Return ``(term, context_id_or_None)`` from a Term cell.

    Accepts either bare ``Order`` or annotated ``[Order](context: sales)``.
    """
    match = _CONTEXT_ANNOTATION.match(cell.strip())
    if match is None:
        return cell.strip(), None
    return match.group("term").strip(), match.group("ctx").strip()


def _extract_glossary_rows(domain_text: str) -> list[tuple[str, str | None, str, int]]:
    """Return ``(term, context_id, raw_term_cell, row_number)`` tuples.

    ``row_number`` is the 1-based index within the glossary slice (only rows
    that successfully parse into ≥2 cells contribute), used for stable
    ordering of malformed-row findings.
    """
    block = _GLOSSARY_BLOCK.search(domain_text)
    if block is None:
        return []
    rows: list[tuple[str, str | None, str, int]] = []
    for idx, line in enumerate(block.group("body").splitlines(), start=1):
        if not line.strip().startswith("|"):
            continue
        if _TABLE_SEPARATOR.match(line):
            continue
        cells = _split_row(line)
        if cells is None:
            continue
        # Skip header row: "| Term | Definition | Context | Invariants |".
        if len(cells) >= 1 and cells[0].lower() == "term":
            continue
        # Preserve raw row even when malformed so the caller can flag it.
        raw = cells[0] if cells else ""
        term, ctx = _parse_term_cell(raw) if raw else ("", None)
        rows.append((term, ctx, raw, idx))
    return rows


def _glossary_row_columns(domain_text: str) -> list[list[str]]:
    """Return cell-lists for every non-header, non-separator glossary row."""
    block = _GLOSSARY_BLOCK.search(domain_text)
    if block is None:
        return []
    out: list[list[str]] = []
    for line in block.group("body").splitlines():
        if not line.strip().startswith("|"):
            continue
        if _TABLE_SEPARATOR.match(line):
            continue
        cells = _split_row(line)
        if cells is None:
            continue
        if len(cells) >= 1 and cells[0].lower() == "term":
            continue
        out.append(cells)
    return out


def _extract_spec_terms(spec_text: str) -> set[str]:
    """Return the set of backtick-wrapped, capitalized SPEC terms.

    Restricts the scan to ``# Intent`` + ``# Scenarios`` sections after
    masking fenced code blocks (so fenced examples cannot inject false
    positives). Tokens are kept when they:

    - Start with an uppercase ASCII letter.
    - Contain only letters / digits / underscore / dash (i.e., not dotted
      module paths like ``module.Symbol``).

    Inline backticks are deliberately preserved here — the upstream
    ``_strip_code`` helper would erase the term markers we extract below.
    Per the locked plan, domain terms in SPEC.md are required to be
    backtick-wrapped (``Order``) so the validator can distinguish them
    from generic prose nouns; ``forge-domain`` instructs the author to
    backtick-wrap when authoring SPEC.md.
    """
    terms: set[str] = set()
    for pattern in (_INTENT_BLOCK, _SCENARIOS_BLOCK):
        match = pattern.search(spec_text)
        if match is None:
            continue
        slice_body = match.group("body")
        # Strip fenced code blocks but preserve inline backticks.
        slice_no_fences = re.sub(r"(?ms)^```.*?^```", "", slice_body)
        for term_match in _BACKTICK_TERM.finditer(slice_no_fences):
            token = term_match.group(1).strip()
            if not token:
                continue
            if not token[0].isalpha() or not token[0].isupper():
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]*", token):
                continue
            terms.add(token)
    return terms


def _read_domain_status(domain_text: str) -> str:
    """Return the DOMAIN.md frontmatter ``status`` field, lowercased.

    Returns ``"draft"`` when frontmatter is missing or malformed — the
    most permissive default, matching the lifecycle described in
    ``docs/plans/2026-05-08-m7-confidence-and-ux-polish.md`` P1.1.
    """
    match = _FRONTMATTER_RE.match(domain_text)
    if match is None:
        return "draft"
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return "draft"
    if not isinstance(parsed, dict):
        return "draft"
    raw = parsed.get("status")
    if not isinstance(raw, str):
        return "draft"
    return raw.strip().lower()


def _gated(severity: Severity, status: str) -> Severity:
    """Downgrade BLOCK to MEDIUM when ``status`` is draft/ready.

    Per the locked plan: validator activates BLOCK-level findings only
    when ``status == "locked"``. ``draft`` and ``ready`` emit advisory
    findings (MEDIUM/LOW). Non-BLOCK severities pass through unchanged.
    """
    if severity == "BLOCK" and status in _ADVISORY_STATUSES:
        return "MEDIUM"
    return severity


def _finding_sort_key(finding: Finding) -> tuple[int, str, str]:
    rank = _SEVERITY_RANK.get(finding.severity, 99)
    code = ""
    msg = finding.message
    prefix = f"{TARGET}:"
    if msg.startswith(prefix):
        rest = msg[len(prefix) :]
        code = rest.split(" ", 1)[0].rstrip(":—-")
    return (rank, code, msg)


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=_finding_sort_key)


def validate_domain_glossary(repo_root: Path, feature_id: str) -> list[Finding]:
    """Cross-reference DOMAIN.md glossary against SPEC.md domain terms.

    Args:
        repo_root: Repository root containing ``.forge/features/<feature_id>/``.
        feature_id: Slug folder name under ``.forge/features``.

    Returns:
        Sorted list of Finding records. Empty list means the feature's
        glossary cleanly resolves every domain-flavoured SPEC term, has no
        duplicates, and uses every entry it declares.
    """
    feature_dir = repo_root / ".forge" / "features" / feature_id
    if not feature_dir.is_dir():
        return [
            Finding(
                "BLOCK",
                TARGET,
                feature_dir,
                f"{TARGET}:feature_missing — {feature_dir} does not exist",
                fix_hint=(
                    f"Create the feature folder .forge/features/{feature_id}/ "
                    f"or correct the feature id passed to the validator."
                ),
            )
        ]

    domain_path = feature_dir / DOMAIN_FILENAME
    spec_path = feature_dir / SPEC_FILENAME
    state_path = feature_dir / STATE_FILENAME

    domain_text = _read_text(domain_path)
    if domain_text is None:
        tier = _load_state_tier(state_path)
        if tier == "full":
            return [
                Finding(
                    "BLOCK",
                    TARGET,
                    domain_path,
                    f"{TARGET}:domain_md_missing — full-tier feature is missing DOMAIN.md",
                    fix_hint=(
                        "Author DOMAIN.md from templates/feature/DOMAIN.md, or "
                        "downgrade the feature tier in state.json if it is not full."
                    ),
                )
            ]
        return []

    status = _read_domain_status(domain_text)

    spec_text = _read_text(spec_path) or ""
    spec_terms = _extract_spec_terms(spec_text)

    raw_rows = _glossary_row_columns(domain_text)
    findings: list[Finding] = []
    glossary_terms, malformed = _classify_rows(raw_rows, domain_path, status=status)
    findings.extend(malformed)
    findings.extend(_duplicate_findings(glossary_terms, domain_path, status=status))
    findings.extend(_orphan_findings(spec_terms, glossary_terms, spec_path, status=status))
    findings.extend(_unused_findings(spec_terms, glossary_terms, domain_path))
    findings.extend(_undefined_context_findings(raw_rows, glossary_terms, domain_path))

    return _sort_findings(findings)


def _classify_rows(
    raw_rows: list[list[str]],
    domain_path: Path,
    *,
    status: str,
) -> tuple[list[tuple[str, str | None]], list[Finding]]:
    """Split raw glossary rows into well-formed term tuples + malformed-row findings."""
    glossary_terms: list[tuple[str, str | None]] = []
    malformed: list[Finding] = []
    for cells in raw_rows:
        if len(cells) < _GLOSSARY_REQUIRED_COLUMNS or not cells[0].strip():
            malformed.append(
                Finding(
                    _gated("BLOCK", status),
                    TARGET,
                    domain_path,
                    f"{TARGET}:malformed_glossary_row — row has wrong column count or "
                    f"empty Term cell: {cells!r}",
                    fix_hint=(
                        "Fix the DOMAIN.md row to have all four cells "
                        "(Term | Definition | Context | Invariants)."
                    ),
                )
            )
            continue
        term, ctx = _parse_term_cell(cells[0])
        if not term:
            malformed.append(
                Finding(
                    _gated("BLOCK", status),
                    TARGET,
                    domain_path,
                    f"{TARGET}:malformed_glossary_row — empty Term cell in row {cells!r}",
                    fix_hint=("Populate the Term cell in the DOMAIN.md row, or delete the row."),
                )
            )
            continue
        glossary_terms.append((term, ctx))
    return glossary_terms, malformed


def _duplicate_findings(
    glossary_terms: list[tuple[str, str | None]],
    domain_path: Path,
    *,
    status: str,
) -> list[Finding]:
    seen: dict[str, int] = {}
    duplicates: set[str] = set()
    for term, _ctx in glossary_terms:
        key = term.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            duplicates.add(term)
    return [
        Finding(
            _gated("BLOCK", status),
            TARGET,
            domain_path,
            f"{TARGET}:duplicate_term — {term!r} appears more than once in the glossary",
            fix_hint=(
                f"Remove the duplicate row for {term!r} in DOMAIN.md or merge the "
                f"definitions into one row."
            ),
        )
        for term in sorted(duplicates)
    ]


def _orphan_findings(
    spec_terms: set[str],
    glossary_terms: list[tuple[str, str | None]],
    spec_path: Path,
    *,
    status: str,
) -> list[Finding]:
    glossary_lower = {term.lower() for term, _ in glossary_terms}
    orphans = sorted({t for t in spec_terms if t.lower() not in glossary_lower})
    return [
        Finding(
            _gated("BLOCK", status),
            TARGET,
            spec_path,
            f"{TARGET}:orphan_term — SPEC term {term!r} is not defined in DOMAIN.md glossary",
            fix_hint=(
                f"Add a glossary row for {term!r} in DOMAIN.md, or rephrase SPEC.md "
                f"to use a term already defined."
            ),
        )
        for term in orphans
    ]


def _unused_findings(
    spec_terms: set[str],
    glossary_terms: list[tuple[str, str | None]],
    domain_path: Path,
) -> list[Finding]:
    spec_lower = {t.lower() for t in spec_terms}
    unused = sorted({term for term, _ in glossary_terms if term.lower() not in spec_lower})
    return [
        Finding(
            "MEDIUM",
            TARGET,
            domain_path,
            f"{TARGET}:unused_glossary_entry — glossary term {term!r} is not referenced "
            f"in SPEC.md # Intent or # Scenarios",
        )
        for term in unused
    ]


def _undefined_context_findings(
    raw_rows: list[list[str]],
    glossary_terms: list[tuple[str, str | None]],
    domain_path: Path,
) -> list[Finding]:
    """LOW finding when an annotation references a ctx-id no other row anchors.

    The Context column is cells[2] (0=Term, 1=Definition, 2=Context, 3=Invariants).
    """
    context_anchors: set[str] = set()
    for cells in raw_rows:
        if len(cells) < _GLOSSARY_REQUIRED_COLUMNS or not cells[0].strip():
            continue
        ctx_value = cells[2].strip()
        if ctx_value and ctx_value not in _CONTEXT_PLACEHOLDERS:
            context_anchors.add(ctx_value)
    return [
        Finding(
            "LOW",
            TARGET,
            domain_path,
            f"{TARGET}:undefined_context — annotation on {term!r} references "
            f"context {ctx!r} which is not anchored by any glossary Context column",
        )
        for term, ctx in glossary_terms
        if ctx is not None and ctx not in context_anchors
    ]


__all__ = ["DOMAIN_FILENAME", "TARGET", "validate_domain_glossary"]
