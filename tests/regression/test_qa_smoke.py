"""End-to-end smoke regression for ``tools.validate.qa_shape``.

Drives the validator against two committed feature-shaped fixtures:

- ``2026-05-08-qa-delivers-feature``: a well-formed positive case with
  matching frontmatter + section statuses. Validator must return zero
  findings.
- ``2026-05-08-qa-blocks-feature``: a negative case where frontmatter
  ``verdict`` does not match the ``# Acceptance`` Status AND declared
  ``confidence`` does not aggregate from the per-section statuses.
  Validator must return at least two BLOCK findings, including
  ``qa_shape:verdict_mismatch`` and
  ``qa_shape:confidence_aggregation_mismatch``.

The fixtures live under ``tests/smoke/<feature_id>/.forge/features/<feature_id>/``,
mirroring the layout already used by ``2026-05-08-tdd-paired-feature``.
"""

from __future__ import annotations

from pathlib import Path

from tools.validate._finding import Finding
from tools.validate.qa_shape import validate_qa_shape

SMOKE_ROOT = Path(__file__).resolve().parents[1] / "smoke"

DELIVERS_FIXTURE_ID = "2026-05-08-qa-delivers-feature"
BLOCKS_FIXTURE_ID = "2026-05-08-qa-blocks-feature"


def test_qa_smoke_fixtures_present_at_known_paths() -> None:
    """Sanity check the fixture directories exist where the validator looks."""
    for feature_id in (DELIVERS_FIXTURE_ID, BLOCKS_FIXTURE_ID):
        feature_dir = SMOKE_ROOT / feature_id / ".forge" / "features" / feature_id
        assert feature_dir.is_dir(), f"missing fixture dir {feature_dir}"
        assert (feature_dir / "QA.md").is_file(), f"missing QA.md in {feature_dir}"
        assert (feature_dir / "state.json").is_file(), f"missing state.json in {feature_dir}"
        assert (feature_dir / "SPEC.md").is_file(), f"missing SPEC.md in {feature_dir}"
        assert (feature_dir / "decisions.md").is_file(), f"missing decisions.md in {feature_dir}"


def test_qa_smoke_delivers_fixture_passes() -> None:
    """Positive fixture: frontmatter agrees with section statuses, zero findings."""
    repo_root = SMOKE_ROOT / DELIVERS_FIXTURE_ID
    findings: list[Finding] = validate_qa_shape(repo_root, DELIVERS_FIXTURE_ID)
    assert findings == [], [(f.severity, f.message) for f in findings]


def test_qa_smoke_blocks_fixture_emits_findings() -> None:
    """Negative fixture: verdict + confidence both lie; at least two BLOCKs."""
    repo_root = SMOKE_ROOT / BLOCKS_FIXTURE_ID
    findings: list[Finding] = validate_qa_shape(repo_root, BLOCKS_FIXTURE_ID)
    blocks = [f for f in findings if f.severity == "BLOCK"]
    assert len(blocks) >= 2, [(f.severity, f.message) for f in findings]
    codes = {f.message.split(" ", 1)[0].rstrip(":—-") for f in blocks}
    assert "qa_shape:verdict_mismatch" in codes, codes
    assert "qa_shape:confidence_aggregation_mismatch" in codes, codes
