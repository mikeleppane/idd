"""Single-source-of-truth lock for the shared phase vocabulary.

Three sources describe the same lifecycle phase set:

* ``current_phase.enum`` in ``state.schema.json`` (12 phases + the terminal
  ``done`` state).
* ``routing.phase_list.items.enum`` in ``state.schema.json`` (the same 12
  phases — ``done`` is never a phase to execute).
* ``tools.state.VALID_LIFECYCLE_PHASES`` (the 12-tuple Python consumers
  validate against in ``complete_phase`` / ``start_phase``).

Drift between any two of these is silent and corrosive: a phase added to the
schema but not the Python tuple silently disables every state transition for
that phase; a phase added to the Python tuple but not the schema lets a
tampered ``state.json`` slip through validation. This test makes drift loud
across all three sources.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.state import VALID_LIFECYCLE_PHASES


def test_phase_list_enum_matches_current_phase_minus_done(schemas_dir: Path) -> None:
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))

    current_phase_enum = set(schema["properties"]["current_phase"]["enum"])
    phase_list_enum = set(
        schema["properties"]["routing"]["properties"]["phase_list"]["items"]["enum"]
    )

    assert phase_list_enum == current_phase_enum - {"done"}, (
        "phase_list.items.enum must equal current_phase.enum minus 'done'; "
        f"current_phase={sorted(current_phase_enum)}, "
        f"phase_list={sorted(phase_list_enum)}"
    )


def test_python_lifecycle_tuple_matches_phase_list_schema_enum(schemas_dir: Path) -> None:
    """``tools.state.VALID_LIFECYCLE_PHASES`` must equal the schema enum
    *element-for-element and in the same order* — order matters because the
    tuple is consumed positionally by lifecycle-ordering checks downstream.
    """
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    schema_enum = tuple(
        schema["properties"]["routing"]["properties"]["phase_list"]["items"]["enum"]
    )

    assert schema_enum == VALID_LIFECYCLE_PHASES, (
        "VALID_LIFECYCLE_PHASES drifted from phase_list.items.enum; "
        f"schema={schema_enum}, python={VALID_LIFECYCLE_PHASES}"
    )


def test_python_lifecycle_tuple_matches_current_phase_minus_done(schemas_dir: Path) -> None:
    """Belt-and-braces: the Python tuple must also match
    ``current_phase.enum`` minus the terminal ``done`` state, *as a set*
    (the ``current_phase`` enum carries ``done`` at the end, which is not a
    phase the lifecycle tuple enumerates).
    """
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    current_phase_enum = set(schema["properties"]["current_phase"]["enum"])

    assert set(VALID_LIFECYCLE_PHASES) == current_phase_enum - {"done"}
