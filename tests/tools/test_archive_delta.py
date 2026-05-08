"""Tests for merge_delta_proposal (D-7 transactional delta merger).

19 paths covering preflight x8, mutation x4, rollback x5, cross-fs x2.

Preflight failures:
  1. Invalid change_id slug -> ArchiveError("invalid change id").
  2. Invalid capability slug -> raises ArchiveError.
  3. proposal.md missing -> raises (no snapshot created).
  4. Frontmatter status: draft -> raises "not approved".
  5. Frontmatter affects_capability mismatch -> raises with both values.
  6. Canonical SPEC.md missing -> raises "canonical spec not found".
  7. Archive target already exists -> raises "archive already exists".
  8. validate_delta returns BLOCK finding -> raises with finding details.

Mutation success:
  9.  Happy path - no hook; canonical updated; archive contains snapshots; orig folder gone.
  10. Happy path with pre_archive_hook - hook called once; side-effect visible in archive.
  11. Multiple ops (ADD + ADD) merge correctly.
  12. Capability-spec validator accepts merged canonical (positive).

Rollback:
  13. Validator-after-merge fail -> canonical untouched; no archive; snapshots in change folder.
  14. Hook fail -> proposal.md restored; canonical untouched; no archive.
  15. Canonical atomic-write fail -> proposal.md restored; canonical untouched; no archive.
  16. test_proposal_status_restored_on_archive_failure (mandatory name).
  17. test_cross_fs_copytree_fail_restores_both_snapshots (mandatory name).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.archive import ArchiveError, _mark_change_merged_hook, merge_delta_proposal
from tools.validate._finding import Finding
from tools.validate.spec_structural import (
    validate_capability_spec_sections,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_CANONICAL_BODY = """\
---
capability: my-cap
status: shipped
created: "2026-01-01"
last_updated: "2026-01-01"
evidence:
  - 2026-01-01-initial-feature: features/archive/2026-01-01-initial-feature/
bounded_context: null
---

# My Cap

## Intent

Original intent paragraph.

## Scope

In scope: everything. Out of scope: nothing.

## Domain

| Term | Definition |
|------|------------|
| foo  | bar        |

## Scenarios

Scenario: basic usage
  Given a user
  When they act
  Then it works

## Acceptance Criteria

criterion 1: the system does X
criterion 2: the system does Y

## Negative Requirements

The system MUST NOT do Z.

## Decisions

- 2026-01-01-initial-feature: features/archive/2026-01-01-initial-feature/decisions.md
"""

_PROPOSAL_BODY = """\
---
id: 2026-05-08-add-criterion
affects_capability: my-cap
status: approved
created: "2026-05-08"
---

## Affects

sections [Acceptance Criteria]

## Delta

+ ADD: criterion-3
  criterion 3: the system does Z
"""


def _make_canonical(repo_root: Path, capability: str, body: str = _CANONICAL_BODY) -> Path:
    """Create .forge/specs/<capability>/SPEC.md with the given body."""
    spec = repo_root / ".forge" / "specs" / capability / "SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body, encoding="utf-8")
    return spec


def _make_proposal(
    repo_root: Path,
    change_id: str,
    body: str = _PROPOSAL_BODY,
) -> Path:
    """Create .forge/changes/<change_id>/proposal.md with the given body."""
    proposal = repo_root / ".forge" / "changes" / change_id / "proposal.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(body, encoding="utf-8")
    return proposal


# ---------------------------------------------------------------------------
# Preflight 1 — Invalid change_id slug
# ---------------------------------------------------------------------------


def test_invalid_change_id_raises(tmp_path: Path) -> None:
    """Malformed change_id → ArchiveError('invalid change id'). No mutation."""
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, "2026-05-08-add-criterion")  # valid; won't be reached

    with pytest.raises(ArchiveError, match="invalid change id"):
        merge_delta_proposal(tmp_path, "not-a-valid-change-id", "my-cap")


def test_invalid_change_id_uppercase_raises(tmp_path: Path) -> None:
    """Uppercase letters in change_id are rejected."""
    with pytest.raises(ArchiveError, match="invalid change id"):
        merge_delta_proposal(tmp_path, "2026-05-08-Add-Criterion", "my-cap")


# ---------------------------------------------------------------------------
# Preflight 2 — Invalid capability slug
# ---------------------------------------------------------------------------


def test_invalid_capability_raises(tmp_path: Path) -> None:
    """Malformed capability slug → raises ArchiveError."""
    _make_proposal(tmp_path, "2026-05-08-add-criterion")
    with pytest.raises(ArchiveError):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "INVALID SLUG!")


# ---------------------------------------------------------------------------
# Preflight 3 — proposal.md missing
# ---------------------------------------------------------------------------


def test_proposal_missing_raises(tmp_path: Path) -> None:
    """proposal.md does not exist → raises without creating any snapshot."""
    _make_canonical(tmp_path, "my-cap")
    change_id = "2026-05-08-add-criterion"
    change_folder = tmp_path / ".forge" / "changes" / change_id
    change_folder.mkdir(parents=True, exist_ok=True)  # folder exists; proposal.md absent

    with pytest.raises(ArchiveError, match="proposal"):
        merge_delta_proposal(tmp_path, change_id, "my-cap")

    assert not (change_folder / "canonical-pre.md").exists()
    assert not (change_folder / "proposal-pre.md").exists()


# ---------------------------------------------------------------------------
# Preflight 4 — Frontmatter status != "approved"
# ---------------------------------------------------------------------------


def test_status_draft_raises(tmp_path: Path) -> None:
    """status: draft → raises ArchiveError mentioning 'not approved' or 'approved'."""
    _make_canonical(tmp_path, "my-cap")
    draft_body = _PROPOSAL_BODY.replace("status: approved", "status: draft")
    _make_proposal(tmp_path, "2026-05-08-add-criterion", draft_body)

    with pytest.raises(ArchiveError, match="approved"):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")


# ---------------------------------------------------------------------------
# Preflight 5 — affects_capability mismatch
# ---------------------------------------------------------------------------


def test_affects_capability_mismatch_raises(tmp_path: Path) -> None:
    """affects_capability != capability arg → raises ArchiveError with both values."""
    _make_canonical(tmp_path, "other-cap")
    body = _PROPOSAL_BODY.replace("affects_capability: my-cap", "affects_capability: other-cap")
    _make_proposal(tmp_path, "2026-05-08-add-criterion", body)

    with pytest.raises(ArchiveError, match="my-cap"):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")


def test_affects_capability_mismatch_message_contains_both_values(tmp_path: Path) -> None:
    """Error message includes both the proposal value and the argument value."""
    _make_canonical(tmp_path, "my-cap")
    body = _PROPOSAL_BODY.replace("affects_capability: my-cap", "affects_capability: other-cap")
    _make_proposal(tmp_path, "2026-05-08-add-criterion", body)

    with pytest.raises(ArchiveError) as exc_info:
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")
    msg = str(exc_info.value)
    assert "my-cap" in msg
    assert "other-cap" in msg


# ---------------------------------------------------------------------------
# Preflight 6 — Canonical SPEC.md missing
# ---------------------------------------------------------------------------


def test_canonical_spec_missing_raises(tmp_path: Path) -> None:
    """SPEC.md for the capability doesn't exist → raises ArchiveError."""
    _make_proposal(tmp_path, "2026-05-08-add-criterion")
    # Do NOT create .forge/specs/my-cap/SPEC.md

    with pytest.raises(ArchiveError, match="canonical spec"):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")


# ---------------------------------------------------------------------------
# Preflight 7 — Archive target already exists
# ---------------------------------------------------------------------------


def test_archive_already_exists_raises(tmp_path: Path) -> None:
    """Archive target already present → raises ArchiveError before any mutation."""
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, "2026-05-08-add-criterion")
    archive_target = tmp_path / ".forge" / "changes" / "archive" / "2026-05-08-add-criterion"
    archive_target.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ArchiveError, match="archive"):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")


# ---------------------------------------------------------------------------
# Preflight 8 — validate_delta returns BLOCK finding
# ---------------------------------------------------------------------------


def test_validate_delta_block_finding_raises(tmp_path: Path) -> None:
    """validate_delta returning a BLOCK finding → ArchiveError with finding details."""
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, "2026-05-08-add-criterion")

    fake_finding = Finding(
        "BLOCK",
        "delta",
        tmp_path / ".forge" / "changes" / "2026-05-08-add-criterion" / "proposal.md",
        "missing required '## Affects' section",
    )

    with (
        patch("tools.archive.validate_delta", return_value=[fake_finding]),
        pytest.raises(ArchiveError, match="missing required"),
    ):
        merge_delta_proposal(tmp_path, "2026-05-08-add-criterion", "my-cap")


# ---------------------------------------------------------------------------
# Mutation 9 — Happy path (no hook)
# ---------------------------------------------------------------------------


def test_happy_path_no_hook(tmp_path: Path) -> None:
    """All preflight + mutation succeed; canonical updated; archive correct; orig gone.

    With no explicit ``pre_archive_hook``, the default
    ``_mark_change_merged_hook`` is wired automatically — the archived
    proposal MUST carry ``status: merged``, never the stale
    ``status: approved`` (Reviewer-3 Important).
    """
    change_id = "2026-05-08-add-criterion"
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, change_id)

    canonical_path, archive_path = merge_delta_proposal(tmp_path, change_id, "my-cap")

    # Returns canonical spec and archive folder
    assert canonical_path == tmp_path / ".forge" / "specs" / "my-cap" / "SPEC.md"
    assert archive_path == tmp_path / ".forge" / "changes" / "archive" / change_id

    # Canonical SPEC.md was updated
    assert canonical_path.is_file()
    content = canonical_path.read_text(encoding="utf-8")
    assert "criterion-3" in content  # merged (dash form — H1 fix: header label in new_text)

    # Archive folder contains snapshots
    assert (archive_path / "canonical-pre.md").is_file()
    assert (archive_path / "proposal-pre.md").is_file()

    # Proposal.md moved into archive
    archived_proposal = archive_path / "proposal.md"
    assert archived_proposal.is_file()

    # Default hook ran — archived proposal has status: merged, NOT approved.
    archived_text = archived_proposal.read_text(encoding="utf-8")
    assert "status: merged" in archived_text
    assert "status: approved" not in archived_text

    # Original change folder is gone (moved)
    orig_folder = tmp_path / ".forge" / "changes" / change_id
    assert not orig_folder.exists()


def test_default_hook_marks_proposal_merged_when_no_hook_passed(tmp_path: Path) -> None:
    """Explicit assertion of the default-hook contract.

    Reviewer-3 Important: ``merge_delta_proposal`` archived approved
    proposals when the caller omitted the hook.  After the fix, the default
    is ``_mark_change_merged_hook(proposal_path)``; archived proposals
    always reflect the merged state.
    """
    change_id = "2026-05-08-add-criterion"
    _make_canonical(tmp_path, "my-cap")
    proposal = _make_proposal(tmp_path, change_id)
    assert "status: approved" in proposal.read_text(encoding="utf-8")

    _canonical, archive_path = merge_delta_proposal(tmp_path, change_id, "my-cap")

    # The merged proposal lives in the archive only — original was moved.
    archived_text = (archive_path / "proposal.md").read_text(encoding="utf-8")
    assert "status: merged" in archived_text
    # The pre-merge snapshot still records the original approved status.
    pre_text = (archive_path / "proposal-pre.md").read_text(encoding="utf-8")
    assert "status: approved" in pre_text


# ---------------------------------------------------------------------------
# Mutation 10 — Happy path with pre_archive_hook
# ---------------------------------------------------------------------------


def test_happy_path_with_hook(tmp_path: Path) -> None:
    """pre_archive_hook called once with change_folder; side-effect visible in archive."""
    change_id = "2026-05-08-add-criterion"
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, change_id)

    calls: list[Path] = []

    def _hook(change_folder: Path) -> None:
        calls.append(change_folder)
        # Simulate status flip (what _mark_change_merged_hook does in T6)
        proposal = change_folder / "proposal.md"
        text = proposal.read_text(encoding="utf-8")
        proposal.write_text(
            text.replace("status: approved", "status: merged"),
            encoding="utf-8",
        )

    _canonical_path, archive_path = merge_delta_proposal(
        tmp_path, change_id, "my-cap", pre_archive_hook=_hook
    )

    # Hook was called exactly once
    assert len(calls) == 1

    # Hook's side-effect (status flip) visible in the archived proposal.md
    archived_proposal = archive_path / "proposal.md"
    assert archived_proposal.is_file()
    assert "status: merged" in archived_proposal.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Mutation 11 — Multiple ops (ADD + ADD) merge correctly
# ---------------------------------------------------------------------------


def test_multiple_add_ops_merge(tmp_path: Path) -> None:
    """Two ADD ops are both applied to the canonical body."""
    change_id = "2026-05-08-two-adds"
    _make_canonical(tmp_path, "my-cap")

    multi_op_proposal = """\
---
id: 2026-05-08-two-adds
affects_capability: my-cap
status: approved
created: "2026-05-08"
---

## Affects

sections [Acceptance Criteria]

## Delta

+ ADD: criterion-3
  criterion 3: the system does Z

+ ADD: criterion-4
  criterion 4: the system does W
"""
    _make_proposal(tmp_path, change_id, multi_op_proposal)

    canonical_path, _ = merge_delta_proposal(tmp_path, change_id, "my-cap")
    content = canonical_path.read_text(encoding="utf-8")

    assert "criterion-3" in content  # dash form — H1 fix: header label in new_text
    assert "criterion-4" in content  # dash form — H1 fix: header label in new_text


# ---------------------------------------------------------------------------
# Mutation 12 — Capability-spec validator accepts merged canonical (positive)
# ---------------------------------------------------------------------------


def test_merged_canonical_passes_validator(tmp_path: Path) -> None:
    """The merged canonical has all 7 required H2 sections — validator passes."""
    change_id = "2026-05-08-add-criterion"
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, change_id)

    # Should complete without ArchiveError from the validate-merged step
    canonical_path, _ = merge_delta_proposal(tmp_path, change_id, "my-cap")

    findings = validate_capability_spec_sections(canonical_path)
    assert findings == []


# ---------------------------------------------------------------------------
# Rollback 13 — Validator-after-merge fail
# ---------------------------------------------------------------------------


def test_validator_after_merge_fail_leaves_canonical_untouched(tmp_path: Path) -> None:
    """Inject BLOCK finding from validate_capability_spec_sections → canonical untouched."""
    change_id = "2026-05-08-add-criterion"
    canonical_spec = _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, change_id)
    original_canonical_bytes = canonical_spec.read_bytes()

    fake_finding = Finding(
        "BLOCK",
        "spec",
        canonical_spec,
        "missing required '## Scenarios' section",
    )

    with (
        patch(
            "tools.archive.validate_capability_spec_sections",
            return_value=[fake_finding],
        ),
        pytest.raises(ArchiveError, match="missing required"),
    ):
        merge_delta_proposal(tmp_path, change_id, "my-cap")

    # Canonical SPEC.md untouched
    assert canonical_spec.read_bytes() == original_canonical_bytes

    # No archive folder created
    archive_target = tmp_path / ".forge" / "changes" / "archive" / change_id
    assert not archive_target.exists()

    # Snapshots still in change folder (they were created before the validation step)
    change_folder = tmp_path / ".forge" / "changes" / change_id
    assert (change_folder / "canonical-pre.md").is_file()
    assert (change_folder / "proposal-pre.md").is_file()


# ---------------------------------------------------------------------------
# Rollback 14 — Hook fail
# ---------------------------------------------------------------------------


def test_hook_fail_restores_proposal_and_leaves_canonical_untouched(tmp_path: Path) -> None:
    """Hook raises RuntimeError → proposal.md restored; canonical untouched; no archive."""
    change_id = "2026-05-08-add-criterion"
    canonical_spec = _make_canonical(tmp_path, "my-cap")
    proposal = _make_proposal(tmp_path, change_id)
    original_canonical_bytes = canonical_spec.read_bytes()
    original_proposal_bytes = proposal.read_bytes()

    def _bad_hook(_change_folder: Path) -> None:
        raise RuntimeError("simulated")

    with pytest.raises(ArchiveError, match="pre_archive_hook"):
        merge_delta_proposal(tmp_path, change_id, "my-cap", pre_archive_hook=_bad_hook)

    # Canonical untouched
    assert canonical_spec.read_bytes() == original_canonical_bytes

    # proposal.md restored to pre-snapshot state
    assert proposal.read_bytes() == original_proposal_bytes

    # No archive
    archive_target = tmp_path / ".forge" / "changes" / "archive" / change_id
    assert not archive_target.exists()


# ---------------------------------------------------------------------------
# Rollback 15 — Canonical atomic-write fail
# ---------------------------------------------------------------------------


def test_atomic_replace_fail_restores_proposal(tmp_path: Path) -> None:
    """atomic_replace raises → proposal.md restored; canonical untouched; no archive.

    A no-op hook is supplied explicitly so the default ``_mark_change_merged_hook``
    (which itself calls ``atomic_replace`` to flip proposal.md status) does not
    intercept the patched OSError before the canonical-write step.
    """
    change_id = "2026-05-08-add-criterion"
    canonical_spec = _make_canonical(tmp_path, "my-cap")
    proposal = _make_proposal(tmp_path, change_id)
    original_canonical_bytes = canonical_spec.read_bytes()
    original_proposal_bytes = proposal.read_bytes()

    def _noop(_change_folder: Path) -> None:
        return None

    with (
        patch("tools.archive.atomic_replace", side_effect=OSError("simulated write fail")),
        pytest.raises(ArchiveError, match="atomic_replace"),
    ):
        merge_delta_proposal(tmp_path, change_id, "my-cap", pre_archive_hook=_noop)

    # Canonical SPEC.md untouched
    assert canonical_spec.read_bytes() == original_canonical_bytes

    # proposal.md restored from snapshot
    assert proposal.read_bytes() == original_proposal_bytes

    # No archive created
    archive_target = tmp_path / ".forge" / "changes" / "archive" / change_id
    assert not archive_target.exists()


# ---------------------------------------------------------------------------
# Rollback 16 — test_proposal_status_restored_on_archive_failure (mandatory name)
# ---------------------------------------------------------------------------


def test_proposal_status_restored_on_archive_failure(tmp_path: Path) -> None:
    """shutil.move fails → canonical restored; proposal.md restored; no partial archive.

    This is the Reviewer-2 scenario: the hook runs (flipping status), the
    canonical atomic-write succeeds, then shutil.move fails.  Both the
    canonical write and the status flip must be rolled back via snapshots.
    """
    change_id = "2026-05-08-add-criterion"
    canonical_spec = _make_canonical(tmp_path, "my-cap")
    proposal = _make_proposal(tmp_path, change_id)
    original_canonical_bytes = canonical_spec.read_bytes()

    def _flipping_hook(change_folder: Path) -> None:
        # Flip status to simulate _mark_change_merged_hook
        p = change_folder / "proposal.md"
        p.write_text(
            p.read_text(encoding="utf-8").replace("status: approved", "status: merged"),
            encoding="utf-8",
        )

    with (
        patch("tools.archive.shutil.move", side_effect=OSError("disk full")),
        pytest.raises(ArchiveError, match="archive move"),
    ):
        merge_delta_proposal(tmp_path, change_id, "my-cap", pre_archive_hook=_flipping_hook)

    # Canonical SPEC.md restored from snapshot (back to pre-merge state)
    assert canonical_spec.read_bytes() == original_canonical_bytes

    # proposal.md restored from snapshot (status flipped back to approved)
    restored_text = proposal.read_text(encoding="utf-8")
    assert "status: approved" in restored_text

    # No archive folder
    archive_target = tmp_path / ".forge" / "changes" / "archive" / change_id
    assert not archive_target.exists()


# ---------------------------------------------------------------------------
# Rollback 17 — test_cross_fs_copytree_fail_restores_both_snapshots (mandatory name)
# ---------------------------------------------------------------------------


def test_cross_fs_copytree_fail_restores_both_snapshots(tmp_path: Path) -> None:
    """OSError from shutil.move (cross-fs copytree) → canonical and proposal both restored.

    Simulates a cross-filesystem move that shutil.move handles with a copytree
    fallback which may partially fail.  Expects:
    - canonical SPEC.md restored from canonical-pre.md snapshot
    - proposal.md restored from proposal-pre.md snapshot
    - partial archive target cleaned up (rmtree with ignore_errors=True)
    """
    change_id = "2026-05-08-add-criterion"
    canonical_spec = _make_canonical(tmp_path, "my-cap")
    proposal = _make_proposal(tmp_path, change_id)
    original_canonical_bytes = canonical_spec.read_bytes()
    original_proposal_bytes = proposal.read_bytes()

    with (
        patch(
            "tools.archive.shutil.move",
            side_effect=OSError("simulated cross-fs copytree partial fail"),
        ),
        pytest.raises(ArchiveError, match="archive move"),
    ):
        merge_delta_proposal(tmp_path, change_id, "my-cap")

    # Both canonical and proposal restored from snapshots
    assert canonical_spec.read_bytes() == original_canonical_bytes
    assert proposal.read_bytes() == original_proposal_bytes

    # Partial archive target cleaned up (rmtree ignore_errors=True)
    archive_target = tmp_path / ".forge" / "changes" / "archive" / change_id
    assert not archive_target.exists()


# ---------------------------------------------------------------------------
# Coverage gap — _read_proposal_frontmatter edge cases
# ---------------------------------------------------------------------------


def test_proposal_missing_frontmatter_block_raises(tmp_path: Path) -> None:
    """proposal.md without any frontmatter block raises ArchiveError."""
    _make_canonical(tmp_path, "my-cap")
    change_id = "2026-05-08-add-criterion"
    # No --- delimiters at all
    _make_proposal(tmp_path, change_id, "No frontmatter here\n")

    with pytest.raises(ArchiveError, match="proposal frontmatter"):
        merge_delta_proposal(tmp_path, change_id, "my-cap")


def test_proposal_invalid_yaml_frontmatter_raises(tmp_path: Path) -> None:
    """proposal.md with invalid YAML in frontmatter raises ArchiveError."""
    _make_canonical(tmp_path, "my-cap")
    change_id = "2026-05-08-add-criterion"
    # Deliberately broken YAML (tab in a place YAML forbids)
    broken = "---\nkey: [unclosed\n---\n\nbody\n"
    _make_proposal(tmp_path, change_id, broken)

    with pytest.raises(ArchiveError, match="frontmatter"):
        merge_delta_proposal(tmp_path, change_id, "my-cap")


def test_proposal_non_mapping_yaml_frontmatter_raises(tmp_path: Path) -> None:
    """proposal.md whose frontmatter parses to a YAML list raises ArchiveError."""
    _make_canonical(tmp_path, "my-cap")
    change_id = "2026-05-08-add-criterion"
    # YAML list instead of mapping
    list_body = "---\n- item1\n- item2\n---\n\nbody\n"
    _make_proposal(tmp_path, change_id, list_body)

    with pytest.raises(ArchiveError, match="frontmatter must be a YAML mapping"):
        merge_delta_proposal(tmp_path, change_id, "my-cap")


# ---------------------------------------------------------------------------
# _mark_change_merged_hook
# ---------------------------------------------------------------------------

_APPROVED_PROPOSAL = """\
---
id: 2026-05-08-add-criterion
affects_capability: my-cap
status: approved
created: "2026-05-08"
extra_field: preserved
---

## Affects

sections [Acceptance Criteria]

## Delta

+ ADD: criterion-3
  criterion 3: the system does Z
"""

_MERGED_PROPOSAL = """\
---
id: 2026-05-08-add-criterion
affects_capability: my-cap
status: merged
created: "2026-05-08"
extra_field: preserved
---

## Affects

sections [Acceptance Criteria]

## Delta

+ ADD: criterion-3
  criterion 3: the system does Z
"""


def test_mark_change_merged_hook_flips_status_approved_to_merged(tmp_path: Path) -> None:
    """Hook flips status: approved -> merged; other frontmatter fields preserved."""
    proposal = tmp_path / "proposal.md"
    proposal.write_text(_APPROVED_PROPOSAL, encoding="utf-8")

    hook = _mark_change_merged_hook(proposal)
    hook(tmp_path)  # change_folder arg is ignored; proposal_path already captured

    result = proposal.read_text(encoding="utf-8")
    assert "status: merged" in result
    assert "status: approved" not in result
    # Other frontmatter fields must be preserved
    assert "extra_field: preserved" in result
    assert "affects_capability: my-cap" in result


def test_mark_change_merged_hook_idempotent_on_already_merged(tmp_path: Path) -> None:
    """Hook is a no-op when status is already merged; second call does not raise."""
    proposal = tmp_path / "proposal.md"
    proposal.write_text(_MERGED_PROPOSAL, encoding="utf-8")

    hook = _mark_change_merged_hook(proposal)
    # First call
    hook(tmp_path)
    # Second call — must not raise
    hook(tmp_path)

    result = proposal.read_text(encoding="utf-8")
    assert "status: merged" in result
    assert "status: approved" not in result


def test_mark_change_merged_hook_rejects_unexpected_status(tmp_path: Path) -> None:
    """Hook raises ArchiveError when proposal status is neither approved nor merged."""
    draft_proposal = """\
---
id: 2026-05-08-add-criterion
affects_capability: my-cap
status: draft
created: "2026-05-08"
---

## Affects

sections [Intent]

## Delta

+ ADD: something
  something new
"""
    proposal = tmp_path / "proposal.md"
    proposal.write_text(draft_proposal, encoding="utf-8")

    hook = _mark_change_merged_hook(proposal)
    with pytest.raises(ArchiveError, match="expected approved or merged"):
        hook(tmp_path)


# ---------------------------------------------------------------------------
# M3 — Concurrent merge_delta_proposal calls raise a clear error
# ---------------------------------------------------------------------------


def test_concurrent_merge_raises_clear_error(tmp_path: Path) -> None:
    """A second merge_delta_proposal call against an in-flight change_id must raise
    ArchiveError with a clear message — not a tempfile-not-found red herring.

    This unit test monkeypatches fcntl.flock to raise BlockingIOError (the
    signal that the lock is held) so we avoid non-deterministic threading.
    The threaded integration scenario is documented but not automated here
    because deterministic thread interleaving requires synchronisation
    primitives that would make the test fragile and slow.
    """
    change_id = "2026-05-08-add-criterion"
    _make_canonical(tmp_path, "my-cap")
    _make_proposal(tmp_path, change_id)

    with (
        patch("tools.archive.fcntl.flock", side_effect=BlockingIOError("locked")),
        pytest.raises(ArchiveError, match="in flight"),
    ):
        merge_delta_proposal(tmp_path, change_id, "my-cap")
