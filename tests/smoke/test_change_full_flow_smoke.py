"""E2E smoke tests for /forge:change full flow (merge_delta_proposal).

EXPECTED_canonical_after.md is a generated locked baseline: computed once by
running apply_delta_ops against the fixture canonical and proposal, then
committed.  Regenerate only when delta-op semantics change (plan D-7).

Three walks:
  1. Happy path — full flow from approved proposal to merged canonical +
     archive, including snapshot fidelity assertions.
  2. Rollback on validator fail — monkeypatch validate_capability_spec_sections
     to inject a BLOCK finding; assert canonical + proposal.md untouched, no
     archive folder created.
  3. Retry-idempotency — second merge attempt on the same change_id after
     success raises ArchiveError("archive already exists").

Skills cited (per plan body):
  - .agents/skills/test-driven-development/SKILL.md
  - .agents/skills/coding-guidance-python/SKILL.md
  - .agents/skills/git-conventions/SKILL.md
  - .agents/skills/code-review-and-quality/SKILL.md
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.archive import ArchiveError, _mark_change_merged_hook, merge_delta_proposal
from tools.validate._finding import Finding

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "feature-flag-delta-merge"
_CHANGE_ID = "2026-05-08-add-percent-rollout"
_CAPABILITY = "feature-flag"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _copy_target_repo(tmp_path: Path) -> Path:
    """Copy fixture target_repo to tmp_path/repo; return repo root."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT / "target_repo", dest)
    return dest


# ---------------------------------------------------------------------------
# Test 1 — Happy path full flow
# ---------------------------------------------------------------------------


def test_happy_path_full_flow(tmp_path: Path) -> None:
    """Full merge: canonical updated, archive created, snapshots fidelity verified.

    Steps:
      1. Copy fixture target_repo to tmp_path.
      2. Construct hook via _mark_change_merged_hook.
      3. Call merge_delta_proposal; assert (canonical_path, archive_path) returned.
      4. Canonical content matches EXPECTED_canonical_after.md.
      5. Archive folder exists at .forge/changes/archive/<change_id>/.
      6. archive/canonical-pre.md == EXPECTED_canonical_pre_snapshot.md.
      7. archive/proposal-pre.md == EXPECTED_proposal_pre_snapshot.md.
      8. Archived proposal.md has frontmatter status: merged (hook ran).
      9. Original change folder no longer exists (moved to archive).
    """
    repo = _copy_target_repo(tmp_path)
    proposal_path = repo / ".forge" / "changes" / _CHANGE_ID / "proposal.md"
    hook = _mark_change_merged_hook(proposal_path)

    result = merge_delta_proposal(repo, _CHANGE_ID, _CAPABILITY, pre_archive_hook=hook)

    canonical_path, archive_path = result

    # 3. Returns valid tuple
    assert isinstance(canonical_path, Path)
    assert isinstance(archive_path, Path)

    # 4. Canonical matches locked expected output
    expected_after = (FIXTURE_ROOT / "EXPECTED_canonical_after.md").read_text(encoding="utf-8")
    assert canonical_path.read_text(encoding="utf-8") == expected_after

    # 5. Archive folder exists
    assert archive_path.is_dir()
    assert archive_path == repo / ".forge" / "changes" / "archive" / _CHANGE_ID

    # 6. canonical-pre.md matches pre-snapshot fixture
    expected_canonical_pre = (FIXTURE_ROOT / "EXPECTED_canonical_pre_snapshot.md").read_text(
        encoding="utf-8"
    )
    assert (archive_path / "canonical-pre.md").read_text(encoding="utf-8") == expected_canonical_pre

    # 7. proposal-pre.md matches pre-snapshot fixture
    expected_proposal_pre = (FIXTURE_ROOT / "EXPECTED_proposal_pre_snapshot.md").read_text(
        encoding="utf-8"
    )
    assert (archive_path / "proposal-pre.md").read_text(encoding="utf-8") == expected_proposal_pre

    # 8. Archived proposal.md has status: merged (hook ran)
    archived_proposal_text = (archive_path / "proposal.md").read_text(encoding="utf-8")
    assert "status: merged" in archived_proposal_text

    # 9. Original change folder no longer exists
    original_change_folder = repo / ".forge" / "changes" / _CHANGE_ID
    assert not original_change_folder.exists()

    # H1 regression guard: ADD header label must reach canonical.
    canonical_text = canonical_path.read_text(encoding="utf-8")
    assert "scenario-3" in canonical_text, (
        "ADD op anchor label must appear verbatim in merged canonical"
    )


# ---------------------------------------------------------------------------
# Test 2 — Rollback on validator fail
# ---------------------------------------------------------------------------


def test_rollback_on_validator_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected BLOCK finding rolls back: canonical + proposal.md untouched, no archive.

    Monkeypatches tools.archive.validate_capability_spec_sections to return a
    single BLOCK finding.  The merge must raise ArchiveError, and the repo must
    be in the exact same state it was before the call.

    Note on snapshots: canonical-pre.md and proposal-pre.md are written BEFORE
    the validation step (per plan D-7 mutation order steps 1-2 precede step 4).
    On rollback these snapshot files remain in the change folder.  The test
    does NOT assert their absence — it asserts only that canonical SPEC.md and
    proposal.md are byte-identical to their pre-call state.
    """
    repo = _copy_target_repo(tmp_path)
    canonical_spec = repo / ".forge" / "specs" / _CAPABILITY / "SPEC.md"
    proposal_path = repo / ".forge" / "changes" / _CHANGE_ID / "proposal.md"

    # Capture original bytes before any call
    original_canonical = canonical_spec.read_bytes()
    original_proposal = proposal_path.read_bytes()

    fake_finding = Finding(
        "BLOCK",
        "spec",
        canonical_spec,
        "injected BLOCK finding to exercise rollback path",
    )
    monkeypatch.setattr(
        "tools.archive.validate_capability_spec_sections",
        lambda _path: [fake_finding],
    )

    with pytest.raises(ArchiveError):
        merge_delta_proposal(repo, _CHANGE_ID, _CAPABILITY)

    # Canonical SPEC.md is byte-identical to pre-call state
    assert canonical_spec.read_bytes() == original_canonical

    # No archive folder created
    archive_target = repo / ".forge" / "changes" / "archive" / _CHANGE_ID
    assert not archive_target.exists()

    # proposal.md is byte-identical to pre-call state (status still "approved")
    assert proposal_path.read_bytes() == original_proposal
    assert "status: approved" in proposal_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 3 — Retry-idempotency (archive already exists)
# ---------------------------------------------------------------------------


def test_retry_after_archive_exists(tmp_path: Path) -> None:
    """Second merge attempt on the same change_id raises ArchiveError.

    After a successful merge, the change folder has been moved to the archive.
    A retry with the same change_id must fail at preflight — the proposal.md no
    longer exists at its original path (it is inside the archive folder).  The
    already-merged canonical must NOT be touched on the retry.
    """
    repo = _copy_target_repo(tmp_path)
    proposal_path = repo / ".forge" / "changes" / _CHANGE_ID / "proposal.md"
    hook = _mark_change_merged_hook(proposal_path)

    # First merge succeeds
    canonical_path, _archive_path = merge_delta_proposal(
        repo, _CHANGE_ID, _CAPABILITY, pre_archive_hook=hook
    )
    post_merge_canonical = canonical_path.read_text(encoding="utf-8")

    # Second attempt fails at preflight: proposal.md was moved into the archive
    # folder; the archive dir exists, so the "already merged" check fires (L2 fix).
    with pytest.raises(ArchiveError, match="already merged"):
        merge_delta_proposal(repo, _CHANGE_ID, _CAPABILITY)

    # Canonical was NOT touched on the retry
    assert canonical_path.read_text(encoding="utf-8") == post_merge_canonical
