"""Smoke tests for the ``/forge:do --full`` post-confirm routing surface.

Walks the full-tier branch of :func:`tools.routing.seed_routed_feature` end-to-end
through the documented Python helper sequence — no live LLM, no slash-command
runtime, no user dialogue.  Mirrors the focused/standard model in
``tests/smoke/test_do_routing_smoke.py`` and pins the full-tier contract:

  * Full tier seeds ``current_phase="refine"`` (NOT ``"spec"``).
  * The walk advances ``refine → spec → domain`` via the state helpers,
    asserting :func:`tools.state.next_phase_command` returns the right slash
    literal at every boundary.
  * ``state.json.refined_idea`` survives the ``refine → spec`` transition
    (spec consumes it as Intent draft).
  * The capability slug is locked at ``/forge:do`` time via the
    ``feature_slug`` override; refine itself does NOT re-derive or re-scan.
  * Post-seed cleanup wraps ``record_routing_decision`` failures even on the
    full-tier path.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tools import routing
from tools.archive import scan_existing_capabilities, slug_from_idea
from tools.routing import seed_routed_feature
from tools.state import (
    StateError,
    complete_phase,
    next_phase_command,
    read_state,
    record_refined_idea,
    start_phase,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"

# Pinned today so feature_id assertions are stable across CI clocks.
TODAY = date(2026, 5, 8)

# ISO-8601 UTC timestamp pattern (matches ``record_routing_decision`` output and
# the ``phases.<phase>.started_at`` shape produced by ``create_feature_folder``
# / ``start_phase``).  Mirrors the schema's ``date-time`` constraint.
_ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


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
# 1. Full-tier seed produces the refine phase (not spec)
# ---------------------------------------------------------------------------


def test_full_tier_seed_produces_refine_phase(tmp_path: Path) -> None:
    """``final_tier='full'`` seeds a folder anchored at ``current_phase='refine'``.

    The helper returns a folder whose
    ``state.json`` validates against the live schema and carries
    ``current_phase='refine'`` + ``phases.refine.status='in_progress'`` +
    a populated ``routing`` block.  The ``phases`` map contains ONLY
    ``refine`` — no leaked ``spec`` / ``domain`` blocks from the
    focused/standard seed body.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="design billing subsystem",
        final_tier="full",
        today=TODAY,
    )

    # Folder shape: <today>-<slug>.  ``slug_from_idea`` is the deterministic
    # source of truth so the assertion does not hard-code a re-derivation.
    assert folder.is_dir()
    expected_slug = slug_from_idea("design billing subsystem")
    assert folder.name == f"2026-05-08-{expected_slug}"

    # Schema-valid payload via the live schema.
    payload = read_state(_state_path(folder), schema_path=SCHEMA_PATH)

    # Tier + phase contract.
    assert payload["tier"] == "full"
    assert payload["current_phase"] == "refine"
    refine_block = payload["phases"]["refine"]
    assert refine_block["status"] == "in_progress"
    assert _ISO_8601_RE.match(refine_block["started_at"]), (
        f"phases.refine.started_at must be ISO 8601, got {refine_block['started_at']!r}"
    )

    # No leaked spec/domain blocks: the seed body for full tier owns ONLY
    # the refine phase.  Downstream phases are added by ``start_phase`` after
    # ``complete_phase`` clears the previous one.
    assert set(payload["phases"].keys()) == {"refine"}

    # Routing block populated end-to-end.
    routing_block = payload["routing"]
    assert routing_block["idea"] == "design billing subsystem"
    assert routing_block["final_tier"] == "full"
    assert _ISO_8601_RE.match(routing_block["decided_at"]), (
        f"routing.decided_at must be ISO 8601, got {routing_block['decided_at']!r}"
    )
    assert routing_block["constitution_present"] is False


# ---------------------------------------------------------------------------
# 2. Full-tier walk: seed → refine → spec → domain via state helpers
# ---------------------------------------------------------------------------


def test_full_tier_walk_seed_to_domain_via_state_helpers(tmp_path: Path) -> None:
    """Drive ``refine → spec → domain`` via the deterministic state helpers.

    Asserts at each boundary:
      * ``next_phase_command`` returns the documented slash literal per
        ``_FULL_NEXT`` (``refine → /forge:spec``, ``spec → /forge:domain``,
        ``domain → /forge:scenarios``).
      * ``state.json`` validates against the live schema after every
        ``complete_phase`` + ``start_phase`` pair.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="design billing subsystem",
        final_tier="full",
        today=TODAY,
    )
    state_path = _state_path(folder)

    # Refine entry: _FULL_NEXT["refine"] -> /forge:research.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "refine"
    assert next_phase_command(payload) == "/forge:research"

    # Refine populates refined_idea + completes; research phase opens.
    record_refined_idea(
        state_path,
        refined="The billing subsystem charges cards via Stripe.",
        schema_path=SCHEMA_PATH,
    )
    complete_phase(state_path, "refine", schema_path=SCHEMA_PATH)
    start_phase(state_path, "research", schema_path=SCHEMA_PATH)

    # Research entry: _FULL_NEXT["research"] -> /forge:spec.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "research"
    assert next_phase_command(payload) == "/forge:spec"

    # Research → spec boundary.
    complete_phase(state_path, "research", schema_path=SCHEMA_PATH)
    start_phase(state_path, "spec", schema_path=SCHEMA_PATH)

    # Spec entry: _FULL_NEXT["spec"] -> /forge:domain.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"
    assert next_phase_command(payload) == "/forge:domain"

    # Spec → domain boundary.
    complete_phase(state_path, "spec", schema_path=SCHEMA_PATH)
    start_phase(state_path, "domain", schema_path=SCHEMA_PATH)

    # Domain entry: _FULL_NEXT["domain"] -> /forge:scenarios.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "domain"
    assert payload["phases"]["domain"]["status"] == "in_progress"
    assert next_phase_command(payload) == "/forge:scenarios"


# ---------------------------------------------------------------------------
# 3. refined_idea persists across the refine → spec transition
# ---------------------------------------------------------------------------


def test_full_tier_refined_idea_consumed_by_spec_phase(tmp_path: Path) -> None:
    """``refined_idea`` survives ``complete_phase('refine') + start_phase('spec')``.

    Contract: ``/forge:refine`` populates ``state.json.refined_idea``;
    ``/forge:spec`` reads it as the Intent draft.  Phase transitions must
    NOT clear the field.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="design billing subsystem",
        final_tier="full",
        today=TODAY,
    )
    state_path = _state_path(folder)

    refined_text = (
        "The billing subsystem must charge cards via Stripe and emit receipt emails on success."
    )
    record_refined_idea(state_path, refined=refined_text, schema_path=SCHEMA_PATH)

    # refined_idea present at refine phase.
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["refined_idea"] == refined_text

    # Transit to spec.  ``complete_phase`` + ``start_phase`` must not strip
    # the field.
    complete_phase(state_path, "refine", schema_path=SCHEMA_PATH)
    start_phase(state_path, "spec", schema_path=SCHEMA_PATH)

    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "spec"
    assert payload["refined_idea"] == refined_text, (
        "refined_idea must persist across the refine → spec transition "
        "for /forge:spec to consume it as Intent draft (P4 contract)"
    )


# ---------------------------------------------------------------------------
# 4. Capability scan locked at /forge:do time, NOT at refine time
# ---------------------------------------------------------------------------


def test_full_tier_capability_scan_runs_at_do_time_not_refine_time(
    tmp_path: Path,
) -> None:
    """The slug is locked at ``/forge:do`` time via ``feature_slug`` override.

    The capability scan runs in ``/forge:do`` (not in ``/forge:refine``),
    so an operator who picks a
    suffix-disambig slug like ``billing-v2`` carries it through the seed
    unchanged.  The audit record (``routing.idea``) keeps the ORIGINAL
    idea verbatim — only the folder slug is overridden.

    Pre-seeds a canonical capability under ``.forge/specs/<slug>/`` to
    confirm the scan would surface a hit; then the ``feature_slug``
    override deliberately routes around it.
    """
    repo = _stage_repo(tmp_path)

    # Pre-seed a canonical capability so the scan reports the canonical slug.
    idea = "design billing subsystem"
    canonical_slug = slug_from_idea(idea)
    canonical_dir = repo / ".forge" / "specs" / canonical_slug
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "SPEC.md").write_text("# canonical billing\n", encoding="utf-8")

    # The /forge:do scan would surface the canonical hit.
    existing = scan_existing_capabilities(repo)
    assert canonical_slug in existing

    # Operator picks the suffix-disambig slug at /forge:do time.  The
    # routing helper uses ``feature_slug`` verbatim for ``feature_id`` and
    # records the ORIGINAL idea text under ``routing.idea``.
    folder = seed_routed_feature(
        repo,
        idea=idea,
        final_tier="full",
        feature_slug="billing-v2",
        today=TODAY,
    )

    # feature_id reflects the override slug.
    assert folder.name == "2026-05-08-billing-v2"
    assert "billing-v2" in folder.name
    assert canonical_slug not in folder.name

    # routing.idea preserves the original verbatim — refine does NOT
    # re-derive or re-scan.
    payload = read_state(_state_path(folder), schema_path=SCHEMA_PATH)
    assert payload["routing"]["idea"] == idea
    assert payload["feature_id"] == "2026-05-08-billing-v2"
    assert payload["current_phase"] == "refine"


# ---------------------------------------------------------------------------
# 5. Post-seed cleanup on full-tier record_routing_decision failure
# ---------------------------------------------------------------------------


def test_full_tier_post_seed_cleanup_on_record_routing_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A StateError from ``record_routing_decision`` cleans up the full-tier seed.

    The cleanup wrapper in ``seed_routed_feature`` is tier-agnostic; this
    test pins that the full-tier path (which seeds ``phases.refine`` instead
    of ``phases.spec``) still triggers ``cleanup_seeded_feature`` on a
    routing-block failure.  Locks the risk-table mitigation for orphan
    ``phases.refine`` blocks.
    """
    repo = _stage_repo(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise StateError("simulated full-tier routing-block schema rejection")

    # Patch in the routing module's namespace — that is the binding
    # ``seed_routed_feature`` actually calls.
    monkeypatch.setattr(routing, "record_routing_decision", _boom)

    with pytest.raises(StateError, match="simulated full-tier routing-block"):
        seed_routed_feature(
            repo,
            idea="will fail at routing block",
            final_tier="full",
            today=TODAY,
        )

    # The seeded full-tier folder must be gone — no orphan refine block left
    # behind.  ``.forge/features/`` either does not exist (parent unmade) or
    # is empty.
    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or list(features_root.iterdir()) == []
