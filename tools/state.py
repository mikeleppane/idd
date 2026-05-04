"""Read, write, and transition feature state.json files."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    """Raised when state.json cannot be read, parsed, or transitioned."""


def read_state(path: Path) -> dict[str, Any]:
    """Read and parse a state.json file.

    Raises:
        StateError: file does not exist, or content is not valid JSON.
    """
    if not path.exists():
        raise StateError(f"state.json not found at {path}")
    try:
        parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"state.json at {path} is invalid JSON: {exc}") from exc
    return parsed


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
