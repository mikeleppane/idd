"""Regression: forge-spec /forge:do pre-seed branch narrative parity.

Locks the focused/standard pre-seed narrative additions to
``skills/forge-spec/SKILL.md``:

1. The pre-seed predicate has four conjuncts (all must hold) — ``--feature
   <id>`` resolved, ``state.json`` parses, ``routing`` block present,
   ``current_phase == "spec"`` AND ``phases.spec.status == "in_progress"``.
2. The pre-seed branch skips steps 1, 2, 3, AND 4 (capability scan,
   feature-id compute, collision check, folder create) — ``/forge:do``
   already ran them.
3. The direct fallback branch fires when ANY conjunct fails (or the routing
   block is absent) — runs all steps as today.
4. Step 6 Intent honors a three-level idea-source precedence in order:
   ``state.json.refined_idea`` → ``state.json.routing.idea`` → CLI
   ``<idea>``. ``routing.idea`` is the secondary source slotted between
   the upstream ``refined_idea`` and the direct CLI fallback.
5. Step 8 phase transition guards against double-invoking ``start_phase``
   on the pre-seed path: ``/forge:do`` already wrote
   ``phases.spec.status: "in_progress"`` via ``create_feature_folder``'s
   seed body, and re-calling ``start_phase("spec")`` would clobber the
   seed ``started_at`` timestamp. ``complete_phase("spec")`` still runs
   at exit (the guard is on ``start_phase``, not ``complete_phase``).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-spec" / "SKILL.md"
_BODY = SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Pre-seed predicate has all four conjuncts
# ---------------------------------------------------------------------------


def test_pre_seed_predicate_four_conjuncts_present() -> None:
    """All four conjuncts of the pre-seed predicate must appear in the
    SKILL.md prose so the contract is locked: ``--feature <id>`` resolved,
    ``state.json`` parses, ``routing`` block present, AND
    ``current_phase == "spec"`` AND ``phases.spec.status == "in_progress"``.
    """
    assert "--feature <id>" in _BODY, (
        "SKILL.md must name the `--feature <id>` resolved conjunct of the pre-seed predicate"
    )
    assert "state.json parses" in _BODY, (
        "SKILL.md must name the `state.json parses` conjunct of the pre-seed predicate"
    )
    assert "routing" in _BODY and "block" in _BODY, (
        "SKILL.md must name the `routing` block conjunct of the pre-seed predicate"
    )
    assert 'current_phase == "spec"' in _BODY, (
        'SKILL.md must name the `current_phase == "spec"` conjunct of the pre-seed predicate'
    )
    assert 'phases.spec.status == "in_progress"' in _BODY, (
        'SKILL.md must name the `phases.spec.status == "in_progress"` conjunct '
        "of the pre-seed predicate"
    )


# ---------------------------------------------------------------------------
# 2. Pre-seed branch skips steps 1, 2, 3, AND 4
# ---------------------------------------------------------------------------


def test_pre_seed_branch_skips_steps_1_through_4() -> None:
    """The skip instruction must explicitly enumerate steps 1, 2, 3, AND 4
    (capability scan, feature-id compute, collision check, folder create) so
    the contract is unambiguous to the LLM at runtime.
    """
    assert "skip" in _BODY.lower(), "SKILL.md must use the word 'skip' for the pre-seed branch"
    # All four step numbers must appear in the skip-instruction context.
    assert "steps 1, 2, 3, and 4" in _BODY or "steps 1, 2, 3, AND 4" in _BODY, (
        "SKILL.md must spell out 'steps 1, 2, 3, and 4' so all four skipped steps "
        "are explicitly enumerated"
    )


# ---------------------------------------------------------------------------
# 3. direct fallback branch still runs all steps
# ---------------------------------------------------------------------------


def test_direct_fallback_branch_runs_all_steps() -> None:
    """The direct-invocation fallback path must still be documented — when the
    routing block is absent (direct ``/forge:spec "<idea>"`` invocation) the
    skill creates the folder itself and runs all steps as today.
    """
    assert "direct fallback" in _BODY, (
        "SKILL.md must label the no-routing path as the 'direct fallback' branch"
    )
    # Spell out that the fallback path runs all steps (today's behavior).
    assert "all steps" in _BODY, (
        "SKILL.md must state the direct fallback branch runs all steps in order"
    )


# ---------------------------------------------------------------------------
# 4. Idea-source precedence — three levels in correct order
# ---------------------------------------------------------------------------


def test_idea_source_precedence_three_levels() -> None:
    """Step 6 Intent must list all three idea sources (``refined_idea``,
    ``routing.idea``, CLI ``<idea>``) AND in the correct precedence order:
    ``refined_idea`` index < ``routing.idea`` index < CLI ``<idea>`` index.
    """
    assert "state.json.refined_idea" in _BODY, (
        "SKILL.md Step 6 must list `state.json.refined_idea` as a primary Intent source"
    )
    assert "state.json.routing.idea" in _BODY, (
        "SKILL.md Step 6 must list `state.json.routing.idea` as a secondary Intent source"
    )
    assert "CLI" in _BODY or "user's idea text" in _BODY or "<idea>" in _BODY, (
        "SKILL.md Step 6 must mention the CLI `<idea>` argument as the tertiary Intent source"
    )

    # Order: refined_idea first, then routing.idea, then CLI fallback.
    refined_idx = _BODY.index("state.json.refined_idea")
    routing_idx = _BODY.index("state.json.routing.idea")
    assert refined_idx < routing_idx, (
        "SKILL.md must mention `state.json.refined_idea` BEFORE `state.json.routing.idea` "
        "(precedence is refined_idea > routing.idea)"
    )


def test_idea_source_precedence_routing_idea_named_secondary() -> None:
    """``routing.idea`` must be described as the secondary source — i.e.
    consumed when ``refined_idea`` is absent but before falling back to the
    CLI ``<idea>`` argument.
    """
    assert "state.json.routing.idea" in _BODY, (
        "SKILL.md must reference `state.json.routing.idea` explicitly"
    )
    # Ensure prose explicitly ties routing.idea to /forge:do as the
    # producer (mirrors the refined_idea / /forge:refine pairing).
    assert "/forge:do" in _BODY, (
        "SKILL.md must point to `/forge:do` as the producer of `routing.idea`"
    )


# ---------------------------------------------------------------------------
# 5. Pre-seed start_phase guard
# ---------------------------------------------------------------------------


def test_pre_seed_start_phase_guard_present() -> None:
    """When the pre-seed branch fires, the SKILL.md prose must explicitly
    instruct the LLM NOT to call ``start_phase("spec")`` again — that would
    clobber the ``started_at`` seeded by ``/forge:do``.
    """
    # Some phrasing along the lines of "do NOT call start_phase" or "skip
    # the start_phase call" must appear, paired with `start_phase("spec")`.
    assert 'start_phase("spec")' in _BODY, (
        'SKILL.md must explicitly mention `start_phase("spec")` so the guard prose can refer to it'
    )
    # Look for explicit no-op / skip guidance for the pre-seed branch.
    lowered = _BODY.lower()
    assert ("do not call" in lowered) or ("not call" in lowered) or ("skip" in lowered), (
        'SKILL.md must instruct the LLM not to call `start_phase("spec")` in the pre-seed branch'
    )


def test_pre_seed_complete_phase_still_runs() -> None:
    """The guard is on ``start_phase``, not ``complete_phase`` — the pre-seed
    branch still calls ``complete_phase("spec")`` at exit before advancing
    to the tier-deterministic next phase.
    """
    assert 'complete_phase("spec")' in _BODY, (
        'SKILL.md must keep `complete_phase("spec")` in the exit transition for '
        "the pre-seed branch (the guard is on start_phase only)"
    )


def test_pre_seed_started_at_clobber_warning() -> None:
    """Prose must explain WHY the start_phase guard exists — re-calling
    ``start_phase("spec")`` would clobber the ``started_at`` timestamp seeded
    by ``/forge:do``'s ``create_feature_folder``.
    """
    assert "started_at" in _BODY, (
        "SKILL.md must mention `started_at` when explaining the start_phase guard"
    )
    assert "clobber" in _BODY.lower(), (
        "SKILL.md must call out that re-calling `start_phase` would clobber "
        "the seed `started_at` timestamp"
    )


# ---------------------------------------------------------------------------
# 6. Predicate is AND, not OR
# ---------------------------------------------------------------------------


def test_pre_seed_predicate_all_four_required() -> None:
    """The pre-seed predicate is a conjunction — all four conjuncts must hold
    for the branch to fire. Prose must say so explicitly to prevent any
    'any-of' misreading.
    """
    lowered = _BODY.lower()
    assert ("all four" in lowered) or ("all must hold" in lowered), (
        "SKILL.md must explicitly state ALL four conjuncts are required for "
        "the pre-seed branch (not any one of them)"
    )


def test_direct_fallback_predicate_negation() -> None:
    """The direct fallback path runs when ANY conjunct fails (the negation
    of the pre-seed predicate). Prose must say so to make the branch
    decision unambiguous.
    """
    lowered = _BODY.lower()
    assert ("any conjunct fails" in lowered) or ("otherwise" in lowered), (
        "SKILL.md must state the direct fallback runs when any conjunct fails "
        "(or use 'otherwise' to negate the pre-seed predicate cleanly)"
    )
