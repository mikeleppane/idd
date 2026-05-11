"""Lock test: ``skills/forge-research/SKILL.md`` prose contract.

The research-phase skill is the entry point a phase-routing tool dispatches
to when a feature reaches `current_phase == "research"`. Its prose carries
several load-bearing contracts:

* the ten-step lifecycle,
* the dispatch budget that abstracts manifest filenames behind
  ``tools.research.ecosystem.detect()``,
* the degraded-mode failure mode that explains the
  ``_Context7 not available_`` marker,
* the allowed-tools list that authorizes the subagent to invoke the
  Context7 MCP tools and WebSearch.

Drift in any of these would silently weaken the contract — this lock
catches it at CI time.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SKILL: Path = _REPO_ROOT / "skills" / "forge-research" / "SKILL.md"


def _frontmatter_block(text: str) -> str:
    assert text.startswith("---\n"), "skill must start with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end > 0, "skill frontmatter must be terminated"
    return text[4:end]


def test_skill_file_exists() -> None:
    assert _SKILL.is_file(), f"missing skill file: {_SKILL.relative_to(_REPO_ROOT)}"


def test_frontmatter_locks() -> None:
    fm = _frontmatter_block(_SKILL.read_text(encoding="utf-8"))
    assert "name: forge-research" in fm, "frontmatter missing `name: forge-research`"
    assert "disable-model-invocation: true" in fm, (
        "frontmatter missing `disable-model-invocation: true`"
    )
    for tool in (
        "Task",
        "mcp__context7__resolve-library-id",
        "mcp__context7__query-docs",
        "WebSearch",
    ):
        assert tool in fm, f"allowed-tools missing required entry: {tool}"


def test_steps_one_through_ten_present() -> None:
    body = _SKILL.read_text(encoding="utf-8")
    expected = (
        ("### 1.", "Validate state"),
        ("### 2.", "Constitution preflight"),
        ("### 3.", "Initialize RESEARCH.md"),
        ("### 4.", "Dispatch ONE research subagent"),
        ("### 5.", "Subagent runs"),
        ("### 6.", "Subagent writes RESEARCH.md"),
        ("### 7.", "Resolve grounding mode"),
        ("### 8.", "Self-review"),
        ("### 9.", "Transition phase"),
        ("### 10.", "Surface to user"),
    )
    for header, key_phrase in expected:
        assert header in body, f"missing step header: {header}"
        assert key_phrase in body, f"missing step key phrase: {key_phrase}"


def test_budget_block_uses_detector_abstraction() -> None:
    body = _SKILL.read_text(encoding="utf-8")
    assert "tools.research.ecosystem.detect()" in body, (
        "budget block must reference the detector abstraction"
    )
    # Outside the abstraction reference, no raw manifest name is allowed in the
    # skill prose. The full hardcoded-manifest sweep lives in
    # `tests/regression/test_no_hardcoded_manifests.py`; we spot-check the two
    # most common offenders here so a future edit that drops the abstraction
    # fails this test directly.
    for token in ("pyproject.toml", "package.json"):
        assert token not in body, (
            f"skill prose must not name `{token}` directly — route through "
            "`tools.research.ecosystem.detect()`"
        )


def test_degraded_mode_failure_documented() -> None:
    body = _SKILL.read_text(encoding="utf-8")
    assert "Context7 absent" in body, "missing Context7-absent failure-mode entry"
    assert "_Context7 not available_" in body, (
        "skill must reference the literal degraded-marker string"
    )


def test_no_milestone_or_finding_refs() -> None:
    """Forbid internal milestone/phase/finding-id markers in user-facing prose.

    The literal section name ``# Codebase findings`` (defined by the spec)
    and the validator ``Finding`` dataclass term are technical — the ban is
    specifically on internal milestone-tracking references like ``M8``,
    ``P4``, ``finding #15``, or ``milestone N``.
    """
    text = _SKILL.read_text(encoding="utf-8")
    forbidden_patterns = (
        r"\bM[0-9]+\b",  # M0..M99 milestone markers
        r"\bP[0-6](?:\.[0-9]+)?\b",  # P0..P6 phase markers (optionally P4.6)
        r"finding\s*#",  # `finding #N` cross-reference style
        r"\bmilestone\b",
    )
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text, flags=re.IGNORECASE), (
            f"skill prose contains forbidden internal reference matching: {pattern}"
        )
