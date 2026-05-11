"""Regression guard: every tracked state.json still validates after the bump.

Walks ``git ls-files '*state.json'`` and asserts each tracked file validates
against the current ``schemas/state.schema.json``. Catches accidental
tightening of the schema that would invalidate legacy fixtures under
``tests/fixtures/**`` and ``tests/smoke/**``.

Exclusions:

* ``templates/feature/state.json`` — the bare scaffold under templates is
  intentionally minimal and predates several required fields.
* Anything under a ``_negative/`` directory — the negative-fixture pool is
  intentionally invalid (used by the focused-tier smoke test to assert
  rejection).
* ``tests/fixtures/_validate/deviations_unparseable_state/state.json`` — a
  deliberately malformed JSON fixture used by the deviations-validator tests
  to exercise the "state.json is unparseable" path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

_EXCLUDED_RELATIVE = frozenset(
    {
        "templates/feature/state.json",
        "tests/fixtures/_validate/deviations_unparseable_state/state.json",
    }
)


def _tracked_state_files(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*state.json"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if rel in _EXCLUDED_RELATIVE:
            continue
        if "_negative/" in rel:
            continue
        paths.append(repo_root / rel)
    return paths


def _validator_for(schemas_dir: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def test_tracked_state_files_set_is_non_empty(repo_root: Path) -> None:
    """Sanity: at least one tracked state.json exists, otherwise the loop is hollow."""
    files = _tracked_state_files(repo_root)
    assert files, "expected at least one tracked state.json after exclusions"


def test_every_tracked_state_file_validates(repo_root: Path, schemas_dir: Path) -> None:
    validator = _validator_for(schemas_dir)
    failures: list[str] = []
    for path in _tracked_state_files(repo_root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{path}: invalid JSON — {exc}")
            continue
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            failures.append(f"{path}: {messages}")
    if failures:
        pytest.fail("tracked state.json files failed schema validation:\n" + "\n".join(failures))
