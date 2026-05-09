"""Phase enum parity between budget schema, state schema, and the hook.

The hook ``hooks/check_budget.py`` carries a frozen literal
(``_EXECUTE_PHASE = "execute"``) so it stays stdlib-only and does not import
the budget schema at runtime. ``schemas/budget.schema.json`` documents the
full phase enum for human readers. ``schemas/state.schema.json`` is the
canonical source for valid lifecycle phase names.

Drift between the three is caught here:

1. The budget schema's ``phase`` enum equals ``state.schema.json``'s
   ``phases.propertyNames`` enum (the per-phase keys).
2. The hook's ``_EXECUTE_PHASE`` literal appears in both schemas.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
BUDGET_SCHEMA = REPO_ROOT / "schemas" / "budget.schema.json"
STATE_SCHEMA = REPO_ROOT / "schemas" / "state.schema.json"
HOOK = REPO_ROOT / "hooks" / "check_budget.py"


def _load_json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _budget_phase_enum() -> list[str]:
    schema = _load_json(BUDGET_SCHEMA)
    enum = schema["properties"]["phase"]["enum"]
    assert isinstance(enum, list)
    return [str(item) for item in enum]


def _state_phases_propertynames_enum() -> list[str]:
    schema = _load_json(STATE_SCHEMA)
    enum = schema["properties"]["phases"]["propertyNames"]["enum"]
    assert isinstance(enum, list)
    return [str(item) for item in enum]


def _load_hook_module() -> Any:
    spec = importlib.util.spec_from_file_location("check_budget_parity", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_budget_phase_enum_matches_state_phases_propertynames() -> None:
    """The budget schema documents the same lifecycle phases the state
    schema accepts as keys under ``phases``."""
    assert _budget_phase_enum() == _state_phases_propertynames_enum()


def test_hook_execute_phase_literal_in_budget_schema_enum() -> None:
    module = _load_hook_module()
    assert module._EXECUTE_PHASE in _budget_phase_enum()


def test_hook_execute_phase_literal_in_state_schema_phases() -> None:
    module = _load_hook_module()
    assert module._EXECUTE_PHASE in _state_phases_propertynames_enum()
