"""Lock test: ``templates/feature/RESEARCH.md`` shape + degraded fragment.

The research-phase skill copies this template into a fresh feature folder
before dispatching the research subagent. The frontmatter, four section
headers, and the degraded-mode HTML-comment fragment are load-bearing
for downstream consumers (`tools.validate._research_shape`, the
forge-research subagent's degraded-mode copy-paste path).
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.validate._research_shape import validate_research

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_TEMPLATE: Path = _REPO_ROOT / "templates" / "feature" / "RESEARCH.md"


def test_template_exists() -> None:
    assert _TEMPLATE.is_file(), f"missing template: {_TEMPLATE.relative_to(_REPO_ROOT)}"


def test_template_frontmatter_keys_present() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "template must start with YAML frontmatter"
    fm_end = text.find("\n---\n", 4)
    assert fm_end > 0, "template frontmatter must be terminated"
    fm = text[4:fm_end]
    for key in ("spec:", "status:", "tier:", "parallel_used:", "research_grounding:"):
        assert key in fm, f"frontmatter missing required key: {key}"


def test_template_section_headers_present() -> None:
    body = _TEMPLATE.read_text(encoding="utf-8")
    for header in (
        "# Codebase findings",
        "# External docs",
        "# Domain notes",
        "# Risks surfaced",
    ):
        assert header in body, f"missing required section header: {header}"


def test_template_degraded_fragment_present() -> None:
    body = _TEMPLATE.read_text(encoding="utf-8")
    # The fragment lives inside an HTML comment so the as-shipped template
    # itself does not trip the degraded-marker validator gate.
    assert "<!--" in body and "-->" in body, "degraded fragment must be HTML-commented"
    assert "_Context7 not available" in body, "missing degraded marker line"
    assert "https://github.com/upstash/context7" in body, "missing Context7 install link"


def test_template_validates_when_populated_to_done(tmp_path: Path) -> None:
    """Filling the placeholders to a `done` shape must produce no BLOCK findings."""
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Strip the trailing degraded-mode HTML-comment fragment — leaving it in
    # would let the validator detect the marker even though we are exercising
    # the `full` path. The fragment's purpose is copy-paste guidance, not
    # part of the as-populated body.
    src = re.sub(r"\n<!--.*?-->\n?", "\n", src, flags=re.DOTALL)
    populated = (
        src.replace("<YYYY-MM-DD-slug>", "2026-05-11-fake-feature")
        .replace("<focused|standard|full>", "full")
        .replace("status: in_progress", "status: done")
        .replace("<full|degraded|websearch|byod|byod-partial>", "full")
    )
    target = tmp_path / "RESEARCH.md"
    target.write_text(populated, encoding="utf-8")

    findings = validate_research(target)
    blocking = [f for f in findings if f.severity == "BLOCK"]
    assert not blocking, f"populated template produced BLOCK findings: {blocking}"
