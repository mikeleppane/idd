"""Capstone smoke test for the full-tier lifecycle walk through research.

End-to-end deterministic walk over every phase boundary in the full-tier
pipeline as seeded by ``/forge:do --full``. No live LLM, no slash-command
runtime, no user dialogue — just the pure state-machine helpers
(:mod:`tools.state` + :mod:`tools.routing`) the SKILL prose contracts
against.

Pins:

  * Full-tier seed lands at ``current_phase="refine"`` with the explicit
    11-entry pre-v3 ``routing.phase_list`` (refine → research → spec →
    domain → scenarios → plan → crucible → review → execute → verify →
    ship).
  * Every boundary's :func:`tools.state.next_phase_command` returns the
    documented slash literal — including the ``research`` slot inserted
    between ``refine`` and ``spec``.
  * The dual ``review`` pass (target=plan after crucible, target=code
    after execute) advances cleanly via
    :func:`tools.state.set_review_target` +
    :func:`tools.state.complete_review_target`; the lone ``review`` slot
    holds ``targets_done == [plan, code]`` before
    :func:`tools.state.complete_phase` accepts the close.
  * Ship completion stamps ``shipped_at``; :func:`tools.state.migrate_to_v3`
    bumps the feature into the post-merge lane and seeds the ``qa``
    phase so the operator can advance into ``/forge:qa`` and walk the
    final terminal-None boundary.
  * ``routing.phase_list`` survives every transition, contains
    ``research`` between ``refine`` and ``spec``, has unique entries, and
    matches the per-flow-version length (11 entries pre-v3 / 12 with the
    trailing ``qa`` once migrated; the seed locks the list at v1/v2
    length and migration leaves the explicit list untouched).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from tools.routing import seed_routed_feature
from tools.state import (
    complete_phase,
    complete_review_target,
    finish_feature,
    get_phase_list,
    migrate_to_v3,
    next_phase_command,
    read_state,
    record_refined_idea,
    set_review_target,
    start_phase,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"

TODAY = date(2026, 5, 8)


def _stage_repo(tmp_path: Path) -> Path:
    """Stage a repo_root under ``tmp_path`` with the live schema in place.

    ``seed_routed_feature`` resolves ``schema_path`` as
    ``repo_root / "schemas/state.schema.json"`` (see ``tools/routing.py``),
    so the live schema must be copied into the staged tree.  Templates are
    read via ``tools.archive._FEATURE_TEMPLATES_DIR`` (an absolute path
    resolved at import time), so they do NOT need to be duplicated.
    """
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "state.schema.json").write_text(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def _advance(state_path: Path, *, done: str, nxt: str, expected_next_command: str) -> None:
    """Complete ``done``, start ``nxt``, assert ``next_phase_command`` matches."""
    complete_phase(state_path, done, schema_path=SCHEMA_PATH)
    start_phase(state_path, nxt, schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == nxt
    assert next_phase_command(payload) == expected_next_command


def _walk_review_target(state_path: Path, *, target: str) -> None:
    """Open the review slot, set + complete the given target."""
    set_review_target(state_path, review_target=target, schema_path=SCHEMA_PATH)
    complete_review_target(state_path, review_target=target, schema_path=SCHEMA_PATH)


def _assert_phase_list_invariants(phase_list: list[str], *, expected_length: int) -> None:
    """Assert ordering, uniqueness, and length on a routing.phase_list."""
    assert "research" in phase_list
    assert phase_list.index("refine") < phase_list.index("research")
    assert phase_list.index("research") < phase_list.index("spec")
    assert len(phase_list) == len(set(phase_list))
    assert len(phase_list) == expected_length


def _walk_post_ship_to_done(repo: Path, state_path: Path, *, feature_id: str) -> dict[str, Any]:
    """Migrate the (already-shipped) feature to v3, then walk qa -> done.

    Asserts ``shipped_at`` was stamped at ship completion, that
    :func:`migrate_to_v3` lands ``flow_version: 3`` and seeds the qa
    phase, and that ``next_phase_command`` returns ``None`` at both the
    qa entry and the terminal ``done`` slot.

    Returns the final payload so callers can pin additional invariants
    (e.g. routing.phase_list shape).
    """
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert "shipped_at" in payload, "ship completion must stamp shipped_at"

    migrate_to_v3(repo, feature_id, schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["flow_version"] == 3
    assert payload["phases"]["qa"]["status"] == "pending"

    start_phase(state_path, "qa", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "qa"
    assert next_phase_command(payload) is None

    complete_phase(state_path, "qa", schema_path=SCHEMA_PATH)
    finish_feature(state_path, schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "done"
    assert next_phase_command(payload) is None
    return payload


def test_full_tier_walk_seeds_and_advances_through_research(tmp_path: Path) -> None:
    """Full tier seeds via :func:`seed_routed_feature`; walks every transition.

    Asserts ``next_phase_command`` at each entry, including the inserted
    ``research`` slot.  Locks the durable ``routing.phase_list`` shape
    (uniqueness, ordering of ``research`` between ``refine`` and ``spec``,
    expected per-flow-version length).
    """
    repo = _stage_repo(tmp_path)
    folder = seed_routed_feature(
        repo,
        idea="research and ship a billing flow",
        final_tier="full",
        today=TODAY,
    )
    state_path = folder / "state.json"

    # ------------------------------------------------------------------
    # Seed: refine entry, /forge:research is next.
    # ------------------------------------------------------------------
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["tier"] == "full"
    assert payload["current_phase"] == "refine"
    assert next_phase_command(payload) == "/forge:research"

    # routing.phase_list seeded explicitly by _resolve_seed_lifecycle —
    # 11-entry pre-v3 full list (no trailing qa until migrate_to_v3).
    seeded_phase_list = payload["routing"]["phase_list"]
    assert seeded_phase_list == [
        "refine",
        "research",
        "spec",
        "domain",
        "scenarios",
        "plan",
        "crucible",
        "review",
        "execute",
        "verify",
        "ship",
    ]
    _assert_phase_list_invariants(seeded_phase_list, expected_length=11)

    # Refine populates refined_idea, then walks the linear chain through
    # to crucible -> review (target=plan).
    record_refined_idea(
        state_path,
        refined="Bill cards via Stripe and emit receipts on success.",
        schema_path=SCHEMA_PATH,
    )
    _advance(state_path, done="refine", nxt="research", expected_next_command="/forge:spec")
    _advance(state_path, done="research", nxt="spec", expected_next_command="/forge:domain")
    # refined_idea must persist across refine -> research -> spec; the
    # spec phase consumes it as Intent draft.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["refined_idea"].startswith("Bill cards via Stripe")

    _advance(state_path, done="spec", nxt="domain", expected_next_command="/forge:scenarios")
    _advance(state_path, done="domain", nxt="scenarios", expected_next_command="/forge:plan")
    _advance(state_path, done="scenarios", nxt="plan", expected_next_command="/forge:crucible")
    _advance(
        state_path,
        done="plan",
        nxt="crucible",
        expected_next_command="/forge:review --target plan",
    )

    # Crucible -> Review (target=plan): dual-target review pass.
    complete_phase(state_path, "crucible", schema_path=SCHEMA_PATH)
    start_phase(state_path, "review", schema_path=SCHEMA_PATH)
    _walk_review_target(state_path, target="plan")
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "review"
    # plan-pass: _next_review_command -> /forge:execute (plan done, code pending).
    assert next_phase_command(payload) == "/forge:execute"

    # Review (plan) -> Execute: plan-pass leaves review in_progress (gate
    # clears only when both targets done); start the next phase WITHOUT
    # completing review — start_phase("execute") just pivots current_phase.
    start_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "execute"
    assert next_phase_command(payload) == "/forge:review --target code"

    # Execute -> Review (target=code).
    complete_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    start_phase(state_path, "review", schema_path=SCHEMA_PATH)
    _walk_review_target(state_path, target="code")
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    # Both targets done now; _next_review_command falls through to verify.
    assert next_phase_command(payload) == "/forge:verify"

    _advance(state_path, done="review", nxt="verify", expected_next_command="/forge:ship")
    _advance(
        state_path,
        done="verify",
        nxt="ship",
        expected_next_command="/forge:qa --against merged",
    )

    # Ship -> QA (post-merge migration to flow_version=3) -> done.
    complete_phase(state_path, "ship", schema_path=SCHEMA_PATH)
    payload = _walk_post_ship_to_done(repo, state_path, feature_id=payload["feature_id"])

    # routing.phase_list invariants over the full walk: explicit list
    # survives every transition unchanged. migrate_to_v3 bumps
    # flow_version + seeds phases.qa but does NOT rewrite the
    # already-seeded routing.phase_list (the explicit list has precedence
    # over lazy-derive in get_phase_list).
    final_phase_list = payload["routing"]["phase_list"]
    assert final_phase_list == seeded_phase_list
    # 11 entries for the pre-v3 seed; v3 adds qa to the lazy default but
    # the explicit list seeded at /forge:do time stays at 11.
    _assert_phase_list_invariants(final_phase_list, expected_length=11)
    # get_phase_list returns the explicit list (precedence rule), so it
    # also yields 11 entries even though flow_version is now 3.
    assert get_phase_list(payload) == final_phase_list
