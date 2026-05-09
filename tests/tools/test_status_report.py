"""Tests for tools.status_report — pure builder + markdown renderer.

The status_report module is consumed by the forge-status skill orchestrator
when ``--report`` is set. It takes a state.json payload + a list of
validator findings and returns a structured ``StatusReport`` plus a
markdown rendering. No I/O. No subprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools import status_report
from tools.status_report import (
    CommitSummary,
    StatusReport,
    build_status_report,
    render_status_report,
)
from tools.validate import Finding


def _payload(
    *,
    tier: str = "focused",
    current_phase: str = "execute",
    phase_status: str = "in_progress",
    flow_version: int | None = 1,
    commits: list[dict[str, str]] | None = None,
    feature_id: str = "2026-05-09-demo",
) -> dict[str, Any]:
    """Build a minimal state.json-shaped payload for tests."""
    payload: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": tier,
        "current_phase": current_phase,
        "phases": {current_phase: {"status": phase_status}},
        "skipped": [],
        "deviations": [],
        "commits": commits if commits is not None else [],
    }
    if flow_version is not None:
        payload["flow_version"] = flow_version
    return payload


def test_build_status_report_extracts_basic_fields() -> None:
    """Tier, current_phase, status, flow_version, feature_id propagate verbatim."""
    payload = _payload(
        tier="focused",
        current_phase="execute",
        phase_status="in_progress",
        flow_version=2,
    )

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert isinstance(report, StatusReport)
    assert report.feature_id == "2026-05-09-demo"
    assert report.tier == "focused"
    assert report.current_phase == "execute"
    assert report.current_status == "in_progress"
    assert report.flow_version == 2


def test_build_status_report_defaults_flow_version_when_absent() -> None:
    payload = _payload(flow_version=None)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.flow_version == 1


def test_build_status_report_recent_commits_insertion_order() -> None:
    """When >5 commits exist, the report keeps the last 5 in insertion
    order (state.commits[] is the canonical chronology — see
    tools/validate/tdd_evidence.py module docstring) and reverses them
    for most-recent-first display."""
    commits = [
        {
            "sha": f"abc{i:04d}deadbeef",
            "phase": "execute",
            "subject": f"step {i}",
            "logged_at": f"2026-05-0{i}T10:00:00Z",
        }
        for i in range(1, 8)
    ]
    payload = _payload(commits=commits)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert len(report.recent_commits) == 5
    # Most-recent first by insertion order: last 5 are commits[2..6] (i=3..7).
    assert report.recent_commits[0].subject == "step 7"
    assert report.recent_commits[-1].subject == "step 3"
    # sha_short is first 7 chars.
    assert report.recent_commits[0].sha_short == "abc0007"
    assert report.recent_commits[0].phase == "execute"
    assert all(isinstance(c, CommitSummary) for c in report.recent_commits)


def test_build_status_report_recent_commits_breaks_logged_at_ties_by_position() -> None:
    """All commits share the exact same logged_at — only insertion order
    differentiates them. The report must keep the *last* 5 in commits[],
    not an arbitrary 5 from a stable timestamp sort."""
    commits = [
        {
            "sha": f"abc{i:04d}deadbeef",
            "phase": "execute",
            "subject": f"step {i}",
            "logged_at": "2026-05-09T10:00:00Z",
        }
        for i in range(1, 8)
    ]
    payload = _payload(commits=commits)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert [c.subject for c in report.recent_commits] == [
        "step 7",
        "step 6",
        "step 5",
        "step 4",
        "step 3",
    ]


def test_build_status_report_open_blocks_keeps_block_and_high() -> None:
    """BLOCK and HIGH severities both drive non-zero exit (per
    EXIT_NONZERO_SEVERITIES) and both are operationally blocking — the
    report surfaces both. Lower severities are dropped. Input order is
    preserved."""
    findings = [
        Finding("BLOCK", "tdd_evidence", Path("a.md"), "block one"),
        Finding("MEDIUM", "spec", Path("b.md"), "med"),
        Finding("BLOCK", "qa_shape", Path("c.md"), "block two"),
        Finding("LOW", "spec", Path("d.md"), "low"),
        Finding("HIGH", "spec", Path("e.md"), "high"),
    ]
    payload = _payload()

    report = build_status_report(payload, findings, feature_id="2026-05-09-demo")

    assert [f.message for f in report.open_blocks] == [
        "block one",
        "block two",
        "high",
    ]


def test_build_status_report_next_command_focused_tier() -> None:
    """Focused-tier spec phase routes to /forge:execute (mirrors next_phase_command)."""
    payload = _payload(tier="focused", current_phase="spec")

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.next_command == "/forge:execute"


def test_build_status_report_terminal_phase_returns_none() -> None:
    """``qa`` is the terminal phase for non-focused tiers and
    ``current_phase=='done'`` is terminal everywhere."""
    qa_payload = _payload(tier="standard", current_phase="qa", phase_status="done")
    qa_report = build_status_report(qa_payload, [], feature_id="2026-05-09-demo")
    assert qa_report.next_command is None

    done_payload = _payload(current_phase="execute")
    done_payload["current_phase"] = "done"
    done_payload["phases"] = {}
    done_report = build_status_report(done_payload, [], feature_id="2026-05-09-demo")
    assert done_report.next_command is None


def test_build_status_report_ship_routes_to_qa_for_standard_full() -> None:
    """``ship`` is not terminal for non-focused tiers — the next step is
    ``/forge:qa --against merged`` per the README ladder."""
    for tier in ("standard", "full"):
        payload = _payload(tier=tier, current_phase="ship", phase_status="done")
        report = build_status_report(payload, [], feature_id="2026-05-09-demo")
        assert report.next_command == "/forge:qa --against merged", tier


def test_build_status_report_focused_tier_terminates_at_verify() -> None:
    """Focused tier finishes at verify — /forge:ship aborts on focused per
    commands/ship.md, so the report must NOT recommend /forge:ship or
    /forge:qa for a focused feature."""
    payload = _payload(tier="focused", current_phase="verify", phase_status="done")
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")
    assert report.next_command is None


def test_render_status_report_no_commits_no_blocks() -> None:
    """Empty inputs render placeholder rows for the table and the no-blocks line."""
    payload = _payload()
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    assert "# Status: 2026-05-09-demo" in rendered
    assert "## Recent commits" in rendered
    assert "## Open blockers" in rendered
    # Placeholder dash row when no commits.
    assert "| — | — | — |" in rendered
    # Placeholder line when no blocks.
    assert "_no BLOCK findings_" in rendered


def test_render_status_report_with_blocks_includes_fix_hints() -> None:
    """Findings with fix_hint emit ``Fix: <hint>``; findings without emit fallback."""
    findings = [
        Finding(
            "BLOCK",
            "tdd_evidence",
            Path("foo.md"),
            "missing test commit",
            fix_hint="add a paired test commit before the implementation",
        ),
        Finding("BLOCK", "qa_shape", Path("bar.md"), "qa rehearsal incomplete"),
    ]
    payload = _payload()
    report = build_status_report(payload, findings, feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    assert "**tdd_evidence**" in rendered
    assert "missing test commit" in rendered
    assert "Fix: add a paired test commit before the implementation" in rendered
    assert "**qa_shape**" in rendered
    assert "Fix: no recovery hint provided" in rendered


def test_render_status_report_section_order_stable() -> None:
    """Header, recent commits, and open blockers appear in that fixed order."""
    payload = _payload()
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    header_idx = rendered.index("# Status:")
    commits_idx = rendered.index("## Recent commits")
    blocks_idx = rendered.index("## Open blockers")

    assert header_idx < commits_idx < blocks_idx


def test_render_status_report_5_commit_cap() -> None:
    """7 commits in payload → exactly 5 data rows (plus header + separator) in markdown."""
    commits = [
        {
            "sha": f"abc{i:04d}deadbeef",
            "phase": "execute",
            "subject": f"step {i}",
            "logged_at": f"2026-05-0{i}T10:00:00Z",
        }
        for i in range(1, 8)
    ]
    payload = _payload(commits=commits)
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    matched_subjects = [s for s in (f"step {i}" for i in range(1, 8)) if s in rendered]
    assert len(matched_subjects) == 5
    # Last 5 in insertion order, displayed most-recent first.
    assert "step 7" in rendered
    assert "step 3" in rendered  # boundary kept
    assert "step 2" not in rendered  # boundary cut
    assert "step 1" not in rendered


def test_render_status_report_pipe_safe_subjects() -> None:
    """Pipe characters in commit subjects are escaped so the markdown table parses."""
    commits = [
        {
            "sha": "abc1234deadbeef",
            "phase": "execute",
            "subject": "weird | subject | with pipes",
            "logged_at": "2026-05-09T10:00:00Z",
        }
    ]
    payload = _payload(commits=commits)
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    # The subject row must not contain the raw unescaped pipe count from the
    # subject — three pipes there would shred the 3-column table.
    # Locate the data row carrying the sha and verify the column count.
    sha_rows = [line for line in rendered.splitlines() if "abc1234" in line]
    assert sha_rows, "expected a rendered row for the commit"
    data_row = sha_rows[0]
    # 3-column table ⇒ 4 unescaped pipes (leading, between cols, trailing).
    # Replace escaped `\|` with a placeholder so we only count column-boundary
    # pipes when measuring the row.
    masked = data_row.replace(r"\|", "<ESC>")
    boundary_pipes = masked.count("|")
    assert boundary_pipes == 4, (
        f"expected exactly 4 column-boundary pipes after escaping, got {boundary_pipes}: {data_row!r}"
    )
    # Sanity: the original subject pipes really were escaped.
    assert r"\|" in data_row


def test_render_status_report_subject_with_newline_does_not_break_table() -> None:
    """A subject containing CR/LF must not split into multiple rows; the
    renderer collapses line breaks alongside escaping pipes."""
    commits = [
        {
            "sha": "abc1234deadbeef",
            "phase": "execute",
            "subject": "oops\nshould not split\rthis row",
            "logged_at": "2026-05-09T10:00:00Z",
        }
    ]
    payload = _payload(commits=commits)
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    sha_rows = [line for line in rendered.splitlines() if "abc1234" in line]
    assert len(sha_rows) == 1, sha_rows
    assert "oops should not split this row" in sha_rows[0]


def test_render_status_report_next_command_renders_when_present() -> None:
    payload = _payload(tier="focused", current_phase="spec")
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    assert "**Next command:** /forge:execute" in rendered


def test_render_status_report_next_command_renders_dash_when_none() -> None:
    payload = _payload(tier="standard", current_phase="qa", phase_status="done")
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    assert "**Next command:** —" in rendered


def test_build_status_report_handles_missing_phase_block() -> None:
    """Missing phases entry for current_phase falls back to status='pending'."""
    payload = _payload()
    payload["phases"] = {}  # No entry for current_phase

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.current_status == "pending"


@pytest.mark.parametrize(
    "tier,phase,expected",
    [
        ("standard", "spec", "/forge:scenarios"),
        ("standard", "verify", "/forge:ship"),
        ("full", "domain", "/forge:scenarios"),
    ],
)
def test_build_status_report_next_command_other_tiers(tier: str, phase: str, expected: str) -> None:
    payload = _payload(tier=tier, current_phase=phase)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.next_command == expected


def test_status_report_main_renders_active_feature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m tools.status_report --feature <id> --repo-root <root>``
    end-to-end: resolves the active feature, runs in-process validators
    scoped to that feature, and prints the rendered markdown."""

    feature_id = "2026-05-09-demo"
    feature_dir = tmp_path / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True)
    (feature_dir / "state.json").write_text(
        '{"feature_id": "2026-05-09-demo", "tier": "focused", '
        '"current_phase": "execute", '
        '"phases": {"execute": {"status": "in_progress"}}, '
        '"skipped": [], "deviations": [], "commits": [], "flow_version": 1}',
        encoding="utf-8",
    )
    (feature_dir / "SPEC.md").write_text("# Acceptance Criteria\n", encoding="utf-8")

    rc = status_report.main(["--repo-root", str(tmp_path), "--feature", feature_id])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    assert "# Status: 2026-05-09-demo" in captured.out
    assert "## Recent commits" in captured.out
    assert "## Open blockers" in captured.out


def test_status_report_main_returns_1_when_no_active_feature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Operator's recovery path: if no active feature can be resolved, the
    CLI exits 1 with a stderr message."""
    rc = status_report.main(["--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "cannot resolve active feature" in captured.err


def test_status_report_main_scopes_findings_to_active_feature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A BLOCK on a sibling feature must NOT bleed into the active
    feature's report — gather_findings calls per-feature validators with
    the active feature_id only."""
    active_id = "2026-05-09-active"
    sibling_id = "2026-05-09-sibling"
    for fid, current in ((active_id, "execute"), (sibling_id, "execute")):
        feature_dir = tmp_path / ".forge" / "features" / fid
        feature_dir.mkdir(parents=True)
        (feature_dir / "state.json").write_text(
            f'{{"feature_id": "{fid}", "tier": "focused", '
            f'"current_phase": "{current}", '
            f'"phases": {{"execute": {{"status": "in_progress"}}}}, '
            f'"skipped": [], "deviations": [], "commits": [], "flow_version": 1}}',
            encoding="utf-8",
        )
        (feature_dir / "SPEC.md").write_text("# Acceptance Criteria\n", encoding="utf-8")

    rc = status_report.main(["--repo-root", str(tmp_path), "--feature", active_id])
    captured = capsys.readouterr()
    assert rc == 0
    # sibling id MUST NOT appear in the active feature's report.
    assert sibling_id not in captured.out, captured.out
