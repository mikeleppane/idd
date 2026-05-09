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

All three tiers seed normally: ``focused`` / ``standard`` enter at
``current_phase="spec"`` (P6.1 default), and ``full`` enters at
``current_phase="refine"`` (P6.2 — refine is full-tier-only per the locked
constraint enforced by ``create_feature_folder``).  Unknown tiers refuse
via :class:`ValueError` BEFORE any mutation, so the seed slot can never
leave a partial folder behind.

Coverage AC: 100% on this module (M3 P6.1 plan §AC #5; preserved by P6.2).
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
    scan_existing_capabilities,
    slug_from_idea,
)
from tools.state import (
    VALID_TIERS,
    feature_folder_exists,
    record_routing_decision,
)

# Schema-aligned capability slug pattern.  Mirrors
# ``tools.archive._CAPABILITY_SLUG_SCHEMA_RE`` (≥3 chars, alnum-leading,
# no trailing hyphen, no consecutive hyphens — M6 finding L1).  Used
# to validate the operator-supplied ``feature_slug`` for the suffix-disambig
# branch — the chosen ``<slug>-v2`` / ``<slug>-bulk`` slug must satisfy the
# same shape as a slug derived from ``slug_from_idea``.
_FEATURE_SLUG_RE: re.Pattern[str] = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,}$")

# Mirrors ``schemas/state.schema.json`` ``routing.idea.maxLength``.  The
# helper pre-validates length BEFORE any disk mutation so an overlong idea
# surfaces a clean cap error instead of the schema's verbose validation
# dump (which inlines the entire payload — M6 finding M7).
_IDEA_MAX_CHARS: int = 4000


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

      1. ``final_tier not in VALID_TIERS`` → :class:`ValueError` BEFORE any
         disk mutation.  All three tiers (``focused`` / ``standard`` /
         ``full``) survive this gate.
      2. Derive ``current_phase`` from ``final_tier``:
         ``full`` → ``"refine"``; ``focused`` / ``standard`` → ``"spec"``.
         The full-tier branch relies on ``create_feature_folder`` to enforce
         the refine⇒full constraint (M3 P6.2 T1).
      3. Compute ``today_iso`` from ``today`` (or ``date.today()`` when
         omitted) and ``feature_id = f"{today_iso}-{slug_from_idea(idea)}"``
         (or ``feature_slug`` when given for suffix-disambig).
      4. Resolve ``schema_path = repo_root / "schemas/state.schema.json"``.
      5. Pre-collision check via :func:`feature_folder_exists` →
         :class:`ArchiveError` on True (no disk mutation yet).
      6. Call :func:`create_feature_folder` with ``current_phase`` (seed
         body validated against schema BEFORE any disk write).
      7. Call :func:`record_routing_decision` against the new state.json.
         On ANY exception (schema refusal, OSError, KeyboardInterrupt,
         SystemExit), invoke :func:`cleanup_seeded_feature` BEFORE
         re-raising the original exception with traceback intact.  Cleanup
         is best-effort: if it itself raises, the original exception still
         re-raises (the cleanup failure is suppressed; ``cleanup_seeded_feature``
         never raises on the supported ``(spec|refine, in_progress)`` orphan
         shapes so this branch only hits when the predicate has shifted out
         from under us, in which case the partial folder is the lesser of
         two evils).

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        idea: User-supplied idea text.  Persisted verbatim into
            ``state.json.routing.idea`` — the skill prints a one-line
            secrets warning before invoking this helper.
        final_tier: ``focused``, ``standard``, or ``full``.  Full tier
            seeds ``current_phase="refine"``; focused/standard seed
            ``current_phase="spec"``.  Any other value raises
            :class:`ValueError`.
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
        Path to the new ``.forge/features/<feature_id>/`` folder.  The
        skill renders the dispatch literal based on tier:
        focused/standard → ``Next: /forge:spec --feature <feature_id>``;
        full → ``Next: /forge:refine --feature <feature_id>``.

    Raises:
        ValueError: ``final_tier`` is not in ``VALID_TIERS``; OR
            ``feature_slug`` is given but does not satisfy the slug
            pattern.  No folder created in either case.
        ArchiveError: Folder already exists (collision), or
            :func:`create_feature_folder` itself rejects the seed.
        StateError: ``record_routing_decision`` rejected the routing
            block (e.g. invalid timestamp / bogus tier); the seeded
            folder is removed before re-raise.
        BaseException: ``KeyboardInterrupt`` / ``SystemExit`` between
            steps 6 and 7 also trigger the cleanup wrapper before
            re-raising.
    """
    # Step 1: refuse unknown tiers BEFORE any disk mutation.  All three
    # known tiers (focused/standard/full) survive this gate as of P6.2.
    if final_tier not in VALID_TIERS:
        raise ValueError(f"invalid final_tier {final_tier!r}; must be one of {VALID_TIERS}")

    # M6 finding M7: pre-validate idea length BEFORE any other mutation
    # so the operator sees a clean cap error instead of the schema's
    # 6000-char ValidationError dump (which inlines the full payload).
    # The schema mirrors the same 4000-char cap; we surface the friendly
    # message here so the routing helper owns the user-visible wording.
    if len(idea) > _IDEA_MAX_CHARS:
        raise ValueError(
            f"idea exceeds {_IDEA_MAX_CHARS}-char cap (got {len(idea)} chars); "
            "trim before /forge:do"
        )

    # Step 2: derive seed entry phase from tier.  Full tier enters at
    # refine (Socratic loop owns the spec hand-off via complete_phase +
    # start_phase later).  Focused/standard enter at spec directly.
    # ``create_feature_folder`` enforces the refine⇒full constraint so a
    # mis-paired (refine, focused) seed would refuse before any disk write.
    current_phase = "refine" if final_tier == "full" else "spec"

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
        # H1: an operator-supplied feature_slug bypasses the SKILL-layer
        # capability scan (forge-do step 4). Re-run the scan here so an
        # operator who hand-picks a slug naming an existing canonical
        # capability cannot seed silently and waste downstream phases.
        # Suffix-disambig slugs (`<canonical>-v2`, `<canonical>-bulk`) are
        # NOT in the scan result (they are NEW slugs distinct from the
        # canonical one) so this guard only fires on a literal collision.
        if feature_slug in scan_existing_capabilities(repo_root):
            raise ArchiveError(
                f"feature_slug {feature_slug!r} clashes with existing canonical capability"
            )
        slug = feature_slug
    else:
        slug = slug_from_idea(idea)
    feature_id = f"{today_iso}-{slug}"

    # Step 4: schema path passed to BOTH helpers so payload validation
    # refuses BEFORE any disk write. M6 finding M9: pre-check the schema
    # file exists and surface a clean RuntimeError naming the missing
    # path so the operator does not see a raw FileNotFoundError bubble
    # up from inside write_state.
    schema_path = repo_root / "schemas" / "state.schema.json"
    if not schema_path.is_file():
        raise RuntimeError(
            f"schemas/state.schema.json missing under {repo_root}; verify plugin install path"
        )

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
        current_phase=current_phase,
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
        except (KeyboardInterrupt, SystemExit):
            # M6 deep-tester finding M6: a user-cancel signal mid-rollback
            # (Ctrl-C / SystemExit) MUST propagate so the operator sees the
            # actual cancel, instead of being silently masked by the original
            # routing exception. The partial folder may remain on disk —
            # surface that in the WARN so the operator can clean up manually.
            print(
                "WARN: USER CANCELLED MID-ROLLBACK; "
                f"partial folder may remain at .forge/features/{feature_id}",
                file=sys.stderr,
            )
            raise
        except Exception:
            # Cleanup itself raised a non-cancel exception. Log and fall
            # through to re-raise the ORIGINAL record_routing_decision
            # exception, NOT the cleanup one — the original carries the
            # actionable failure mode.
            print(
                "WARN: cleanup_seeded_feature raised during post-seed rollback; "
                "original exception below",
                file=sys.stderr,
            )
        # L7: re-raise the original exception WITHOUT ``from None`` so the
        # ``__cause__`` / ``__context__`` chain is preserved. The cleanup
        # exception is already swallowed via the inner except branches, so
        # chaining cannot reintroduce it; the chain only carries the
        # original record_routing_decision traceback.
        raise original

    return folder
