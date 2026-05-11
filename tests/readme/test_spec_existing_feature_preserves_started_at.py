"""Contract test: /forge:spec --feature <id> preserves started_at mid-refinement.

The forge-spec SKILL.md banner promises that the headless-refusal branch
leaves ``phases.spec.status`` as it was on entry. That promise can only
hold if ``commands/spec.md`` step 4 does NOT unconditionally call
``tools.state.start_phase(path, "spec")`` before invoking the skill —
re-issuing ``start_phase`` overwrites ``started_at`` and replaces the
phase entry, even when the skill itself exits without mutating state.

The same preservation matters for the non-headless re-entry path: a
feature whose self-review gate held ``phases.spec.status`` at
``in_progress`` (validator BLOCK/HIGH finding) must keep its original
``started_at`` across re-invocations.

This test pins ``commands/spec.md`` step 4 to branch on
``phases.spec.status`` and skip ``start_phase`` while
``current_phase == "spec"`` AND ``phases.spec.status == "in_progress"``.
"""

from __future__ import annotations

from pathlib import Path


def test_spec_command_skips_start_phase_when_already_in_progress(repo_root: Path) -> None:
    """commands/spec.md step 4 must guard against clobbering started_at."""
    body = (repo_root / "commands" / "spec.md").read_text(encoding="utf-8")

    assert 'current_phase == "spec"' in body, (
        "commands/spec.md step 4 must explicitly check current_phase before "
        "calling start_phase to preserve started_at on re-entry"
    )
    assert 'phases.spec.status == "in_progress"' in body, (
        "commands/spec.md step 4 must explicitly check phases.spec.status before "
        "calling start_phase to preserve started_at on re-entry"
    )
    assert 'Skip `start_phase("spec")`' in body, (
        "commands/spec.md step 4 must direct the agent to SKIP start_phase('spec') "
        "when the feature is already mid-refinement (current_phase=spec AND "
        "phases.spec.status=in_progress) so re-entry preserves started_at"
    )
    assert "clobber the existing `started_at`" in body, (
        "commands/spec.md step 4 must call out clobbered started_at as the "
        "reason for the skip so future edits cannot accidentally re-introduce "
        "the unconditional start_phase call"
    )
