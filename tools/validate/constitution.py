"""Constitution structural validator (M3 §5.3.6 D-CONSTITUTION)."""

from __future__ import annotations

import re
from pathlib import Path

from ._finding import Finding
from ._frontmatter import (
    _build_validator,
    _load_schema,
    _parse_frontmatter_or_finding,
    _read_text,
    _strip_code,
)

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


def _check_article_numbering(article_numbers: list[int], path: Path) -> list[Finding]:
    """Verify constitution article numbers are unique and monotonic from 1.

    Reports duplicate article numbers separately from gaps so authors can fix
    the right problem (renumber the duplicate vs fill the gap). Resyncs the
    expected counter after each gap so a single missing article fires one
    finding, not one per subsequent article.
    """
    findings: list[Finding] = []
    expected = 1
    seen: set[int] = set()
    for number in article_numbers:
        if number in seen:
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"duplicate article number {number}; renumber the second occurrence",
                ),
            )
        elif number != expected:
            findings.append(
                Finding(
                    "BLOCK",
                    "constitution",
                    path,
                    f"article numbers not monotonic: expected {expected}, found {number}",
                ),
            )
            expected = number
        seen.add(number)
        expected += 1
    return findings


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

    # Strip fenced + inline code so authoring examples (e.g. "paste this template:
    # ## Article N — Title [LEVEL]") inside code blocks are not mistaken for real
    # articles. _strip_code preserves byte offsets, so any future line-number
    # reporting still maps to the original file.
    body = _strip_code(body)

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

    findings.extend(_check_article_numbering(article_numbers, path))

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
