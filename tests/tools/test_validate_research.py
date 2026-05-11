"""Tests for validate_research (RESEARCH.md shape + citation rule)."""

from __future__ import annotations

from pathlib import Path

from tools.validate import Finding
from tools.validate._research_shape import validate_research

_FRONTMATTER_FULL = """---
spec: 2026-05-11-example-feature
status: done
tier: standard
research_grounding: full
---
"""

_FRONTMATTER_IN_PROGRESS = """---
spec: 2026-05-11-example-feature
status: in_progress
tier: focused
research_grounding: full
---
"""

_FRONTMATTER_DEGRADED = """---
spec: 2026-05-11-example-feature
status: done
tier: focused
research_grounding: degraded
---
"""

_FRONTMATTER_BYOD_PARTIAL = """---
spec: 2026-05-11-example-feature
status: done
tier: standard
research_grounding: byod-partial
---
"""

_REQUIRED_SECTIONS = (
    "# Codebase findings",
    "# External docs",
    "# Domain notes",
    "# Risks surfaced",
)


def _body_with_sections(external_paragraph: str = "") -> str:
    parts = []
    for section in _REQUIRED_SECTIONS:
        parts.append(section)
        parts.append("")
        if section == "# External docs" and external_paragraph:
            parts.append(external_paragraph)
            parts.append("")
        else:
            parts.append("Body text.")
            parts.append("")
    return "\n".join(parts)


def test_happy_full_grounding_no_findings(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    body = _body_with_sections(
        "The `pydantic.BaseModel` validator accepts dicts. [context7:/pydantic/pydantic:abc123]"
    )
    research.write_text(_FRONTMATTER_FULL + body, encoding="utf-8")

    findings = validate_research(research)
    assert findings == [], findings


def test_in_progress_relaxed_skips_section_check(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    # Only frontmatter; no body. Status = in_progress => no section check.
    research.write_text(_FRONTMATTER_IN_PROGRESS + "\nDraft content.\n", encoding="utf-8")

    findings = validate_research(research)
    assert findings == [], findings


def test_missing_required_frontmatter_field_blocks(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    bad = """---
spec: 2026-05-11-example-feature
status: done
research_grounding: full
---
"""
    research.write_text(bad + _body_with_sections(), encoding="utf-8")

    findings = validate_research(research)
    assert any(f.severity == "BLOCK" and "tier" in f.message.lower() for f in findings), findings


def test_missing_section_blocks_when_done(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    body = "# Codebase findings\n\nx\n\n# External docs\n\nx\n\n# Risks surfaced\n\nx\n"
    research.write_text(_FRONTMATTER_FULL + body, encoding="utf-8")

    findings = validate_research(research)
    assert any(f.severity == "BLOCK" and "domain notes" in f.message.lower() for f in findings), (
        findings
    )


def test_degraded_without_marker_blocks(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    research.write_text(_FRONTMATTER_DEGRADED + _body_with_sections(), encoding="utf-8")

    findings = validate_research(research)
    assert any(
        f.severity == "BLOCK" and "context7 not available" in f.message.lower() for f in findings
    ), findings


def test_degraded_with_marker_passes(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    body = _body_with_sections() + "\n\n_Context7 not available_ — fell back to local notes.\n"
    research.write_text(_FRONTMATTER_DEGRADED + body, encoding="utf-8")

    findings = validate_research(research)
    assert findings == [], findings


def test_full_grounding_uncited_symbol_paragraph_warns(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    body = _body_with_sections(
        "The `pydantic.BaseModel` validator accepts dicts but cite is missing."
    )
    research.write_text(_FRONTMATTER_FULL + body, encoding="utf-8")

    findings = validate_research(research)
    warns = [f for f in findings if f.severity == "WARN"]
    assert warns, findings
    assert any("citation" in f.message.lower() for f in warns), warns


def test_byod_partial_uncovered_warns(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    body = _body_with_sections(
        "The `requests.Session` API call requires auth. No citation, no marker."
    )
    research.write_text(_FRONTMATTER_BYOD_PARTIAL + body, encoding="utf-8")

    findings = validate_research(research)
    warns = [f for f in findings if f.severity == "WARN"]
    assert warns, findings
    assert any("requests" in f.message.lower() for f in warns), warns


def test_missing_file_blocks(tmp_path: Path) -> None:
    findings = validate_research(tmp_path / "absent.md")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)


def test_returns_list_of_findings(tmp_path: Path) -> None:
    research = tmp_path / "RESEARCH.md"
    research.write_text(_FRONTMATTER_FULL + _body_with_sections(), encoding="utf-8")

    findings = validate_research(research)
    assert isinstance(findings, list)
    for finding in findings:
        assert isinstance(finding, Finding)


def _stage_feature_with_byod(
    tmp_path: Path,
    *,
    covered_libs: tuple[str, ...] = (),
) -> Path:
    """Lay down a ``.forge/features/<id>/RESEARCH.md`` plus optional BYOD docs.

    Returns the RESEARCH.md path. The validator walks up from the
    research file looking for a ``.forge`` ancestor; this fixture
    creates the canonical layout so coverage resolution exercises the
    real file-system path.
    """
    forge = tmp_path / ".forge"
    feature = forge / "features" / "2026-05-11-example-feature"
    feature.mkdir(parents=True)
    if covered_libs:
        byod = forge / "external-docs"
        byod.mkdir(parents=True)
        for lib in covered_libs:
            (byod / f"{lib}.md").write_text("doc", encoding="utf-8")
    return feature / "RESEARCH.md"


def test_byod_partial_covered_library_citation_passes(tmp_path: Path) -> None:
    """A ``[byod:<covered>:...]`` cite satisfies a byod-partial paragraph."""
    research = _stage_feature_with_byod(tmp_path, covered_libs=("httpx",))
    body = _body_with_sections("About httpx: call `Client.send` to send. [byod:httpx:client]")
    research.write_text(_FRONTMATTER_BYOD_PARTIAL + body, encoding="utf-8")

    findings = validate_research(research)
    citation_warns = [f for f in findings if "missing citation" in f.message.lower()]
    assert citation_warns == [], findings


def test_byod_partial_flags_only_uncovered_paragraphs(tmp_path: Path) -> None:
    """Covered libs satisfy; uncovered libs surface as uncovered + missing."""
    research = _stage_feature_with_byod(tmp_path, covered_libs=("httpx",))
    body = _body_with_sections(
        "About httpx: call `Client.send`. [byod:httpx:client]\n\n"
        "About pydantic: use `pydantic.BaseModel` for validation."
    )
    research.write_text(_FRONTMATTER_BYOD_PARTIAL + body, encoding="utf-8")

    findings = validate_research(research)
    uncovered_warns = [f for f in findings if "not covered by staged byod" in f.message.lower()]
    assert any("pydantic" in f.message.lower() for f in uncovered_warns), findings
    assert not any("httpx" in f.message.lower() for f in uncovered_warns), findings


def test_byod_partial_hyphen_underscore_canonicalization(tmp_path: Path) -> None:
    """A BYOD file ``my-lib.md`` matches a cite for ``my_lib`` (canonical form)."""
    research = _stage_feature_with_byod(tmp_path, covered_libs=("my-lib",))
    body = _body_with_sections("Use `my_lib.connect` for setup. [byod:my_lib:connect]")
    research.write_text(_FRONTMATTER_BYOD_PARTIAL + body, encoding="utf-8")

    findings = validate_research(research)
    citation_warns = [f for f in findings if "missing citation" in f.message.lower()]
    assert citation_warns == [], findings


def test_degraded_marker_inside_html_comment_blocks(tmp_path: Path) -> None:
    """The template ships the marker inside an HTML comment.

    Authors who copy the template verbatim without replacing the
    External docs section must still BLOCK — the marker only counts
    when it lives in the visible body.
    """
    research = tmp_path / "RESEARCH.md"
    body = (
        "# Codebase findings\n\nx\n\n"
        "# External docs\n\n"
        "<!--\n"
        "_Context7 not available — research ran in **degraded** mode._\n"
        "-->\n\n"
        "# Domain notes\n\nx\n\n# Risks surfaced\n\nx\n"
    )
    research.write_text(_FRONTMATTER_DEGRADED + body, encoding="utf-8")

    findings = validate_research(research)
    assert any(
        f.severity == "BLOCK" and "context7 not available" in f.message.lower() for f in findings
    ), findings
