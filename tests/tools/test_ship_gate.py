"""Tests for tools.ship_gate constitution-finding parser + ACKNOWLEDGE recorder."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import ship_gate as sg
from tools.constitution import Article
from tools.validate.state_semantic import validate_deviations

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "_constitution"


def _articles() -> list[Article]:
    return [
        Article(
            id="A1",
            title="Repository pattern",
            level="CRITICAL",
            rule="ORM via repository/",
            reference=None,
            rationale=None,
            body_words=4,
        ),
        Article(
            id="A4",
            title="Verbose logger",
            level="SHOULD",
            rule="No swallowed stacks",
            reference=None,
            rationale=None,
            body_words=4,
        ),
    ]


def test_parse_review_findings_extracts_tagged_findings() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    by_article = {f.article_id: f for f in findings}
    assert "A1" in by_article and by_article["A1"].severity == "HIGH"
    assert "A4" in by_article and by_article["A4"].severity == "MEDIUM"
    # F-3 had no tag → not collected
    assert all(f.article_id is not None for f in findings)


def test_parse_review_findings_empty_when_no_tags() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_no_findings.md")
    assert findings == []


def test_parse_review_findings_missing_file_returns_empty() -> None:
    findings = sg.parse_review_findings(FIXTURES / "does_not_exist.md")
    assert findings == []


def test_parse_review_findings_raises_on_unknown_status_value(tmp_path: Path) -> None:
    """T6 review-finding follow-up: typos must surface, not silently drop the row."""
    bad = tmp_path / "review_bad_status.md"
    bad.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | HIGH | acceptedrisk | src/foo.py:42 | [constitution:A1] something | fix it | self |
""",
        encoding="utf-8",
    )
    with pytest.raises(sg.ShipGateError, match="unrecognized Status"):
        sg.parse_review_findings(bad)


def test_partition_by_article_level_uses_loaded_articles() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, warn, info = sg.partition_by_article_level(findings, _articles())
    assert {f.article_id for f in gate} == {"A1"}
    assert {f.article_id for f in warn} == {"A4"}
    assert info == []


def test_partition_filters_below_medium() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    # Force F-2 to LOW for this test
    forced = [
        sg.ShipFinding(
            article_id=f.article_id,
            severity="LOW" if f.severity == "MEDIUM" else f.severity,
            location=f.location,
            message=f.message,
        )
        for f in findings
    ]
    gate, warn, _info = sg.partition_by_article_level(forced, _articles())
    assert all(f.severity != "LOW" for f in gate + warn)


def test_render_gate_prompt_lists_each_finding() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _warn, _info = sg.partition_by_article_level(findings, _articles())
    prompt = sg.render_gate_prompt(gate, _articles())
    assert "[constitution:A1]" in prompt
    assert "Repository pattern" in prompt
    assert "ACKNOWLEDGE" in prompt
    assert "src/services/checkout.py:142" in prompt


def test_parse_review_findings_skips_resolved_rows() -> None:
    """§5.3.9 ship gate must distinguish unresolved from convergence history."""
    findings = sg.parse_review_findings(FIXTURES / "review_resolved_finding.md")
    assert findings == [], "Status: resolved rows must be filtered out"


def test_make_acknowledgement_hook_writes_state_and_decisions(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-07-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {"ship": {"status": "in_progress", "started_at": "2026-05-07T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _w, _i = sg.partition_by_article_level(findings, _articles())

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=gate,
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    # Hook signature matches ship_feature(pre_archive_hook=Callable[[Path], None]).
    hook(tmp_path)  # source path arg unused by ack hook

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["deviations"], "deviation entry must be appended"
    deviation = payload["deviations"][-1]
    assert deviation["phase"] == "ship"
    # Cause prefix MUST share a 60-char substring with the decisions heading title
    # so the existing `validate_deviations` cross-ref passes.
    assert deviation["cause"].lower().startswith("constitution finding acknowledged at ship:"), (
        f"cause={deviation['cause']!r} must align with decisions title prefix"
    )
    assert "[constitution:A1]" in deviation["cause"]
    assert deviation["resolution"] == "user_acknowledged"
    decisions = decisions_path.read_text(encoding="utf-8")
    assert "Constitution finding acknowledged at ship" in decisions
    assert "src/services/checkout.py:142" in decisions


def test_acknowledgement_satisfies_validate_deviations(tmp_path: Path) -> None:
    """Round-trip: post-ACK state.json + decisions.md must validate clean
    against `tools.validate.validate_deviations` so a future re-validate
    does not blow up on the audit-trail entry the gate just wrote."""
    feature_root = tmp_path / "feature"
    feature_root.mkdir()
    state_path = feature_root / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-07-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {"ship": {"status": "in_progress", "started_at": "2026-05-07T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = feature_root / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _w, _i = sg.partition_by_article_level(findings, _articles())

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=gate,
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    hook(feature_root)

    findings_validate = validate_deviations(feature_root)
    assert findings_validate == [], (
        f"validate_deviations should be clean; got {findings_validate!r}"
    )


def test_ack_hook_recovers_from_state_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If state.json write fails after decisions.md write, retry must complete the deviation."""
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-07-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {"ship": {"status": "in_progress", "started_at": "2026-05-07T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _w, _i = sg.partition_by_article_level(findings, _articles())

    # First attempt: decisions write succeeds, state write raises.
    original_write = Path.write_text
    failures = {"count": 0}

    def _failing_write(self: Path, *args: object, **kwargs: object) -> int:
        if self == state_path and failures["count"] == 0:
            failures["count"] += 1
            raise OSError("simulated state write failure")
        return original_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _failing_write)

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=gate,
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(OSError, match="simulated state write failure"):
        hook(tmp_path)

    # Decisions heading was written on the first attempt.
    decisions_after_fail = decisions_path.read_text(encoding="utf-8")
    assert "Constitution finding acknowledged at ship" in decisions_after_fail
    # State unchanged from initial.
    assert json.loads(state_path.read_text(encoding="utf-8"))["deviations"] == []

    # Second attempt: succeeds.
    hook(tmp_path)

    # State now has the deviation; decisions still has exactly one heading.
    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["deviations"][-1]["resolution"] == "user_acknowledged"
    final_decisions = decisions_path.read_text(encoding="utf-8")
    # Exactly one decisions heading — the second hook attempt detected the
    # orphan heading from the first attempt and skipped re-appending. The
    # phrase itself appears twice per entry (heading line + Cause: body line),
    # so count the heading marker instead.
    assert final_decisions.count("## 2026-05-07 — Constitution finding acknowledged at ship") == 1


def test_render_warn_summary_lists_should_findings() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    _gate, warn, _info = sg.partition_by_article_level(findings, _articles())
    summary = sg.render_warn_summary(warn, _articles())
    assert "[constitution:A4]" in summary
    assert "Verbose logger" in summary


def test_render_warn_summary_empty_input_returns_empty_string() -> None:
    assert sg.render_warn_summary([], _articles()) == ""
