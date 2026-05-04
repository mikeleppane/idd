"""Smoke tests for the IDD focused-tier reference fixture.

These do not run live skills or slash commands; live plugin dogfood is the Task 28 release gate. These tests verify:
  - fixture artifacts pass the SPEC and state schemas;
  - every skill/command frontmatter passes lint;
  - the negative fixtures are correctly rejected by the validators;
  - the dummy target_repo's own unit tests pass (sanity-check the fixture);
  - state-machine transitions through the focused tier produce the expected JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools import lint_frontmatter as lint
from tools import state

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bug-fix-focused"
NEGATIVE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "_negative"


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse SPEC/VERIFICATION frontmatter via the production parser.

    `lint.parse_frontmatter` already coerces YAML date/datetime values to ISO
    strings, so smoke tests do not need their own coercion logic.
    """
    parsed = lint.parse_frontmatter(path)
    assert parsed is not None, f"no frontmatter found in {path}"
    return parsed


def test_input_idea_is_nonempty() -> None:
    idea = FIXTURE_DIR / "INPUT_idea.md"
    assert idea.exists()
    assert idea.read_text(encoding="utf-8").strip(), "INPUT_idea.md must not be empty"


def test_expected_spec_frontmatter_satisfies_spec_schema(
    repo_root: Path, schemas_dir: Path
) -> None:
    spec = FIXTURE_DIR / "EXPECTED_SPEC.md"
    fm = _read_frontmatter(spec)
    schema = json.loads((schemas_dir / "spec-frontmatter.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(fm)


def test_expected_spec_has_all_template_sections() -> None:
    spec = FIXTURE_DIR / "EXPECTED_SPEC.md"
    body = spec.read_text(encoding="utf-8")
    for section in (
        "# Intent",
        "# Context",
        "# Domain",
        "# Codebase Anchors",
        "# Scope",
        "# Scenarios",
        "# Test Strategy",
        "# Acceptance Criteria",
        "# Negative Requirements",
        "# Open Questions",
    ):
        assert section in body, f"missing section: {section}"


def test_expected_verification_frontmatter_has_spec_id() -> None:
    ver = FIXTURE_DIR / "EXPECTED_VERIFICATION.md"
    fm = _read_frontmatter(ver)
    assert "spec" in fm
    assert "generated" in fm


def test_state_json_template_passes_schema(repo_root: Path, schemas_dir: Path) -> None:
    template = repo_root / "templates" / "feature" / "state.json"
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    payload: dict[str, Any] = json.loads(template.read_text(encoding="utf-8"))
    payload["feature_id"] = "2026-05-03-template-check"
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(payload)


def test_state_machine_walks_focused_tier_to_done(tmp_path: Path, schemas_dir: Path) -> None:
    """Drive a state.json from spec → execute → verify → done using the public API."""
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-walk-check",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-03T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema)

    state.complete_phase(target, "spec", schema_path=schema, now="2026-05-03T11:00:00Z")
    state.start_phase(target, "execute", schema_path=schema, now="2026-05-03T11:01:00Z")
    state.complete_phase(target, "execute", schema_path=schema, now="2026-05-03T12:00:00Z")
    state.start_phase(target, "verify", schema_path=schema, now="2026-05-03T12:01:00Z")
    state.complete_phase(target, "verify", schema_path=schema, now="2026-05-03T13:00:00Z")
    final = state.finish_feature(target, schema_path=schema)

    assert final["current_phase"] == "done"
    assert final["phases"]["spec"]["status"] == "done"
    assert final["phases"]["execute"]["status"] == "done"
    assert final["phases"]["verify"]["status"] == "done"
    assert "done" not in final["phases"]


def test_negative_invalid_state_is_rejected(repo_root: Path, schemas_dir: Path) -> None:
    bad = NEGATIVE_DIR / "invalid_state.json"
    with pytest.raises(state.StateError):
        state.read_state(bad, schema_path=schemas_dir / "state.schema.json")


def test_negative_broken_frontmatter_is_rejected(repo_root: Path) -> None:
    with pytest.raises(lint.FrontmatterError):
        lint.parse_frontmatter(NEGATIVE_DIR / "broken_frontmatter.md")


def test_negative_empty_idea_is_detected() -> None:
    empty = NEGATIVE_DIR / "empty_idea.md"
    assert not empty.read_text(encoding="utf-8").strip(), "empty_idea fixture must be empty"


@pytest.mark.parametrize(
    "rel",
    [
        "skills/idd-spec/SKILL.md",
        "skills/idd-execute/SKILL.md",
        "skills/idd-verify/SKILL.md",
        "skills/idd-context-budget/SKILL.md",
        "skills/idd-subagent-dispatch/SKILL.md",
        "commands/spec.md",
        "commands/execute.md",
        "commands/verify.md",
    ],
)
def test_every_skill_and_command_passes_frontmatter_lint(
    repo_root: Path, schemas_dir: Path, rel: str
) -> None:
    target = repo_root / rel
    assert target.exists(), f"missing source file: {rel}"
    errors = lint.validate_file(target, schemas_dir / "frontmatter.schema.json")
    assert errors == [], f"{rel} failed frontmatter lint: {errors}"


def test_target_repo_unit_tests_pass() -> None:
    """Run the dummy target_repo's pytest suite to sanity-check the fixture."""
    target_root = FIXTURE_DIR / "target_repo"
    rc = subprocess.run(
        [sys.executable, "-m", "pytest", str(target_root / "tests"), "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rc.returncode == 0, f"target_repo tests failed:\n{rc.stdout}\n{rc.stderr}"
