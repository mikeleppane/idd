"""Regression: forge-do skill + /forge:do command shape parity.

Pins the locked contract for `/forge:do` adaptive routing introduced in
M3 P6.1 (focused + standard tiers). Parity is enforced via greppable
substring assertions so future drift is caught before merge.

Asserts:
1. SKILL.md frontmatter sets ``disable-model-invocation: true``.
2. SKILL.md frontmatter sets ``model: sonnet``.
3. SKILL.md body documents 11 numbered lifecycle steps.
4. SKILL.md prints the literal secrets warning before persisting
   ``routing.idea``.
5. SKILL.md surfaces the ``--full`` ``NotImplementedError`` with the P6.2
   pointer.
6. SKILL.md instructs the lightweight health preflight via
   ``python -m tools.validate --target health``.
7. Capability scan disambig prose mirrors ``forge-spec`` and never offers
   proceed-as-new.
8. SKILL.md calls ``tools.routing.seed_routed_feature(`` literally.
9. SKILL.md prints the locked dispatch literal
   ``Next: /forge:spec --feature <feature_id>``.
10. SKILL.md cleanup hook references ``tools.archive.cleanup_seeded_feature``
    AND ``KeyboardInterrupt``.
11. commands/do.md ``argument-hint`` matches the refine-style convention.
12. commands/do.md documents the ``--full`` P6.2 caveat.
13. SKILL.md self-review checklist covers the five required state-shape
    invariants.
14. SKILL.md Constitution preflight defaults to skip.
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
# 5. --full raise pointer to P6.2
# ---------------------------------------------------------------------------


def test_skill_full_tier_raise_pointer() -> None:
    text = _read(SKILL_PATH)
    assert "--full routing ships in M3 P6.2" in text, (
        "SKILL.md must surface the literal NotImplementedError message that "
        "points users at the P6.2 plan"
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
# 12. commands/do.md documents --full P6.2 caveat
# ---------------------------------------------------------------------------


def test_command_full_tier_caveat_present() -> None:
    text = _read(COMMAND_PATH)
    assert "--full" in text, "commands/do.md must mention --full"
    assert "P6.2" in text, "commands/do.md must mention the P6.2 caveat for --full"


# ---------------------------------------------------------------------------
# 13. Self-review checklist (five invariants)
# ---------------------------------------------------------------------------


def test_skill_self_review_checklist_present() -> None:
    text = _read(SKILL_PATH)
    expected_substrings = [
        'current_phase == "spec"',
        'phases.spec.status == "in_progress"',
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
