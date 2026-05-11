"""Contract test for the forge-spec headless-mode refusal prose.

When forge-spec runs in a non-interactive session (claude -p, no TTY,
agent-driven loop with no user-input channel), step 6's "asking only
when ambiguous" interactive Q&A contract cannot be satisfied. The
skill must refuse with a clear message rather than silently abort
mid-refinement and leave state.json in a half-mutated shape.

The refusal prose is consumed by an LLM, so the test pins it as a
static contract: the skill body must carry both the trigger condition
and the verbatim message the user sees.
"""

from __future__ import annotations

from pathlib import Path

_VERBATIM = (
    "Spec refinement requires interactive Q&A and the current session is "
    "headless (claude -p / no TTY). Re-run /forge:spec --feature <id> in an "
    "interactive session, or pre-answer gaps inline in the prompt then "
    "re-invoke. The skill will exit without mutating state.json."
)


def test_forge_spec_skill_carries_headless_refusal_banner(repo_root: Path) -> None:
    """forge-spec SKILL.md must carry the headless-refusal contract."""
    body = (repo_root / "skills" / "forge-spec" / "SKILL.md").read_text(encoding="utf-8")

    assert "Headless-mode refusal" in body, (
        "skills/forge-spec/SKILL.md must surface a Headless-mode refusal banner "
        "near the top of the skill so the agent sees it before step 6"
    )
    assert _VERBATIM in body, (
        "skills/forge-spec/SKILL.md must carry the verbatim refusal message "
        "the agent prints when interactive Q&A is unavailable"
    )
    assert "Do NOT call `complete_phase" in body or "Do NOT call complete_phase" in body, (
        "skills/forge-spec/SKILL.md must explicitly forbid completing the spec "
        "phase from the headless-refusal branch"
    )
