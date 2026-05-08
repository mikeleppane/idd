"""Routing entry-helper for ``/forge:do``.

This module is the single Python entry point for the **post-confirm** half of
``/forge:do``.  The skill (T3) handles all LLM tier proposal, user-confirm
dialogue, secrets warning, Constitution preflight, and capability-scan UI;
everything below the user-confirm waterline is composed here so pytest can
drive the seed flow deterministically.

The function composes T0.5 + T2 + existing P5/P1 helpers:

  * :func:`tools.archive.slug_from_idea` — derive the capability slug.
  * :func:`tools.state.feature_folder_exists` — collision check.
  * :func:`tools.archive.create_feature_folder` — seed folder + state.json
    body + SPEC.md + decisions.md (current_phase=spec, phases.spec=
    in_progress, skipped[research]).
  * :func:`tools.state.record_routing_decision` — append the validated
    ``routing`` block to the seeded state.json.
  * :func:`tools.archive.cleanup_seeded_feature` — best-effort rollback of
    the seeded folder when ``record_routing_decision`` fails AFTER the
    folder was created on disk.

``--full`` raises :class:`NotImplementedError` BEFORE any disk mutation.  The
helper also rejects unknown tiers via :class:`ValueError` BEFORE any
mutation, so neither slot can leave a partial folder behind.

Coverage AC: 100% on this module (M3 P6.1 plan §AC #5).
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

from tools.archive import (
    ArchiveError,
    cleanup_seeded_feature,
    create_feature_folder,
    slug_from_idea,
)
from tools.state import (
    VALID_TIERS,
    feature_folder_exists,
    record_routing_decision,
)

# Schema-aligned capability slug pattern.  Mirrors
# ``tools.archive._CAPABILITY_SLUG_SCHEMA_RE`` (≥3 chars, alnum-leading).  Used
# to validate the operator-supplied ``feature_slug`` for the suffix-disambig
# branch — the chosen ``<slug>-v2`` / ``<slug>-bulk`` slug must satisfy the
# same shape as a slug derived from ``slug_from_idea``.
_FEATURE_SLUG_RE: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]{2,}$")

# Locked dispatch literal for the not-yet-shipped ``--full`` path.  The skill
# (T3) surfaces the raise verbatim so an operator can grep the upcoming plan.
_FULL_TIER_PLAN_POINTER: str = (
    "--full routing ships in M3 P6.2; track at docs/plans/2026-05-DD-m3-p6-2-full-tier-routing.md"
)


def seed_routed_feature(
    repo_root: Path,
    *,
    idea: str,
    final_tier: str,
    proposed_tier: str | None = None,
    rationale: str | None = None,
    constitution_present: bool = False,
    today: date | None = None,
    feature_slug: str | None = None,
) -> Path:
    """Seed a ``/forge:do`` feature folder + routing block in one validated step.

    Composes :func:`create_feature_folder` and
    :func:`record_routing_decision` with a post-seed cleanup wrapper so
    ``record_routing_decision`` failures never leave a half-seeded folder
    behind.  Both helpers receive ``schema_path =
    repo_root / "schemas/state.schema.json"`` so an invalid payload refuses
    BEFORE any disk mutation.

    Order of operations:

      1. ``final_tier == "full"`` → :class:`NotImplementedError` (P6.2 pointer).
      2. ``final_tier not in VALID_TIERS`` → :class:`ValueError`.  At this
         point only ``focused`` / ``standard`` survive; ``full`` was already
         caught above.
      3. Compute ``today_iso`` from ``today`` (or ``date.today()`` when
         omitted) and ``feature_id = f"{today_iso}-{slug_from_idea(idea)}"``.
      4. Resolve ``schema_path = repo_root / "schemas/state.schema.json"``.
      5. Pre-collision check via :func:`feature_folder_exists` →
         :class:`ArchiveError` on True (no disk mutation yet).
      6. Call :func:`create_feature_folder` (seed body validated against
         schema BEFORE any disk write).
      7. Call :func:`record_routing_decision` against the new state.json.
         On ANY exception (schema refusal, OSError, KeyboardInterrupt,
         SystemExit), invoke :func:`cleanup_seeded_feature` BEFORE
         re-raising the original exception with traceback intact.  Cleanup
         is best-effort: if it itself raises, the original exception still
         re-raises (the cleanup failure is suppressed; ``cleanup_seeded_feature``
         never raises on the supported orphan shape so this branch only hits
         when the predicate has already shifted out from under us, in which
         case the partial folder is the lesser of two evils).

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        idea: User-supplied idea text.  Persisted verbatim into
            ``state.json.routing.idea`` — the skill prints a one-line
            secrets warning before invoking this helper.
        final_tier: ``focused`` or ``standard`` for P6.1.  ``full`` raises
            :class:`NotImplementedError` with a pointer to the planned
            P6.2 plan.  Any other value raises :class:`ValueError`.
        proposed_tier: Tier the LLM proposed before user override (optional).
        rationale: One-sentence reason from the router or user (optional).
        constitution_present: ``True`` when ``.forge/CONSTITUTION.md`` was
            loaded at routing time.  Default ``False``.
        today: Optional date injection for deterministic ``feature_id``
            computation (used by tests).  Defaults to ``date.today()``.
        feature_slug: Optional operator-supplied capability slug for the
            suffix-disambig branch (``<slug>-v2``, ``<slug>-bulk``).  When
            given, this slug is used verbatim to compose ``feature_id`` and
            ``idea`` is persisted into ``routing.idea`` unchanged — the
            operator's chosen disambiguation never corrupts the audit
            record.  Must satisfy ``^[a-z0-9][a-z0-9-]{2,}$`` (mirrors the
            schema-aligned slug shape ``slug_from_idea`` produces).  When
            omitted, the slug is derived via ``slug_from_idea(idea)`` (the
            no-collision path).

    Returns:
        Path to the new ``.forge/features/<feature_id>/`` folder, ready for
        the skill to print ``Next: /forge:spec --feature <feature_id>``.

    Raises:
        NotImplementedError: ``final_tier == "full"`` (M3 P6.2 territory);
            no folder created.
        ValueError: ``final_tier`` is not in ``VALID_TIERS`` and is not
            ``"full"``; OR ``feature_slug`` is given but does not satisfy
            the slug pattern.  No folder created in either case.
        ArchiveError: Folder already exists (collision), or
            :func:`create_feature_folder` itself rejects the seed.
        StateError: ``record_routing_decision`` rejected the routing
            block (e.g. invalid timestamp / bogus tier); the seeded
            folder is removed before re-raise.
        BaseException: ``KeyboardInterrupt`` / ``SystemExit`` between
            steps 6 and 7 also trigger the cleanup wrapper before
            re-raising.
    """
    # Step 1: --full → NotImplementedError BEFORE any disk mutation.
    if final_tier == "full":
        raise NotImplementedError(_FULL_TIER_PLAN_POINTER)

    # Step 2: any other unknown tier → ValueError BEFORE any disk mutation.
    if final_tier not in VALID_TIERS:
        raise ValueError(f"invalid final_tier {final_tier!r}; must be one of {VALID_TIERS}")

    # Step 3: feature_id = <today>-<slug>.  ``today`` injection keeps tests
    # deterministic without monkeypatching ``datetime.date``.  When
    # ``feature_slug`` is given (suffix-disambig branch) it is used verbatim
    # after a regex check; ``idea`` is persisted into ``routing.idea``
    # unchanged so the audit record reflects what the user actually said.
    today_iso = (today or date.today()).isoformat()
    if feature_slug is not None:
        if not _FEATURE_SLUG_RE.fullmatch(feature_slug):
            raise ValueError(
                f"invalid feature_slug {feature_slug!r}; "
                "expected slug matching ^[a-z0-9][a-z0-9-]{2,}$ "
                "(≥3 chars, alnum-leading, lowercase + hyphens)"
            )
        slug = feature_slug
    else:
        slug = slug_from_idea(idea)
    feature_id = f"{today_iso}-{slug}"

    # Step 4: schema path passed to BOTH helpers so payload validation
    # refuses BEFORE any disk write.
    schema_path = repo_root / "schemas" / "state.schema.json"

    # Step 5: collision check BEFORE seed.  ``create_feature_folder`` would
    # also raise on collision, but we surface the same ArchiveError earlier
    # with a stable message so the skill can render it without inspecting
    # downstream helpers' wording.
    if feature_folder_exists(repo_root, feature_id):
        raise ArchiveError(f"feature folder already exists: {feature_id!r}")

    # Step 6: seed the folder.  ``create_feature_folder`` is itself wrapped
    # in best-effort rmtree on per-file failure (T2 contract), so a failure
    # HERE never leaves a partial folder behind — no cleanup wrapper needed
    # around this call.
    folder = create_feature_folder(
        repo_root,
        feature_id=feature_id,
        tier=final_tier,
        schema_path=schema_path,
    )

    # Step 7: append the routing block.  This is the only post-seed write,
    # so the cleanup wrapper is scoped tightly to it.  ``BaseException``
    # catches KeyboardInterrupt + SystemExit alongside the normal raise
    # path (StateError on schema refusal, OSError on disk-full mid-write).
    #
    # Cleanup-failure semantics: we MUST surface the original
    # ``record_routing_decision`` exception to the caller — it carries the
    # actionable failure mode (schema refusal, disk-full, KeyboardInterrupt).
    # If ``cleanup_seeded_feature`` itself raises during rollback (a
    # ``BaseException`` such as ``KeyboardInterrupt`` mid-rmtree, or an OSError
    # surfacing through a path the helper doesn't normally trap), we log a
    # one-line WARN and re-raise the ORIGINAL exception via ``raise original``.
    # We catch ``BaseException`` from cleanup — not just ``Exception`` — so a
    # ``KeyboardInterrupt`` during rmtree can never mask the underlying
    # ``record_routing_decision`` failure.  Addresses M3 P6.1 T7 finding p6-1-M3.
    state_path = folder / "state.json"
    try:
        record_routing_decision(
            state_path,
            idea=idea,
            final_tier=final_tier,
            proposed_tier=proposed_tier,
            rationale=rationale,
            constitution_present=constitution_present,
            schema_path=schema_path,
        )
    except BaseException as original:
        try:
            cleanup_seeded_feature(repo_root, feature_id)
        except BaseException:
            # Cleanup itself raised. Log and fall through to re-raise the
            # ORIGINAL record_routing_decision exception, NOT the cleanup one.
            print(
                "WARN: cleanup_seeded_feature raised during post-seed rollback; "
                "original exception below",
                file=sys.stderr,
            )
        raise original from None

    return folder
