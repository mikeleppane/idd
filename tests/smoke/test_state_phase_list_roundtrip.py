"""Smoke: explicit ``routing.phase_list`` survives the write/read cycle
and ``get_phase_list`` returns it verbatim (does not re-derive from
``tier``). Exercises the explicit-set branch of the lazy accessor;
distinct from the no-writeback walker, which exercises the legacy
derive-from-tier branch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.state import get_phase_list, read_state, write_state


def test_explicit_phase_list_survives_round_trip(tmp_path: Path) -> None:
    explicit_phase_list = ["refine", "spec", "plan", "execute", "ship"]
    payload: dict[str, Any] = {
        "feature_id": "2026-05-09-explicit-phase-list-smoke",
        "tier": "standard",
        "current_phase": "refine",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": [],
        "routing": {
            "phase_list": list(explicit_phase_list),
        },
    }

    state_path = tmp_path / "state.json"
    write_state(state_path, payload)

    reloaded = read_state(state_path)

    routing = reloaded.get("routing")
    assert isinstance(routing, dict)
    assert routing.get("phase_list") == explicit_phase_list

    resolved = get_phase_list(reloaded)
    assert resolved == explicit_phase_list

    # Defensive: the accessor returns a fresh copy so caller mutation
    # does not bleed back into the on-disk-derived structure.
    assert resolved is not routing.get("phase_list")
