"""Smoke test for the M2 standard-tier surface.

This test does not invoke an LLM. It proves that:
- All M2 commands and skills carry compliant frontmatter (validated against
  the live ``frontmatter.schema.json`` + lint quality bar).
- The state machine accepts the full standard-tier phase chain in order,
  including the dual review pass (target=plan, then target=code).
- ``bdd_detect.detect`` resolves the fixture ``target_repo`` to the
  python+pytest-bdd binding and falls back to ``None`` when signals are
  partial (declared dep but no features dir) or absent (empty repo).
- Archive helpers move a seeded feature folder, write a canonical spec,
  and ``ship_feature`` performs both writes transactionally.
- Every ``EXPECTED_*.md`` fixture validates against its matching schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from tools import archive, bdd_detect, state
from tools.lint_frontmatter import parse_frontmatter, validate_file

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "feature-flag-standard"
SCHEMAS_DIR = REPO_ROOT / "schemas"
FRONTMATTER_SCHEMA = SCHEMAS_DIR / "frontmatter.schema.json"


M2_COMMANDS = [
    REPO_ROOT / "commands" / name
    for name in ("scenarios.md", "plan.md", "crucible.md", "review.md", "ship.md")
]

M2_SKILLS = [
    REPO_ROOT / "skills" / name / "SKILL.md"
    for name in ("idd-scenarios", "idd-plan", "idd-crucible", "idd-review", "idd-ship")
]

# The standard-tier flow visits `review` twice — once after crucible
# (target=plan), once after execute (target=code). The state machine has a
# single `review` slot, so the second pass overwrites the first slot's
# timestamps; the durable record is the per-target REVIEW file
# (REVIEW.plan.md / REVIEW.code.md), not state.json.
STANDARD_TIER_FLOW = (
    "spec",
    "scenarios",
    "plan",
    "crucible",
    "review",  # target=plan
    "execute",
    "review",  # target=code
    "verify",
    "ship",
)

# Each EXPECTED fixture artifact -> matching schema file.
FIXTURE_FRONTMATTER_BINDINGS = [
    ("EXPECTED_SPEC.md", "spec-frontmatter.schema.json"),
    ("EXPECTED_PLAN.md", "plan-frontmatter.schema.json"),
    ("EXPECTED_UNDERSTANDING.md", "understanding-frontmatter.schema.json"),
    ("EXPECTED_REVIEW.plan.md", "review-frontmatter.schema.json"),
    ("EXPECTED_REVIEW.code.md", "review-frontmatter.schema.json"),
    ("EXPECTED_CAPABILITY_SPEC.md", "capability-spec-frontmatter.schema.json"),
]


@pytest.mark.parametrize(
    "path",
    M2_COMMANDS + M2_SKILLS,
    ids=lambda p: p.relative_to(REPO_ROOT).as_posix(),
)
def test_m2_surface_lints_against_frontmatter_schema(path: Path) -> None:
    """Every M2 command + skill passes the full lint bar (schema + description quality)."""
    assert path.exists(), f"missing M2 source file: {path}"
    errors = validate_file(path, FRONTMATTER_SCHEMA)
    assert not errors, f"{path} lint errors: {errors}"


def test_bdd_detect_fixture_returns_python_pytest_bdd() -> None:
    """The fixture target_repo declares pytest-bdd and ships a tests/features dir."""
    result = bdd_detect.detect(FIXTURE / "target_repo")
    assert isinstance(result, bdd_detect.Detected)
    assert result.framework.ecosystem == "python"
    assert result.framework.framework == "pytest-bdd"


def test_bdd_detect_returns_ambiguous_when_dep_present_but_features_dir_missing(
    tmp_path: Path,
) -> None:
    """Partial signal — calling skill must ask the user once and cache the answer."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\ndependencies = ['pytest-bdd']\n",
        encoding="utf-8",
    )
    result = bdd_detect.detect(tmp_path)
    assert isinstance(result, bdd_detect.Ambiguous)
    assert "tests/features" in result.reason


def test_bdd_detect_returns_not_detected_for_empty_repo(tmp_path: Path) -> None:
    assert bdd_detect.detect(tmp_path) == bdd_detect.NotDetected()


def test_state_machine_accepts_full_standard_tier_flow_with_dual_review(tmp_path: Path) -> None:
    feature_id = "2026-05-04-feature-flag-killswitch"
    feature_dir = tmp_path / ".idd" / "features" / feature_id
    feature_dir.mkdir(parents=True)
    state_path = feature_dir / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": feature_id,
                "tier": "standard",
                "current_phase": "spec",
                "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-04T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    schema = SCHEMAS_DIR / "state.schema.json"

    # Walk the flow: spec is already in_progress; everything else needs start_phase
    # before complete_phase. The two `review` entries hit the same slot — the
    # second call overwrites the first.
    for index, phase in enumerate(STANDARD_TIER_FLOW):
        if not (index == 0 and phase == "spec"):
            state.start_phase(state_path, phase, schema_path=schema)
        state.complete_phase(state_path, phase, schema_path=schema)

    state.finish_feature(state_path, schema_path=schema)
    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert final["current_phase"] == "done"
    # Every distinct phase shows status=done. Review's slot reflects the SECOND pass.
    for phase in set(STANDARD_TIER_FLOW):
        assert final["phases"][phase]["status"] == "done", f"phase {phase} not done"


def test_archive_round_trip(tmp_path: Path) -> None:
    feature_id = "2026-05-04-feature-flag-killswitch"
    capability = "feature-flag"
    seed = tmp_path / ".idd" / "features" / feature_id
    seed.mkdir(parents=True)
    (seed / "SPEC.md").write_text("# spec\n", encoding="utf-8")
    (seed / "state.json").write_text("{}\n", encoding="utf-8")

    archived = archive.archive_feature(tmp_path, feature_id)
    assert archived.is_dir()
    assert (archived / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"

    body = (
        "---\ncapability: feature-flag\nstatus: shipped\ncreated: 2026-05-04\n"
        "last_updated: 2026-05-04\nevidence:\n"
        "  - 2026-05-04-feature-flag-killswitch: features/archive/2026-05-04-feature-flag-killswitch/\n"
        "bounded_context: null\n---\n# Feature Flag\n"
    )
    written = archive.write_canonical_spec(tmp_path, capability, body)
    assert written.read_text(encoding="utf-8") == body


def test_ship_feature_transactional_round_trip(tmp_path: Path) -> None:
    feature_id = "2026-05-04-feature-flag-killswitch"
    capability = "feature-flag"
    seed = tmp_path / ".idd" / "features" / feature_id
    seed.mkdir(parents=True)
    (seed / "SPEC.md").write_text("# spec\n", encoding="utf-8")

    spec, archived = archive.ship_feature(
        tmp_path,
        feature_id,
        capability,
        body="---\ncapability: feature-flag\nstatus: shipped\n---\n# Feature Flag\n",
    )
    assert spec.is_file()
    assert archived.is_dir()
    assert not (tmp_path / ".idd" / "features" / feature_id).exists()


@pytest.mark.parametrize(
    "fixture_name,schema_name",
    FIXTURE_FRONTMATTER_BINDINGS,
    ids=[name for name, _ in FIXTURE_FRONTMATTER_BINDINGS],
)
def test_fixture_artifact_frontmatter_validates(fixture_name: str, schema_name: str) -> None:
    """Every EXPECTED_*.md in the fixture validates against its matching schema."""
    fm = parse_frontmatter(FIXTURE / fixture_name)
    assert fm is not None, f"{fixture_name} missing frontmatter"
    schema = json.loads((SCHEMAS_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    ).validate(fm)
