"""Regression: forge-do skill + /forge:do command shape parity.

Pins the locked contract for `/forge:do` adaptive routing introduced in
M3 P6.1 (focused + standard tiers) and extended in M3 P6.2 (full tier).
Parity is enforced via greppable substring assertions so future drift is
caught before merge.

Asserts:
1. SKILL.md frontmatter sets ``disable-model-invocation: true``.
2. SKILL.md frontmatter sets ``model: sonnet``.
3. SKILL.md body documents 11 numbered lifecycle steps.
4. SKILL.md prints the literal secrets warning before persisting
   ``routing.idea``.
5. SKILL.md no longer carries the P6.1 ``--full`` ``NotImplementedError``
   pointer (full-tier routing shipped in P6.2).
6. SKILL.md instructs the lightweight health preflight via
   ``python -m tools.validate --target health``.
7. Capability scan disambig prose mirrors ``forge-spec`` and never offers
   proceed-as-new.
8. SKILL.md calls ``tools.routing.seed_routed_feature(`` literally.
9. SKILL.md prints the locked focused/standard dispatch literal
   ``Next: /forge:spec --feature <feature_id>``.
10. SKILL.md cleanup hook references ``tools.archive.cleanup_seeded_feature``
    AND ``KeyboardInterrupt``.
11. commands/do.md ``argument-hint`` matches the refine-style convention.
12. commands/do.md no longer carries the ``--full raises`` P6.1 caveat.
13. SKILL.md self-review checklist covers the required state-shape
    invariants for both ``spec`` and ``refine`` seed phases.
14. SKILL.md Constitution preflight defaults to skip.
15. SKILL.md prints the locked full-tier dispatch literal
    ``Next: /forge:refine --feature <feature_id>``.
16. SKILL.md prose names ``state.json.current_phase`` as the
    tier-deterministic dispatch resolver.
17. SKILL.md self-review accepts ``current_phase ∈ {"spec", "refine"}``
    rather than hard-coding ``spec``.
18. SKILL.md LLM tier proposal prompt widens to all three tiers.
19. SKILL.md drops the P6.1 "refuse if LLM picks full" prose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-do" / "SKILL.md"
COMMAND_PATH = REPO / "commands" / "do.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Frontmatter: disable-model-invocation: true
# ---------------------------------------------------------------------------


def test_skill_frontmatter_disable_model_invocation() -> None:
    text = _read(SKILL_PATH)
    assert "disable-model-invocation: true" in text, (
        "SKILL.md frontmatter must set 'disable-model-invocation: true' so the "
        "skill is invoked only via /forge:do, matching the AGENTS.md "
        "'explicit' classification"
    )


# ---------------------------------------------------------------------------
# 2. Frontmatter: model: sonnet
# ---------------------------------------------------------------------------


def test_skill_frontmatter_model_sonnet() -> None:
    text = _read(SKILL_PATH)
    assert "model: sonnet" in text, "SKILL.md frontmatter must contain 'model: sonnet'"


# ---------------------------------------------------------------------------
# 3. Lifecycle: 11 numbered steps survive
# ---------------------------------------------------------------------------


def test_skill_steps_count_eleven() -> None:
    text = _read(SKILL_PATH)
    # Find the Steps section and count numbered list items inside it. The
    # plan locks the lifecycle at 11 steps; if a future edit drops or adds
    # one, this test surfaces it.
    matches = re.findall(r"\n(\d+)\.\s", text)
    numbered = [int(m) for m in matches]
    # The Steps section is the only sustained 1..N numbered list in the
    # SKILL.md body. Assert that 11 sequential step numbers appear.
    for expected in range(1, 12):
        assert expected in numbered, (
            f"SKILL.md must contain step {expected} of the 11-step lifecycle; "
            f"found numbered items {sorted(set(numbered))}"
        )


# ---------------------------------------------------------------------------
# 4. Secrets warning literal
# ---------------------------------------------------------------------------


def test_skill_secrets_warning_literal() -> None:
    text = _read(SKILL_PATH)
    expected = (
        "sensitive content (tokens, passwords) discouraged — "
        "text is persisted to state.json.routing.idea verbatim"
    )
    assert expected in text, (
        "SKILL.md must contain the exact secrets warning prose locked by "
        "AC #4 of the parity contract"
    )


# ---------------------------------------------------------------------------
# 5. --full raise pointer to P6.2 — REMOVED (P6.2 shipped the full tier)
# ---------------------------------------------------------------------------


def test_skill_full_tier_raise_pointer_removed() -> None:
    """The P6.1 raise pointer is gone now that P6.2 ships full-tier routing.

    The skill no longer raises ``NotImplementedError`` for ``--full``; the
    routing helper seeds ``current_phase="refine"`` and the skill dispatches
    to ``/forge:refine``.  Asserting the prose is ABSENT pins this lift so a
    future revert cannot silently re-introduce the raise without flipping
    this contract.
    """
    text = _read(SKILL_PATH)
    assert "--full routing ships in M3 P6.2" not in text, (
        "SKILL.md must NOT carry the P6.1 NotImplementedError pointer — "
        "full-tier routing shipped in P6.2 and the prose is now stale"
    )


# ---------------------------------------------------------------------------
# 6. Health preflight literal command
# ---------------------------------------------------------------------------


def test_skill_calls_health_preflight() -> None:
    text = _read(SKILL_PATH)
    assert "python -m tools.validate --target health" in text, (
        "SKILL.md must instruct running the lightweight health preflight via "
        "the canonical CLI subcommand"
    )


# ---------------------------------------------------------------------------
# 7. Capability scan disambig contract mirrors forge-spec
# ---------------------------------------------------------------------------


def test_skill_capability_scan_disambig_mirrors_forge_spec() -> None:
    text = _read(SKILL_PATH)
    assert "route to /forge:change for delta proposal" in text or (
        "route to `/forge:change` for delta proposal" in text
    ), "SKILL.md capability-scan prompt must offer /forge:change as a route"
    assert "disambiguating slug suffix" in text, (
        "SKILL.md capability-scan prompt must offer a disambiguating slug "
        "suffix path (mirrors forge-spec contract)"
    )
    # Proceed-as-new without a suffix MUST NOT be offered.
    lowered = text.lower()
    assert "proceed as new" not in lowered, (
        "SKILL.md must not offer proceed-as-new; suffix-disambig is the "
        "only escape hatch (mirrors forge-spec contract)"
    )
    assert "proceed-as-new" not in lowered, "SKILL.md must not offer proceed-as-new variant either"


# ---------------------------------------------------------------------------
# 8. Calls tools.routing.seed_routed_feature literal
# ---------------------------------------------------------------------------


def test_skill_calls_seed_routed_feature_literal() -> None:
    text = _read(SKILL_PATH)
    assert "tools.routing.seed_routed_feature(" in text, (
        "SKILL.md must invoke tools.routing.seed_routed_feature(...) as the "
        "single Python entry-point for the post-confirm half of /forge:do"
    )


# ---------------------------------------------------------------------------
# 9. Dispatch literal locked exactly
# ---------------------------------------------------------------------------


def test_skill_dispatch_literal() -> None:
    """Locked dispatch literal must appear as its own line in SKILL.md.

    Substring containment is too lax — a future edit could insert prose
    inside the literal (e.g. ``Next: /forge:spec --feature <feature_id>
    --debug``) and the substring check would still pass.  Anchor the
    assertion to a complete line via ``text.splitlines()`` after stripping
    leading whitespace and a single optional surrounding backtick (the
    literal is rendered as inline code in SKILL.md step 11).

    Locks remediation for M3 P6.1 T7 finding p6-1-L3.
    """
    text = _read(SKILL_PATH)
    expected = "Next: /forge:spec --feature <feature_id>"

    def _line_matches(raw: str) -> bool:
        # Strip indentation, then strip a single optional pair of surrounding
        # backticks that would render the literal as inline code in markdown.
        stripped = raw.strip()
        if stripped.startswith("`") and stripped.endswith("`"):
            stripped = stripped[1:-1]
        return stripped == expected

    matching = [line for line in text.splitlines() if _line_matches(line)]
    assert matching, (
        f"SKILL.md must print the locked dispatch literal {expected!r} as its "
        f"own line (optionally inline-coded with backticks); no exact-line "
        f"match found"
    )
    # Sanity-check substring still present so the migration doesn't break
    # other readers that grep the file.
    assert expected in text, "SKILL.md must still contain the dispatch literal as a substring"


# ---------------------------------------------------------------------------
# 10. Cleanup hook on KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_skill_cleanup_hook_calls_cleanup_seeded_feature() -> None:
    text = _read(SKILL_PATH)
    assert "tools.archive.cleanup_seeded_feature" in text, (
        "SKILL.md must reference tools.archive.cleanup_seeded_feature in the UI-cancel cleanup hook"
    )
    assert "KeyboardInterrupt" in text, (
        "SKILL.md must mention KeyboardInterrupt as a trigger for the cleanup hook"
    )


# ---------------------------------------------------------------------------
# 11. commands/do.md argument-hint matches refine convention
# ---------------------------------------------------------------------------


def test_command_argument_hint_matches_refine_convention() -> None:
    text = _read(COMMAND_PATH)
    assert 'argument-hint: "<idea> [--focused | --standard | --full]"' in text, (
        "commands/do.md must declare the locked argument-hint exactly"
    )


# ---------------------------------------------------------------------------
# 12. commands/do.md drops the P6.1 --full raises caveat
# ---------------------------------------------------------------------------


def test_command_drops_full_raises_caveat() -> None:
    """P6.2 lifted the ``--full`` ``NotImplementedError``; the caveat is gone.

    The command file MUST still mention ``--full`` (the flag is documented in
    the args + argument-hint), but the P6.1 caveat prose ("raises
    NotImplementedError until P6.2", the "## --full caveat (until M3 P6.2)"
    section, etc.) MUST be absent.
    """
    text = _read(COMMAND_PATH)
    assert "--full" in text, "commands/do.md must still mention --full as a valid tier flag"
    assert "NotImplementedError" not in text, (
        "commands/do.md must NOT carry the P6.1 NotImplementedError caveat — "
        "full-tier routing shipped in P6.2"
    )
    assert "until M3 P6.2" not in text, (
        "commands/do.md must NOT carry the 'until M3 P6.2' caveat — full-tier "
        "routing shipped in P6.2"
    )
    assert "--full caveat" not in text, (
        "commands/do.md must NOT retain the '## --full caveat' section header"
    )


# ---------------------------------------------------------------------------
# 13. Self-review checklist (five invariants)
# ---------------------------------------------------------------------------


def test_skill_self_review_checklist_present() -> None:
    text = _read(SKILL_PATH)
    # P6.2 widened the phase invariant from hard-coded ``spec`` to the
    # tier-deterministic union ``{spec, refine}``; the per-phase status
    # check generalizes to ``phases.<current_phase>.status``.
    expected_substrings = [
        'current_phase ∈ {"spec", "refine"}',
        "research",
        "routing",
        "state.json",
        "SPEC.md",
        "decisions.md",
    ]
    for sub in expected_substrings:
        assert sub in text, (
            f"SKILL.md self-review checklist must reference '{sub}' so the "
            "skill verifies state shape before dispatch"
        )


# ---------------------------------------------------------------------------
# 14. Constitution preflight defaults to skip
# ---------------------------------------------------------------------------


def test_skill_constitution_preflight_default_skip() -> None:
    text = _read(SKILL_PATH)
    assert "default = skip" in text, (
        "SKILL.md Constitution preflight must document the default = skip "
        "behavior so the agent doesn't bootstrap on every fresh project"
    )


# ---------------------------------------------------------------------------
# 14b. feature_slug carried into seed_routed_feature for suffix-disambig
# ---------------------------------------------------------------------------


def test_skill_carries_feature_slug_into_seed_call() -> None:
    """Suffix-disambig contract — chosen slug must reach the seeder.

    The skill MUST instruct the LLM to pass the disambiguated slug as the
    ``feature_slug=`` argument to ``tools.routing.seed_routed_feature`` so
    ``idea`` (and therefore ``state.json.routing.idea``) is preserved
    verbatim.  Pre-fix the skill silently relied on re-deriving the slug
    from ``idea``, forcing operators to mutate ``idea`` text and corrupt
    the audit record.

    Locks remediation for the external review finding on
    ``skills/forge-do/SKILL.md:54`` + ``tools/routing.py:140``.
    """
    text = _read(SKILL_PATH)
    assert "feature_slug=" in text, (
        "SKILL.md must thread the disambiguated slug as `feature_slug=` to "
        "tools.routing.seed_routed_feature so the operator's chosen suffix "
        "flows into feature_id while idea remains unchanged"
    )
    # Audit-record protection prose must appear so a future edit cannot
    # silently regress the contract back to "edit idea text".
    lowered = text.lower()
    assert "audit record" in lowered, (
        "SKILL.md must explain why the audit record matters — operators "
        "MUST NOT bake the suffix into idea text"
    )


# ---------------------------------------------------------------------------
# 15. Cleanup caveat — decisions.md edits NOT preserved on cancel
# ---------------------------------------------------------------------------


def test_skill_cleanup_caveat_decisions_md_lost() -> None:
    """Step 9 cleanup is filename-based, not content-based.

    User edits to ``decisions.md`` between seed and cancel are silently lost
    because the cleanup predicate only checks that folder contents are a
    strict subset of the orphan-allowed file set; content of those files is
    never inspected.  The SKILL.md MUST surface this caveat so users know
    to commit decisions worth keeping before cancelling.

    Locks remediation for M3 P6.1 T7 finding p6-1-M2.
    """
    text = _read(SKILL_PATH)
    assert "filename-based, not content-based" in text, (
        "SKILL.md step 9 must spell out that cleanup is filename-based, "
        "not content-based, so users understand decisions.md edits are lost"
    )
    assert "decisions.md" in text and "silently lost" in text, (
        "SKILL.md step 9 must explicitly warn that user edits to "
        "decisions.md are silently lost on cancel-cleanup"
    )
    assert "commit it before cancelling" in text or "commit" in text, (
        "SKILL.md step 9 must instruct users to commit before cancelling "
        "if they want decisions preserved"
    )


# ---------------------------------------------------------------------------
# 16. Full-tier dispatch literal — Next: /forge:refine --feature <feature_id>
# ---------------------------------------------------------------------------


def test_skill_full_tier_dispatch_literal() -> None:
    """P6.2 full-tier dispatch literal must appear as its own line.

    Mirrors the focused/standard line-anchored assertion: the literal must
    appear as a complete line (optionally inline-coded with backticks), not
    merely as a substring inside a sentence.  Substring containment is too
    lax — a future edit could insert prose inside the literal and the
    substring check would still pass.
    """
    text = _read(SKILL_PATH)
    expected = "Next: /forge:refine --feature <feature_id>"

    def _line_matches(raw: str) -> bool:
        stripped = raw.strip()
        if stripped.startswith("`") and stripped.endswith("`"):
            stripped = stripped[1:-1]
        return stripped == expected

    matching = [line for line in text.splitlines() if _line_matches(line)]
    assert matching, (
        f"SKILL.md must print the locked full-tier dispatch literal "
        f"{expected!r} as its own line (optionally inline-coded with "
        f"backticks); no exact-line match found"
    )
    # Anchored regex check via re.MULTILINE (covers the optionally-backticked
    # form too) so the parity test surfaces a structurally-anchored match.
    pattern = re.compile(
        r"^[ \t`]*Next: /forge:refine --feature <feature_id>[ \t`]*$", re.MULTILINE
    )
    assert pattern.search(text), (
        "SKILL.md must contain at least one anchored line matching the full-tier dispatch literal"
    )


# ---------------------------------------------------------------------------
# 17. Dispatch resolves by state.json.current_phase
# ---------------------------------------------------------------------------


def test_skill_dispatch_resolves_by_current_phase() -> None:
    """The skill must name ``state.json.current_phase`` as the resolver.

    Tier-deterministic dispatch is the P6.2 contract: the dispatch literal
    is chosen by reading ``state.json.current_phase`` after the seed (``spec``
    → ``/forge:spec``; ``refine`` → ``/forge:refine``).  The skill prose
    MUST name this resolver explicitly so a future edit cannot silently
    regress to a flag-based or LLM-proposal-based dispatch.
    """
    text = _read(SKILL_PATH)
    assert "state.json.current_phase" in text, (
        "SKILL.md must name `state.json.current_phase` as the tier-deterministic dispatch resolver"
    )


# ---------------------------------------------------------------------------
# 18. Self-review accepts current_phase ∈ {"spec", "refine"}
# ---------------------------------------------------------------------------


def test_skill_full_tier_self_review_accepts_refine_phase() -> None:
    """The self-review checklist must accept refine as a valid seed phase.

    P6.1 hard-coded ``current_phase == "spec"``.  P6.2 widens to
    ``current_phase ∈ {"spec", "refine"}`` so full-tier seeds (which seed
    ``current_phase="refine"``) survive the self-review without false
    positives.  The hard-coded ``current_phase == "spec"`` line MUST be
    gone so the checklist generalizes correctly.
    """
    text = _read(SKILL_PATH)
    assert 'current_phase ∈ {"spec", "refine"}' in text, (
        "SKILL.md self-review must mention current_phase ∈ {'spec', 'refine'} "
        "to accept both focused/standard (spec) and full (refine) seed phases"
    )
    # Hard-coded spec-only assertion must be gone so the checklist does not
    # falsely fail on full-tier seeds.
    assert 'current_phase == "spec"' not in text, (
        "SKILL.md self-review must NOT hard-code current_phase == 'spec'; "
        "the P6.2 widening replaces it with the {'spec', 'refine'} union"
    )


# ---------------------------------------------------------------------------
# 19. LLM proposal widens to focused/standard/full
# ---------------------------------------------------------------------------


def test_skill_llm_proposal_widens_to_focused_standard_full() -> None:
    """The step-5 LLM prompt must include all three tiers.

    P6.1 limited the proposal space to focused/standard.  P6.2 widens to
    focused/standard/full so the LLM can propose any tier and the override
    flag (when supplied) still wins at step 7.
    """
    text = _read(SKILL_PATH)
    assert "focused/standard/full" in text, (
        "SKILL.md step-5 LLM prompt must enumerate all three tiers "
        "(focused/standard/full) — the P6.2 widening removes the "
        "P6.1 focused/standard-only restriction"
    )


# ---------------------------------------------------------------------------
# 20. Drop the P6.1 "refuse if LLM picks full" prose
# ---------------------------------------------------------------------------


def test_skill_drops_full_excluded_refusal_prose() -> None:
    """The P6.1 hallucination-refusal prose is gone in P6.2.

    P6.1 instructed the skill to refuse if the LLM picked ``full`` despite
    the prompt excluding it.  P6.2 makes ``full`` a legitimate proposal
    target, so the refusal prose ("If the LLM hallucinates `full` ...") MUST
    be absent.  Pinning the absence prevents a future edit from silently
    re-introducing the refusal and breaking the LLM-proposes-full path.
    """
    text = _read(SKILL_PATH)
    lowered = text.lower()
    assert "hallucinates" not in lowered, (
        "SKILL.md must NOT carry the P6.1 'LLM hallucinates full' refusal "
        "prose — full is a legitimate proposal target in P6.2"
    )
    assert "p6.1 excludes full" not in lowered, (
        "SKILL.md must NOT carry the P6.1 'excludes full from the proposal "
        "space' restriction — P6.2 widens the proposal space"
    )


# ---------------------------------------------------------------------------
# 21. Confirm-UI prose enumerates --full as a valid override
# ---------------------------------------------------------------------------


def test_skill_confirm_ui_lists_full_as_override() -> None:
    """Step-6 confirm-UI prose must list ``--full`` alongside ``--focused`` /
    ``--standard`` as a valid override.

    P6.2 made ``full`` a legitimate proposal and override target. If the
    confirm-UI guidance enumerates only ``--focused`` / ``--standard``, an
    LLM-proposes-standard path silently blocks the user from overriding to
    full at confirm time. This test pins the three-tier override list so a
    future edit cannot drop ``--full`` again.
    """
    text = _read(SKILL_PATH)
    assert "`--focused` / `--standard` / `--full`" in text, (
        "SKILL.md step 6 must enumerate `--focused` / `--standard` / `--full` "
        "as the override flag list — P6.2 made full a legitimate override target"
    )


def test_command_confirm_ui_lists_full_as_override() -> None:
    """commands/do.md step 6 must list ``--full`` alongside ``--focused`` /
    ``--standard`` as a valid override.

    Mirrors the SKILL.md parity assertion: command-file prose is the
    surface a user grepping for valid flags hits first, so it must stay
    in sync with the skill body and the documented argument-hint.
    """
    text = _read(COMMAND_PATH)
    assert "`--focused` / `--standard` / `--full`" in text, (
        "commands/do.md step 6 must enumerate `--focused` / `--standard` / `--full` "
        "as the override flag list — P6.2 made full a legitimate override target"
    )


# ---------------------------------------------------------------------------
# M6 finding M4: SKILL + command reject multi-flag input at parse time
# ---------------------------------------------------------------------------


def test_do_skill_rejects_multi_flag_input() -> None:
    """SKILL.md step 1 must explicitly reject multi-flag input
    (e.g. ``--focused --standard`` simultaneously) with a literal abort
    message naming the constraint ``Pass at most one of --focused /
    --standard / --full``. Without this rule the LLM has no documented
    behavior for multi-flag input and may pick one silently or seed
    twice.
    """
    text = _read(SKILL_PATH)
    assert "Pass at most one of --focused / --standard / --full" in text, (
        "SKILL.md must carry the literal multi-flag rejection prose "
        "'Pass at most one of --focused / --standard / --full'"
    )


def test_do_command_rejects_multi_flag_input() -> None:
    """commands/do.md must mirror the SKILL multi-flag rejection prose
    so users grepping the command file see the same constraint.
    """
    text = _read(COMMAND_PATH)
    assert "Pass at most one of --focused / --standard / --full" in text, (
        "commands/do.md must mirror the literal multi-flag rejection prose "
        "'Pass at most one of --focused / --standard / --full'"
    )
