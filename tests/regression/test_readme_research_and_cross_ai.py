"""README surfaces research phase + cross-AI peer review user-facing prose.

Locks:
- "## Research phase" section exists.
- "## Cross-AI peer review" section exists.
- Both sections appear in the table of contents.
- Research section names all five grounding modes + the BYOD pattern + the
  ecosystem-detector pluggability.
- Cross-AI section documents manual default + auto opt-in + redaction +
  the dispatch-approval cache field name + cost-warn threshold field name.
- No internal milestone/phase shorthand in the new sections.
- No bare ``M[0-9]+`` / ``P[0-6]`` / ``milestone`` / ``finding-N`` tokens
  anywhere in the README body prose (fenced code blocks are exempt so
  language identifiers like ``python`` and class names survive).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"

_FORBIDDEN_LABELS = ("M8", "P0", "P1", "P2", "P3", "P4", "P5", "P6", "milestone")

_GROUNDING_MODES = ("full", "degraded", "websearch", "byod", "byod-partial")

# Patterns scanned across the README body prose (fenced code blocks
# exempted via ``_strip_fenced_code``). The patterns intentionally use
# word boundaries so ``M_PI``-style identifiers do not false-trip.
_BODY_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Mn token", re.compile(r"\bM\d+\b")),
    ("Pn token", re.compile(r"\bP[0-6](?:\.[0-9]+)?\b")),
    ("milestone token", re.compile(r"\bmilestone\b", re.IGNORECASE)),
    ("finding-N token", re.compile(r"\bfinding-\d+\b", re.IGNORECASE)),
)


def _read() -> str:
    return README.read_text(encoding="utf-8")


def _section(text: str, header: str) -> str:
    """Return the body of the section starting at ``header`` up to the next ``## ``."""
    start = text.find(header)
    assert start != -1, f"README missing section header {header!r}"
    after = text[start + len(header) :]
    next_header = after.find("\n## ")
    if next_header == -1:
        return after
    return after[:next_header]


def test_research_section_exists() -> None:
    assert "## Research phase" in _read()


def test_cross_ai_section_exists() -> None:
    assert "## Cross-AI peer review" in _read()


def test_toc_lists_research_and_cross_ai_anchors() -> None:
    text = _read()
    assert "[Research phase](#research-phase)" in text
    assert "[Cross-AI peer review](#cross-ai-peer-review)" in text


def test_research_section_names_all_five_grounding_modes() -> None:
    body = _section(_read(), "## Research phase")
    for mode in _GROUNDING_MODES:
        assert mode in body, f"Research section missing grounding mode {mode!r}"


def test_research_section_documents_byod_pattern() -> None:
    body = _section(_read(), "## Research phase")
    assert "BYOD" in body or "bring-your-own-docs" in body
    assert ".forge/external-docs/" in body


def test_research_section_documents_pluggable_ecosystems() -> None:
    body = _section(_read(), "## Research phase")
    assert "pluggable" in body or "Ecosystem detection" in body
    assert "Python" in body
    assert "Node" in body


def test_cross_ai_section_documents_manual_default_and_auto_optin() -> None:
    body = _section(_read(), "## Cross-AI peer review")
    assert "--cross-ai" in body
    assert "--auto" in body
    assert "manual" in body.lower()
    assert "--cross-ai-paste" in body


def test_cross_ai_section_documents_dispatch_approval_and_cost_warn() -> None:
    body = _section(_read(), "## Cross-AI peer review")
    assert "dispatch_approved_at" in body
    assert "cost_warn_threshold_usd" in body
    assert "APPROVE" in body
    assert "APPROVE-COST" in body


def test_cross_ai_section_documents_redaction_surface() -> None:
    body = _section(_read(), "## Cross-AI peer review")
    assert "redaction" in body.lower()
    assert "deny_globs" in body
    assert "fatal_regex" in body


def test_no_internal_phase_labels_in_new_sections() -> None:
    text = _read()
    for header in ("## Research phase", "## Cross-AI peer review"):
        body = _section(text, header)
        for forbidden in _FORBIDDEN_LABELS:
            assert forbidden not in body, (
                f"README section {header!r} contains forbidden internal label {forbidden!r}; "
                "user-facing docs describe behavior, not internal milestones."
            )


def _strip_fenced_code(text: str) -> str:
    """Drop content inside ``` ``` ``` ``` blocks so code samples are exempt.

    Splits on a triple-backtick line and keeps every other chunk (the chunks
    OUTSIDE fenced blocks). Fence open/close lines themselves are dropped.
    Mirrors the coarse-but-bounded approach used by
    ``tests/regression/test_no_mcp_imports.py::_strip_triple_quoted`` so the
    two sweep helpers stay parallel for future maintainers.
    """
    parts: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            parts.append(line)
    return "\n".join(parts)


def test_readme_has_no_internal_milestone_or_phase_labels() -> None:
    """Body prose (everything outside fenced code blocks) must be free of
    the planning-doc shorthand the project enforces in commit messages.

    The user-facing README describes behavior; ``M8`` / ``P3.4`` / "milestone"
    /  ``finding-12`` belong in the planning directory and PR descriptions.
    """
    body = _strip_fenced_code(_read())
    failures: list[str] = []
    for label, pattern in _BODY_FORBIDDEN_PATTERNS:
        for match in pattern.finditer(body):
            # Locate the line number the match falls on so the failure
            # message points the reader at the offending prose immediately.
            line_no = body.count("\n", 0, match.start()) + 1
            failures.append(f"line {line_no}: {label} {match.group(0)!r}")
    assert not failures, (
        "README body prose contains internal planning labels:\n  "
        + "\n  ".join(failures)
        + "\nFenced code blocks are exempt; rewrite body prose to describe behavior."
    )
