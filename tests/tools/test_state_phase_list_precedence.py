"""``next_phase_command`` consults ``routing.phase_list`` before falling back.

Locks the resolution order documented on :func:`tools.state.next_phase_command`:

1. ``routing.phase_list`` is the source of truth when present, non-empty, AND
   ``current_phase`` appears in it (custom ordering wins over the per-tier
   static table; ``review`` still delegates to the targets-aware helper;
   ``ship → qa`` still keeps the ``--against merged`` flag).
2. End of phase_list resolves to ``None``.
3. ``current_phase`` not in phase_list → fallback to the static table
   (preserves backward compatibility for legacy / inconsistent state).
4. Empty / absent phase_list → fallback to the static table.
"""

from __future__ import annotations

from typing import Any

from tools import state


def _payload(
    *,
    tier: str,
    current_phase: str,
    phase_list: list[str] | None,
    review_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal state payload with optional ``routing.phase_list``.

    The shape mirrors ``tests/tools/test_state_next_phase.py`` so the two
    suites stay parallel; the only delta is the optional ``routing`` block
    so the precedence path can be exercised in isolation.
    """
    if current_phase == "review":
        review_block: dict[str, Any] = {"status": "in_progress"}
        if review_extra is not None:
            review_block.update(review_extra)
        phases: dict[str, Any] = {current_phase: review_block}
    else:
        phases = {current_phase: {"status": "in_progress"}}

    routing: dict[str, Any] = {}
    if phase_list is not None:
        routing["phase_list"] = phase_list

    return {
        "feature_id": "2026-05-11-phase-list-precedence",
        "tier": tier,
        "current_phase": current_phase,
        "phases": phases,
        "skipped": [],
        "deviations": [],
        "commits": [],
        "routing": routing,
    }


def test_phase_list_overrides_static_table_for_custom_ordering() -> None:
    """A phase_list that skips scenarios + crucible still drives the next call.

    Standard tier's static next table after ``spec`` is ``/forge:scenarios``;
    a phase_list of ``[spec, plan, ...]`` must take precedence and route the
    feature into ``/forge:plan`` directly.
    """
    payload = _payload(
        tier="standard",
        current_phase="spec",
        phase_list=["spec", "plan", "execute", "verify", "ship"],
    )
    assert state.next_phase_command(payload) == "/forge:plan"


def test_phase_list_review_next_delegates_to_review_helper() -> None:
    """When phase_list says ``review`` is next, the targets-aware helper runs.

    Empty ``targets_done`` → ``/forge:review --target plan`` per the existing
    review two-pass semantics; the precedence path must not bypass that.
    """
    payload = _payload(
        tier="standard",
        current_phase="plan",
        phase_list=["spec", "plan", "review", "execute", "verify", "ship"],
        review_extra=None,
    )
    # Add an empty review block so the helper has somewhere to look up
    # ``targets_done``; the static table never reaches review here because
    # current_phase=plan resolves through phase_list straight to review.
    payload["phases"]["review"] = {"status": "pending", "targets_done": []}
    assert state.next_phase_command(payload) == "/forge:review --target plan"


def test_phase_list_ship_to_qa_keeps_against_merged_flag() -> None:
    """ship → qa transition retains ``--against merged`` when phase_list applies.

    Mirrors the static ``_STANDARD_NEXT["ship"]`` entry so callers see the
    same slash literal whether the precedence or the fallback path resolves.
    """
    payload = _payload(
        tier="standard",
        current_phase="ship",
        phase_list=["spec", "plan", "execute", "verify", "ship", "qa"],
    )
    assert state.next_phase_command(payload) == "/forge:qa --against merged"


def test_phase_list_terminal_entry_returns_none() -> None:
    """``current_phase`` is the last entry → no next command (overrides table)."""
    payload = _payload(
        tier="standard",
        current_phase="qa",
        phase_list=["spec", "plan", "execute", "verify", "ship", "qa"],
    )
    assert state.next_phase_command(payload) is None


def test_phase_list_unknown_current_phase_falls_back_to_static_table() -> None:
    """Legacy / inconsistent state where ``current_phase`` is not in the list.

    Falls back to the static table; this preserves backward compatibility
    for hand-edited or pre-routing state.json fixtures.
    """
    payload = _payload(
        tier="standard",
        current_phase="spec",
        phase_list=["plan", "execute", "verify", "ship"],
    )
    # Static table for standard tier, spec → /forge:scenarios.
    assert state.next_phase_command(payload) == "/forge:scenarios"


def test_empty_phase_list_falls_back_to_static_table() -> None:
    """Empty list is treated as absent — the static table drives the next call."""
    payload = _payload(
        tier="standard",
        current_phase="spec",
        phase_list=[],
    )
    assert state.next_phase_command(payload) == "/forge:scenarios"


def test_absent_phase_list_falls_back_to_static_table() -> None:
    """No ``phase_list`` field at all → fallback (the legacy behavior)."""
    payload = _payload(
        tier="standard",
        current_phase="spec",
        phase_list=None,
    )
    assert state.next_phase_command(payload) == "/forge:scenarios"


def test_focused_tier_honors_phase_list_when_present() -> None:
    """Focused never gets a phase_list write under normal seeding, but if a
    caller injects one explicitly the precedence still applies.
    """
    payload = _payload(
        tier="focused",
        current_phase="spec",
        phase_list=["spec", "verify"],  # custom: skip execute
    )
    assert state.next_phase_command(payload) == "/forge:verify"


def test_focused_tier_without_phase_list_still_uses_static_table() -> None:
    """Backward-compat sanity: focused tier without phase_list keeps its
    legacy static routing.
    """
    payload = _payload(
        tier="focused",
        current_phase="spec",
        phase_list=None,
    )
    assert state.next_phase_command(payload) == "/forge:execute"
