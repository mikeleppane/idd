"""Smoke tests for the ``/forge:do`` post-confirm routing surface (M3 P6.1 T6).

These tests drive :func:`tools.routing.seed_routed_feature` and the documented
downstream Python helper sequence — no live LLM, no slash-command runtime,
no user dialogue.  They walk the post-user-confirm half of ``/forge:do``
end-to-end against schema-validated ``state.json`` payloads and assert that
``next_phase_command`` returns the right slash literal at every boundary.

Out-of-scope for pytest (manual / plugin-verified per the appended checklist
at the bottom of ``docs/plans/2026-05-08-m3-p6-1-do-routing.md``):

  * Live LLM tier proposal.
  * User-confirm dialogue (numbered checkbox UI).
  * Constitution skip / bootstrap / cancel UI flow.
  * KeyboardInterrupt mid-confirm cleanup (between Constitution preflight
    and tier confirm).

Coverage target: AC #6, #7, #11 from the M3 P6.1 plan.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tools import routing
from tools.archive import (
    ArchiveError,
    cleanup_seeded_feature,
    scan_existing_capabilities,
    slug_from_idea,
)
from tools.routing import seed_routed_feature
from tools.state import (
    StateError,
    complete_phase,
    next_phase_command,
    read_state,
    start_phase,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"

# Pinned today so feature_id assertions are stable across CI clocks.
TODAY = date(2026, 5, 8)


# ---------------------------------------------------------------------------
# Fixture helper — stage a tmp_path repo_root with the live schema in place
# ---------------------------------------------------------------------------


def _stage_repo(tmp_path: Path) -> Path:
    """Stage a repo_root under ``tmp_path`` with the real schema next to it.

    ``seed_routed_feature`` resolves ``schema_path`` as
    ``repo_root / "schemas/state.schema.json"`` (see ``tools/routing.py``),
    so we copy the live schema into the staged tree.  Templates are read
    from the actual repo via ``tools.archive._FEATURE_TEMPLATES_DIR`` (an
    absolute path resolved at import time), so they do NOT need to be
    duplicated under ``tmp_path``.
    """
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "state.schema.json").write_text(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def _state_path(folder: Path) -> Path:
    return folder / "state.json"


# ---------------------------------------------------------------------------
# 1. Focused walk: seed -> spec -> execute -> verify
# ---------------------------------------------------------------------------


def test_focused_walk_seed_to_execute(tmp_path: Path) -> None:
    """Drive the focused-tier post-confirm walk through every phase boundary.

    Asserts at each step:
      * ``state.json`` validates against the live schema (via ``read_state``
        with ``schema_path``).
      * ``current_phase`` + ``phases[current_phase].status`` advance per
        ``_FOCUSED_NEXT``.
      * :func:`next_phase_command` returns the documented slash literal.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="add OAuth login flow",
        final_tier="focused",
        today=TODAY,
    )
    state_path = _state_path(folder)

    # Post-seed: schema-valid; current_phase=spec/in_progress; routing block
    # present per spec §5.3.2 step 6 + plan deviation #4.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert folder.is_dir()
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"
    assert payload["routing"]["final_tier"] == "focused"
    assert payload["routing"]["idea"] == "add OAuth login flow"
    assert payload["skipped"] == [
        {"phase": "research", "reason": "M3 deferred — manual research acceptable"}
    ]
    # _FOCUSED_NEXT["spec"] -> /forge:execute.
    assert next_phase_command(payload) == "/forge:execute"

    # spec -> execute boundary.
    complete_phase(state_path, "spec", schema_path=SCHEMA_PATH)
    start_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "execute"
    assert payload["phases"]["execute"]["status"] == "in_progress"
    # _FOCUSED_NEXT["execute"] -> /forge:verify.
    assert next_phase_command(payload) == "/forge:verify"

    # execute -> verify boundary (terminal for focused).
    complete_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    start_phase(state_path, "verify", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "verify"
    assert payload["phases"]["verify"]["status"] == "in_progress"
    # _FOCUSED_NEXT["verify"] -> None (terminal).
    assert next_phase_command(payload) is None


# ---------------------------------------------------------------------------
# 2. Standard walk: seed -> spec -> scenarios -> plan
# ---------------------------------------------------------------------------


def test_standard_walk_seed_to_plan(tmp_path: Path) -> None:
    """Drive the standard-tier post-confirm walk through ``spec → scenarios → plan``.

    The standard tier never enters ``refine`` (locked decision from P4
    deep-followup; see plan deviation #1).  Walk stops at ``plan`` per the
    T6 contract — the rest of the standard pipeline is covered by the M2
    standard-tier smoke.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="multi-tenant billing pipeline",
        final_tier="standard",
        constitution_present=True,
        today=TODAY,
    )
    state_path = _state_path(folder)

    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["tier"] == "standard"
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"
    # _STANDARD_NEXT["spec"] -> /forge:scenarios.
    assert next_phase_command(payload) == "/forge:scenarios"

    # spec -> scenarios boundary.
    complete_phase(state_path, "spec", schema_path=SCHEMA_PATH)
    start_phase(state_path, "scenarios", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "scenarios"
    assert payload["phases"]["scenarios"]["status"] == "in_progress"
    # _STANDARD_NEXT["scenarios"] -> /forge:plan.
    assert next_phase_command(payload) == "/forge:plan"

    # scenarios -> plan boundary.
    complete_phase(state_path, "scenarios", schema_path=SCHEMA_PATH)
    start_phase(state_path, "plan", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "plan"
    assert payload["phases"]["plan"]["status"] == "in_progress"
    # _STANDARD_NEXT["plan"] -> /forge:crucible.
    assert next_phase_command(payload) == "/forge:crucible"


# ---------------------------------------------------------------------------
# 3. Capability collision suffix-disambig branch
# ---------------------------------------------------------------------------


def test_capability_scan_does_not_block_seeder_no_canonical_guard(tmp_path: Path) -> None:
    """The seeder does NOT consult ``scan_existing_capabilities``.

    The documented division of labour for ``/forge:do`` step 4 puts the
    canonical-capability check at the SKILL layer (prose-driven, runs
    before the seeder).  The seeder itself only collides on a feature
    folder under ``.forge/features/<feature_id>``, not on a canonical
    capability under ``.forge/specs/<slug>/``.

    This test pins that separation: pre-seed a canonical capability, then
    confirm seed_routed_feature happily runs (no guard rejection).  The
    second invocation collides because the FEATURE folder now exists, not
    because the canonical capability is in the way.

    Renamed from ``test_capability_collision_suffix_disambig_branch``
    (M3 P6.1 T7 finding p6-1-L7) — the old name overstated coverage; the
    real suffix-disambig flow is locked by
    ``test_suffix_disambig_yields_distinct_slug``.
    """
    repo = _stage_repo(tmp_path)

    # Pre-seed the canonical capability so the scan returns a hit.
    idea = "add OAuth login flow"
    canonical_slug = slug_from_idea(idea)
    canonical_dir = repo / ".forge" / "specs" / canonical_slug
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "SPEC.md").write_text("# canonical\n", encoding="utf-8")

    # Documented preflight: capability scan surfaces the existing slug.
    existing = scan_existing_capabilities(repo)
    assert canonical_slug in existing

    # The pre-seed guard is the SCAN — not the seeder.  The seeder only
    # collides on a feature folder under .forge/features/<feature_id>, not
    # on a canonical capability.  Confirm that without a folder collision
    # the seeder happily runs (the user's actual disambiguation lives at
    # the SCAN layer; the seeder doesn't refuse).
    folder_first = seed_routed_feature(
        repo,
        idea=idea,
        final_tier="focused",
        today=TODAY,
    )
    assert folder_first.is_dir()
    assert folder_first.name == f"2026-05-08-{canonical_slug}"

    # Re-invoking with the same idea now collides on the feature folder
    # (independent of the canonical capability hit) — the helper raises so
    # the skill prose can route to the suffix-disambig prompt.
    with pytest.raises(ArchiveError) as excinfo:
        seed_routed_feature(
            repo,
            idea=idea,
            final_tier="focused",
            today=TODAY,
        )
    assert canonical_slug in str(excinfo.value)


def test_suffix_disambig_yields_distinct_slug(tmp_path: Path) -> None:
    """Two ideas that differ only by a disambiguating suffix produce two
    distinct feature folders, both seeded successfully.

    Locks the real suffix-disambig flow that the previous (now-renamed)
    test claimed to cover but actually didn't.  Drives two
    ``seed_routed_feature`` calls back-to-back with idea variants that
    slug-derive to different folders.

    M3 P6.1 T7 finding p6-1-L7.
    """
    repo = _stage_repo(tmp_path)
    idea_a = "add OAuth login flow"
    idea_b = "add OAuth login flow v2"

    slug_a = slug_from_idea(idea_a)
    slug_b = slug_from_idea(idea_b)
    assert slug_a != slug_b, (
        f"fixtures must produce distinct slugs to exercise suffix-disambig: "
        f"got {slug_a!r} == {slug_b!r}"
    )

    folder_a = seed_routed_feature(
        repo,
        idea=idea_a,
        final_tier="focused",
        today=TODAY,
    )
    folder_b = seed_routed_feature(
        repo,
        idea=idea_b,
        final_tier="focused",
        today=TODAY,
    )

    # Both seeds succeeded and produced distinct feature folders.
    assert folder_a.is_dir()
    assert folder_b.is_dir()
    assert folder_a != folder_b
    assert folder_a.name == f"2026-05-08-{slug_a}"
    assert folder_b.name == f"2026-05-08-{slug_b}"


# ---------------------------------------------------------------------------
# 4. Cancellation cleanup via cleanup_seeded_feature
# ---------------------------------------------------------------------------


def test_cancellation_cleanup_via_cleanup_seeded_feature(tmp_path: Path) -> None:
    """Post-seed user-cancel: cleanup_seeded_feature removes the folder.

    Simulates the skill's try/finally cancel hook (per plan §"Surface to
    add" §3 step 9): the seed succeeded, no commits were appended, no
    decisions.md mutation occurred — the user typed 'n' at a downstream
    prompt and the skill calls ``cleanup_seeded_feature(repo, feature_id)``.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="user will cancel",
        final_tier="focused",
        today=TODAY,
    )
    feature_id = folder.name

    # Sanity: folder + state.json present BEFORE cleanup.
    assert folder.is_dir()
    payload = read_state(_state_path(folder), schema_path=SCHEMA_PATH)
    assert payload["commits"] == []
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"

    # Cleanup hook fires.
    assert cleanup_seeded_feature(repo, feature_id) is True

    # Folder removed; parent .forge/features/ is empty.
    assert not folder.exists()
    features_root = repo / ".forge" / "features"
    assert features_root.is_dir()
    assert list(features_root.iterdir()) == []


# ---------------------------------------------------------------------------
# 5. record_routing_decision failure leaves no orphan
# ---------------------------------------------------------------------------


def test_record_routing_decision_failure_leaves_no_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A StateError from record_routing_decision triggers the cleanup wrapper.

    Asserts the helper's post-seed cleanup contract (per the
    ``seed_routed_feature`` docstring step 7): cleanup_seeded_feature runs
    BEFORE the original StateError re-raises, leaving no folder behind.
    """
    repo = _stage_repo(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise StateError("simulated routing-block schema rejection")

    # Patch the symbol in the routing module's namespace — that is the
    # binding seed_routed_feature actually calls.
    monkeypatch.setattr(routing, "record_routing_decision", _boom)

    with pytest.raises(StateError, match="simulated routing-block"):
        seed_routed_feature(
            repo,
            idea="will fail at routing block",
            final_tier="focused",
            today=TODAY,
        )

    # The seeded folder must be gone — no orphan left behind.
    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or list(features_root.iterdir()) == []


# ---------------------------------------------------------------------------
# Full-tier smoke moved to tests/smoke/test_do_routing_full_tier_smoke.py
# (P6.2 owns the end-to-end seed → refine → spec → domain walk plus
# refined_idea persistence, capability-scan locking, and full-tier
# post-seed cleanup. This file owns focused/standard only.)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. Schema-path tier enforcement: bogus tier refused before any seed write
# ---------------------------------------------------------------------------


def test_schema_path_enforcement_refuses_bogus_tier_before_seed(tmp_path: Path) -> None:
    """Unknown tier raises ValueError BEFORE any disk mutation.

    The validation order in ``seed_routed_feature`` (step 1 of the
    docstring) catches any tier outside ``VALID_TIERS`` and refuses
    BEFORE the slug compute, the collision check, or any folder
    creation.  As of P6.2 ``full`` is a legitimate tier and seeds
    normally; only genuinely invalid values land here.
    """
    repo = _stage_repo(tmp_path)

    with pytest.raises(ValueError, match="bogus"):
        seed_routed_feature(
            repo,
            idea="invalid tier path",
            final_tier="bogus",
            today=TODAY,
        )

    # .forge/features/ must NOT exist — the helper bails before any mkdir.
    features_root = repo / ".forge" / "features"
    assert not features_root.exists()
