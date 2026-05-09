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

from tools.status_report import (
    CommitSummary,
    StatusReport,
    build_status_report,
    render_status_report,
)
from tools.validate._finding import Finding


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


def test_build_status_report_recent_commits_sorted_desc() -> None:
    """When >5 commits exist, the report keeps the 5 most-recent (desc by logged_at)."""
    commits = [
        {"sha": f"abc{i:04d}deadbeef", "phase": "execute", "subject": f"step {i}", "logged_at": f"2026-05-0{i}T10:00:00Z"}
        for i in range(1, 8)
    ]
    payload = _payload(commits=commits)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert len(report.recent_commits) == 5
    # Most recent first.
    assert report.recent_commits[0].logged_at == "2026-05-07T10:00:00Z"
    assert report.recent_commits[-1].logged_at == "2026-05-03T10:00:00Z"
    # sha_short is first 7 chars.
    assert report.recent_commits[0].sha_short == "abc0007"
    # Subject + phase preserved.
    assert report.recent_commits[0].subject == "step 7"
    assert report.recent_commits[0].phase == "execute"
    assert all(isinstance(c, CommitSummary) for c in report.recent_commits)


def test_build_status_report_open_blocks_filters_severity() -> None:
    """Only BLOCK findings are retained; input order is preserved."""
    findings = [
        Finding("BLOCK", "tdd_evidence", Path("a.md"), "block one"),
        Finding("MEDIUM", "spec", Path("b.md"), "med"),
        Finding("BLOCK", "qa_shape", Path("c.md"), "block two"),
        Finding("LOW", "spec", Path("d.md"), "low"),
        Finding("HIGH", "spec", Path("e.md"), "high"),
    ]
    payload = _payload()

    report = build_status_report(payload, findings, feature_id="2026-05-09-demo")

    assert len(report.open_blocks) == 2
    assert report.open_blocks[0].message == "block one"
    assert report.open_blocks[1].message == "block two"


def test_build_status_report_next_command_focused_tier() -> None:
    """Focused-tier spec phase routes to /forge:execute (mirrors next_phase_command)."""
    payload = _payload(tier="focused", current_phase="spec")

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.next_command == "/forge:execute"


def test_build_status_report_terminal_phase_returns_none() -> None:
    """When the lifecycle has no further phase (qa or current_phase=done) → None."""
    qa_payload = _payload(current_phase="qa", phase_status="done")
    qa_report = build_status_report(qa_payload, [], feature_id="2026-05-09-demo")
    assert qa_report.next_command is None

    done_payload = _payload(current_phase="execute")
    done_payload["current_phase"] = "done"
    done_payload["phases"] = {}
    done_report = build_status_report(done_payload, [], feature_id="2026-05-09-demo")
    assert done_report.next_command is None


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
        {"sha": f"abc{i:04d}deadbeef", "phase": "execute", "subject": f"step {i}", "logged_at": f"2026-05-0{i}T10:00:00Z"}
        for i in range(1, 8)
    ]
    payload = _payload(commits=commits)
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    # Count data rows by counting "step N" occurrences in the body.
    step_lines = [line for line in rendered.splitlines() if line.startswith("| step ") or "| step " in line]
    # Each rendered table row begins with `|` and contains the subject; just
    # count distinct subjects rendered.
    matched_subjects = [s for s in (f"step {i}" for i in range(1, 8)) if s in rendered]
    assert len(matched_subjects) == 5
    # Top row should be most-recent commit (step 7).
    assert "step 7" in rendered
    assert "step 3" in rendered  # boundary kept
    assert "step 2" not in rendered  # boundary cut
    assert "step 1" not in rendered
    # Sanity: at least 5 rows starting with `|` past the separator.
    assert len(step_lines) >= 5


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
    # Count outer-pipe boundaries by splitting on the literal escape we emit
    # (`\|`) first to avoid counting escaped pipes as separators.
    parts = data_row.split(r"\|")
    rejoined = "|ESC|".join(parts)
    boundary_pipes = rejoined.count("|") - rejoined.count("|ESC|")
    assert boundary_pipes == 4, (
        f"expected exactly 4 column-boundary pipes after escaping, got {boundary_pipes}: {data_row!r}"
    )


def test_render_status_report_next_command_renders_when_present() -> None:
    payload = _payload(tier="focused", current_phase="spec")
    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    rendered = render_status_report(report)

    assert "**Next command:** /forge:execute" in rendered


def test_render_status_report_next_command_renders_dash_when_none() -> None:
    payload = _payload(current_phase="qa", phase_status="done")
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
def test_build_status_report_next_command_other_tiers(
    tier: str, phase: str, expected: str
) -> None:
    payload = _payload(tier=tier, current_phase=phase)

    report = build_status_report(payload, [], feature_id="2026-05-09-demo")

    assert report.next_command == expected
