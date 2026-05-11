"""Regression: forge-spec skill research-aware prelude prose locks.

The skill must prepend a RESEARCH.md excerpt to the spec context when
the upstream research phase landed ``status: "done"`` and log the
carry-over line when it landed ``status: "skipped"``. Locks the
skill prose so a future edit cannot silently drop the prelude (which
would re-orphan research from spec authoring) or change the carry-over
wording (which downstream telemetry parsers may key on).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-spec" / "SKILL.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skill_documents_research_aware_prelude() -> None:
    """SKILL.md must carry a Research-aware prelude step header.

    The prelude is the bridge between the research phase artifact and
    spec authoring; without an explicit step the LLM may forget to
    consult RESEARCH.md and re-derive context from scratch.
    """
    text = _read(SKILL_PATH)
    assert "Research-aware prelude" in text, (
        "SKILL.md must label the prelude step explicitly so the LLM "
        "knows to consult RESEARCH.md before drafting the spec"
    )


def test_skill_prelude_reads_research_phase_status() -> None:
    """The prelude must key off ``state.json.phases.research`` status."""
    text = _read(SKILL_PATH)
    assert "state.json.phases.research" in text, (
        "SKILL.md prelude must read state.json.phases.research to "
        "decide between excerpt / carry-over / no-op branches"
    )


def test_skill_prelude_done_branch_locks_excerpt_shape() -> None:
    """Done branch must lock the excerpt cap + paragraph-boundary truncation."""
    text = _read(SKILL_PATH)
    assert "RESEARCH.md" in text
    # Cap is documented in chars (≈1500 tokens at 4 chars/token).
    assert "6000 chars" in text
    # Paragraph-boundary truncation rule documented literally so the
    # truncation point cannot drift to mid-sentence on a future edit.
    assert "paragraph boundary" in text
    # Header carries the grounding mode read from RESEARCH.md frontmatter
    # so the spec context block tells readers which fallback ran.
    assert "## Research excerpt (mode: <research_grounding>)" in text


def test_skill_prelude_skipped_branch_logs_carry_over_literal() -> None:
    """Skipped branch must log the locked carry-over literal."""
    text = _read(SKILL_PATH)
    expected = "Research skipped: <reason>; spec proceeds without external grounding excerpt."
    assert expected in text, (
        f"SKILL.md prelude must log the carry-over literal {expected!r} "
        "verbatim — downstream telemetry may key on the wording"
    )


def test_skill_prelude_absent_branch_is_no_op() -> None:
    """Legacy / focused features without ``phases.research`` get no-op.

    Without an explicit no-op clause the LLM may invent a fallback (e.g.
    grep the repo for research artifacts) that could mutate state or
    surface spurious warnings on focused features.
    """
    text = _read(SKILL_PATH)
    assert "no-op" in text, (
        "SKILL.md prelude must document the no-op branch for features "
        "that never ran research (legacy or focused tier)"
    )


def test_skill_prelude_is_read_only() -> None:
    """The prelude must be read-only — it must not mutate state.json."""
    text = _read(SKILL_PATH)
    assert "read-only" in text, (
        "SKILL.md prelude must declare itself read-only so a future "
        "edit cannot quietly add a state.json mutation that would "
        "race with the spec phase's own writes"
    )
