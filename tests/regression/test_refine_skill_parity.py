"""Regression: forge-refine skill + /forge:refine command shape parity.

Asserts:
1. skills/forge-refine/SKILL.md exists.
2. SKILL.md frontmatter contains name, description, and model: sonnet lines.
3. SKILL.md body references the state helpers
   (``tools.state.increment_refine_attempts`` and
   ``tools.state.record_refined_idea``).
4. SKILL.md body documents the phase transition (``complete_phase``,
   ``start_phase``, ``refine``, ``spec``).
5. SKILL.md body documents the 5-round cap.
6. SKILL.md body documents deviation handling (``decisions.md`` and
   ``deviations``).
7. SKILL.md body lists ``routing.idea`` as the input source.
8. SKILL.md body points to ``/forge:spec`` as the next phase.
9. commands/refine.md exists.
10. commands/refine.md frontmatter contains an ``argument-hint:`` pointing at
    ``--feature``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-refine" / "SKILL.md"
COMMAND_PATH = REPO / "commands" / "refine.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Skill file exists
# ---------------------------------------------------------------------------


def test_forge_refine_skill_exists() -> None:
    assert SKILL_PATH.exists(), f"Expected {SKILL_PATH} to exist"


# ---------------------------------------------------------------------------
# 2. Skill frontmatter tokens
# ---------------------------------------------------------------------------


def test_refine_skill_frontmatter_has_name_description_model() -> None:
    text = _read(SKILL_PATH)
    assert "name: forge-refine" in text, "SKILL.md frontmatter must contain 'name: forge-refine'"
    assert "description:" in text, "SKILL.md frontmatter must contain 'description:' line"
    assert "model: sonnet" in text, "SKILL.md frontmatter must contain 'model: sonnet'"


# ---------------------------------------------------------------------------
# 3. Body references state helpers
# ---------------------------------------------------------------------------


def test_refine_skill_references_state_helpers() -> None:
    text = _read(SKILL_PATH)
    assert "tools.state.increment_refine_attempts" in text, (
        "SKILL.md must reference tools.state.increment_refine_attempts"
    )
    assert "tools.state.record_refined_idea" in text, (
        "SKILL.md must reference tools.state.record_refined_idea"
    )


# ---------------------------------------------------------------------------
# 4. Body documents phase transition
# ---------------------------------------------------------------------------


def test_refine_skill_documents_phase_transition() -> None:
    text = _read(SKILL_PATH)
    assert "complete_phase" in text, "SKILL.md must reference complete_phase"
    assert "start_phase" in text, "SKILL.md must reference start_phase"
    assert "refine" in text, "SKILL.md must mention 'refine' phase"
    assert "spec" in text, "SKILL.md must mention 'spec' phase"


# ---------------------------------------------------------------------------
# 5. Body documents the 5-round cap
# ---------------------------------------------------------------------------


def test_refine_skill_documents_round_cap() -> None:
    text = _read(SKILL_PATH)
    assert re.search(r"5 rounds", text, re.IGNORECASE), (
        "SKILL.md must mention '5 rounds' (the Socratic loop cap)"
    )


# ---------------------------------------------------------------------------
# 6. Body documents deviation handling
# ---------------------------------------------------------------------------


def test_refine_skill_documents_deviation_handling() -> None:
    text = _read(SKILL_PATH)
    assert "decisions.md" in text, "SKILL.md must reference decisions.md for round-cap deviation"
    assert "deviations" in text, "SKILL.md must reference state.json.deviations append on round-cap"


# ---------------------------------------------------------------------------
# 7. Body lists routing.idea as input source
# ---------------------------------------------------------------------------


def test_refine_skill_lists_routing_idea_input() -> None:
    text = _read(SKILL_PATH)
    assert "routing.idea" in text, (
        "SKILL.md must document state.json.routing.idea as the seeded input"
    )


# ---------------------------------------------------------------------------
# 8. Body points to /forge:spec
# ---------------------------------------------------------------------------


def test_refine_skill_points_to_next_command() -> None:
    text = _read(SKILL_PATH)
    assert "/forge:spec" in text, "SKILL.md must point to /forge:spec as the next-phase command"


# ---------------------------------------------------------------------------
# 9. Command file exists
# ---------------------------------------------------------------------------


def test_refine_command_exists() -> None:
    assert COMMAND_PATH.exists(), f"Expected {COMMAND_PATH} to exist"


# ---------------------------------------------------------------------------
# 10. Command frontmatter contains argument-hint with --feature
# ---------------------------------------------------------------------------


def test_refine_command_frontmatter_has_argument_hint() -> None:
    text = _read(COMMAND_PATH)
    assert "argument-hint:" in text, (
        "commands/refine.md frontmatter must contain 'argument-hint:' line"
    )
    assert "--feature" in text, (
        "commands/refine.md frontmatter argument-hint must mention '--feature'"
    )


# ---------------------------------------------------------------------------
# 11. Skill is marked explicit (disable-model-invocation: true)
# ---------------------------------------------------------------------------


def test_refine_skill_disable_model_invocation_true() -> None:
    """AGENTS.md classifies forge-refine as 'explicit' = disable-model-invocation: true.
    Without this, Claude Code may auto-load the skill on description match,
    contradicting the documented command-only phase model.
    """
    text = _read(SKILL_PATH)
    assert "disable-model-invocation: true" in text, (
        "SKILL.md frontmatter must set 'disable-model-invocation: true' so the "
        "skill is invoked only via /forge:refine, matching the AGENTS.md "
        "'explicit' classification"
    )


# ---------------------------------------------------------------------------
# 12. Command argument-hint accepts optional <idea> (plan T2/T3)
# ---------------------------------------------------------------------------


def test_refine_command_argument_hint_includes_idea() -> None:
    """Plan T2 specifies `/forge:refine [--feature <id>] [<idea>]`.

    The CLI ``<idea>`` arg backs the direct-invocation fallback path
    (re-running refine on an existing feature whose ``current_phase`` is
    already ``refine``). The canonical entry path is ``/forge:do --full``,
    but the positional must remain available for standalone invocation.
    """
    text = _read(COMMAND_PATH)
    assert "[<idea>]" in text, (
        "commands/refine.md argument-hint must accept optional `<idea>` per plan T2"
    )


# ---------------------------------------------------------------------------
# 13. Skill body documents CLI idea fallback path
# ---------------------------------------------------------------------------


def test_refine_skill_documents_cli_idea_fallback() -> None:
    text = _read(SKILL_PATH)
    assert "record_routing_decision" in text, (
        "SKILL.md must instruct seeding routing via record_routing_decision when "
        "routing.idea is absent and CLI <idea> was passed"
    )
    assert "ignore" in text.lower() or "ignored" in text.lower(), (
        "SKILL.md must say CLI <idea> is ignored when routing.idea is already set "
        "(routing is the canonical record)"
    )


# ---------------------------------------------------------------------------
# 14. Skill names /forge:do --full as the canonical entry path
# ---------------------------------------------------------------------------


def test_refine_skill_names_canonical_entry_path() -> None:
    """SKILL.md must name ``/forge:do --full`` as the canonical entry path.

    Replaces the P6.1 'bootstrap caveat (until P6.2)' assertion: now that
    P6.2 has shipped, the only invariant left is that the skill points
    callers at ``/forge:do --full`` rather than at hand-bootstrapping a
    refine-tier feature folder.
    """
    text = _read(SKILL_PATH)
    assert "/forge:do --full" in text, (
        "SKILL.md must name `/forge:do --full` as the canonical entry path"
    )
    assert "canonical entry" in text.lower(), (
        "SKILL.md must label `/forge:do --full` as the 'canonical entry' path"
    )


# ---------------------------------------------------------------------------
# 15. P6.2 T4: pre-seed predicate four conjuncts present
# ---------------------------------------------------------------------------


def test_refine_pre_seed_predicate_four_conjuncts_present() -> None:
    """All four conjuncts of the pre-seed predicate must appear in the
    SKILL.md prose so the contract is locked: ``--feature <id>`` resolved,
    ``state.json`` parses, ``routing`` block present, AND
    ``current_phase == "refine"`` AND
    ``phases.refine.status == "in_progress"``.
    """
    text = _read(SKILL_PATH)
    assert "--feature <id>" in text, (
        "SKILL.md must name the `--feature <id>` resolved conjunct of the pre-seed predicate"
    )
    assert "state.json parses" in text, (
        "SKILL.md must name the `state.json parses` conjunct of the pre-seed predicate"
    )
    assert "routing" in text and "block" in text, (
        "SKILL.md must name the `routing` block conjunct of the pre-seed predicate"
    )
    assert 'current_phase == "refine"' in text, (
        'SKILL.md must name the `current_phase == "refine"` conjunct of the pre-seed predicate'
    )
    assert 'phases.refine.status == "in_progress"' in text, (
        'SKILL.md must name the `phases.refine.status == "in_progress"` '
        "conjunct of the pre-seed predicate"
    )
    lowered = text.lower()
    assert ("all four" in lowered) or ("all must hold" in lowered), (
        "SKILL.md must explicitly state ALL four conjuncts are required for "
        "the pre-seed branch (not any one of them)"
    )


# ---------------------------------------------------------------------------
# 16. P6.2 T4: pre-seed branch skips record_routing_decision
# ---------------------------------------------------------------------------


def test_refine_pre_seed_branch_skips_record_routing_decision() -> None:
    """The pre-seed branch must explicitly tell the LLM NOT to call
    ``tools.state.record_routing_decision`` again — the routing block is
    already populated by ``/forge:do --full``, and re-calling would
    clobber the seed ``decided_at`` timestamp.
    """
    text = _read(SKILL_PATH)
    assert "record_routing_decision" in text, (
        "SKILL.md must mention `record_routing_decision` so the skip instruction can refer to it"
    )
    lowered = text.lower()
    assert (
        ("does not call" in lowered)
        or ("do not call" in lowered)
        or ("not call" in lowered)
        or ("does **not**" in lowered)
    ), "SKILL.md must instruct the LLM not to call `record_routing_decision` in the pre-seed branch"


# ---------------------------------------------------------------------------
# 17. P6.2 T4: direct-invocation fallback retained
# ---------------------------------------------------------------------------


def test_refine_direct_invocation_fallback_retained() -> None:
    """The direct-invocation fallback (existing M3 P4 behavior) must still
    be documented — when the pre-seed predicate fails, prose still
    describes the path that seeds routing via
    ``record_routing_decision(... final_tier="full" ...)`` from the CLI
    ``<idea>`` argument.
    """
    text = _read(SKILL_PATH)
    assert "direct-invocation" in text.lower() or "direct invocation" in text.lower(), (
        "SKILL.md must label the no-pre-seed path as the 'direct-invocation' fallback branch"
    )
    assert 'final_tier="full"' in text, (
        'SKILL.md must keep the `final_tier="full"` argument in the '
        "direct-invocation fallback's `record_routing_decision` call"
    )
    assert "direct /forge:refine invocation" in text, (
        "SKILL.md must keep the documented rationale string for the "
        "direct-invocation fallback's routing seed"
    )


# ---------------------------------------------------------------------------
# 18. P6.2 T4: bootstrap caveat removed; /forge:do --full canonical entry
# ---------------------------------------------------------------------------


def test_refine_bootstrap_caveat_removed() -> None:
    """The 'Bootstrap caveat (until M3 P6.2 ships ...)' prose must be GONE
    from SKILL.md, and ``/forge:do --full`` must be named as the
    canonical entry path.
    """
    text = _read(SKILL_PATH)
    assert "Bootstrap caveat (until M3 P6.2 ships" not in text, (
        "SKILL.md must drop the 'Bootstrap caveat (until M3 P6.2 ships' "
        "section header — P6.2 is shipped now"
    )
    assert "until M3 P6.2" not in text, (
        "SKILL.md must drop any 'until M3 P6.2' wording — P6.2 is shipped now"
    )
    assert "/forge:do --full" in text, (
        "SKILL.md must name `/forge:do --full` as the canonical entry path"
    )
    assert "canonical entry" in text.lower(), (
        "SKILL.md must explicitly call `/forge:do --full` the 'canonical entry' path"
    )


# ---------------------------------------------------------------------------
# 19. P6.2 T4: command file drops bootstrap caveat
# ---------------------------------------------------------------------------


def test_refine_command_drops_bootstrap_caveat() -> None:
    """commands/refine.md must no longer carry the 'Bootstrap caveat
    (until M3 P6.2)' section header, and the 'until M3 P6.2' wording
    must be gone from the command file too.
    """
    text = _read(COMMAND_PATH)
    assert "Bootstrap caveat (until M3 P6.2)" not in text, (
        "commands/refine.md must drop the '## Bootstrap caveat (until M3 P6.2)' "
        "section header — P6.2 is shipped now"
    )
    assert "until M3 P6.2" not in text, (
        "commands/refine.md must drop any 'until M3 P6.2' wording — P6.2 is shipped now"
    )
    assert "/forge:do --full" in text, (
        "commands/refine.md must still mention `/forge:do --full` as the canonical entry path"
    )


# ---------------------------------------------------------------------------
# 20. P6.2 T4: pre-seed clobber rationale documented
# ---------------------------------------------------------------------------


def test_refine_pre_seed_does_not_clobber_decided_at() -> None:
    """SKILL.md must explain WHY the pre-seed branch skips
    ``record_routing_decision`` — re-calling it would clobber the seed
    ``decided_at`` timestamp written by ``/forge:do --full``.
    """
    text = _read(SKILL_PATH)
    assert "decided_at" in text, (
        "SKILL.md must mention `decided_at` when explaining the "
        "pre-seed branch's skip-record_routing_decision rationale"
    )
    assert "clobber" in text.lower(), (
        "SKILL.md must call out that re-calling `record_routing_decision` "
        "would clobber the seed `decided_at` timestamp"
    )


# ---------------------------------------------------------------------------
# 21. M6 finding M2: guard_refine_entry called BEFORE any state mutation
# ---------------------------------------------------------------------------


def test_refine_skill_calls_guard_refine_entry_before_record_routing() -> None:
    """The tier+phase guard helper ``guard_refine_entry`` must appear in
    SKILL.md prose AND must be referenced inside the ``## Steps`` section
    BEFORE the first ``record_routing_decision`` call inside the same
    Steps section, so an LLM following steps in order cannot write a
    routing block before the guard fires (M6 M2).
    """
    text = _read(SKILL_PATH)
    assert "guard_refine_entry" in text, (
        "SKILL.md must reference tools.state.guard_refine_entry as the tier+phase preflight helper"
    )
    steps_idx = text.index("## Steps")
    steps_body = text[steps_idx:]
    guard_idx = steps_body.index("guard_refine_entry")
    record_idx = steps_body.index("record_routing_decision")
    assert guard_idx < record_idx, (
        "SKILL.md ## Steps section must mention guard_refine_entry BEFORE "
        "record_routing_decision so the guard fires before any state mutation"
    )
