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


def test_partition_routes_by_article_level_independent_of_severity() -> None:
    """Article level is the routing key; severity is advisory metadata.

    Forcing F-2 to LOW must STILL route to warn because A4 is SHOULD; F-1
    stays in gate because A1 is CRITICAL. Pre-H2-fix this test asserted the
    opposite contract (severity bucketed BLOCK/HIGH/MEDIUM into gate/warn,
    LOW into info) — that contract violated the SKILL "CRITICAL article ->
    gate" guarantee whenever the reviewer typed a low severity for a
    CRITICAL-tagged finding.
    """
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
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
    assert {f.article_id for f in gate} == {"A1"}, "CRITICAL-article finding must gate"
    assert {f.article_id for f in warn} == {"A4"}, (
        "SHOULD-article finding routes to warn even at LOW severity"
    )


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

    # First attempt: decisions write succeeds, state write raises. After
    # the M4 atomic-replace migration the state write hits the sibling
    # tmpfile (`state.json.tmp`) before the rename, so target the tmpfile
    # name rather than state.json itself.
    state_tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    original_write = Path.write_text
    failures = {"count": 0}

    def _failing_write(self: Path, *args: object, **kwargs: object) -> int:
        if self == state_tmp_path and failures["count"] == 0:
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


def test_parse_review_findings_emits_one_finding_per_tag_in_message(tmp_path: Path) -> None:
    """H1 — Multi-tag rows must emit one ShipFinding per `[constitution:A<n>]`.

    Pre-fix `_TAG_RE.search` returned only the first match, so a row tagged
    with both a SHOULD article (A4) and a CRITICAL article (A1) routed
    entirely to warn — silently demoting the CRITICAL finding.
    """
    src = tmp_path / "review_multi_tag.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | src/x.py:1 | [constitution:A4] [constitution:A1] dual tag | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    article_ids = sorted(f.article_id for f in findings if f.article_id)
    assert article_ids == ["A1", "A4"], "every tag in the cell must yield its own finding"


def test_parse_review_findings_anchors_to_findings_section(tmp_path: Path) -> None:
    """H4 — `| ID |` table in REVIEW preamble must not zero out the parser.

    Pre-fix `_HEADER_RE` greedily matched the first `| ID |` row in the
    document. A preamble table (e.g. an inventory, ToC, or column legend)
    silently disarmed every downstream `| F-...` row.
    """
    src = tmp_path / "review_with_preamble_table.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

## Inventory of reviewers

| ID | Role |
|----|------|
| R-1 | heavy-subagent |

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].article_id == "A1"


def test_parse_review_findings_raises_on_unknown_severity_value(tmp_path: Path) -> None:
    """H2 — Severity must come from the closed `{BLOCK,HIGH,MEDIUM,LOW}` vocabulary.

    Pre-fix, severity was passed through verbatim. A row with a typo
    (`severity='Lo'`) or a stray case (`severity='High'`) for a
    CRITICAL-tagged article would route to info because the partition
    short-circuited on `severity in {BLOCK,HIGH,MEDIUM}` — silently bypassing
    the gate. The parser now treats severity vocabulary as a closed enum and
    surfaces a ShipGateError on any unrecognized value.
    """
    bad = tmp_path / "review_bad_severity.md"
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
| F-1 | High | open | src/x.py:1 | [constitution:A1] case-typo | fix | self |
""",
        encoding="utf-8",
    )
    with pytest.raises(sg.ShipGateError, match="unrecognized Severity"):
        sg.parse_review_findings(bad)


def test_partition_routes_low_severity_critical_finding_to_gate() -> None:
    """H2/L2 — A CRITICAL article must gate REGARDLESS of severity cell value.

    Pre-fix `partition_by_article_level` short-circuited on
    `severity in {BLOCK,HIGH,MEDIUM}` so a `severity='LOW'` finding tagged to
    a CRITICAL article fell through to info — gate empty, ship proceeded.
    The contract from the SKILL is that CRITICAL article level alone routes
    to the gate; severity is an advisory cell, not a routing key.
    """
    findings = [
        sg.ShipFinding(article_id="A1", severity="LOW", location="src/x.py:1", message="m"),
    ]
    gate, warn, info = sg.partition_by_article_level(findings, _articles())
    assert {f.article_id for f in gate} == {"A1"}, "CRITICAL article must gate at any severity"
    assert warn == [] and info == []


def test_partition_routes_block_severity_should_finding_to_warn() -> None:
    """L2 — A BLOCK severity finding on a SHOULD article must route to warn.

    After H2 lands and severity drops out of the partition logic, BLOCK on a
    SHOULD article belongs in warn (article level decides), not info or gate.
    """
    findings = [
        sg.ShipFinding(article_id="A4", severity="BLOCK", location="src/x.py:1", message="m"),
    ]
    gate, warn, info = sg.partition_by_article_level(findings, _articles())
    assert gate == [] and info == []
    assert {f.article_id for f in warn} == {"A4"}


def test_ack_hook_writes_state_atomically_via_tmpfile_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4 — state.json write must be atomic (tmpfile + rename), not direct rewrite.

    Pre-fix the ACK hook called `state_path.write_text(...)` directly, so a
    crash mid-write left state.json half-written and unrecoverable. With an
    atomic-replace, an injected failure on write_text against `state.json`
    itself must NEVER happen — the hook always writes to a sibling tempfile
    first and then renames into place. Track every write_text invocation so
    we can assert the final write went through a `*.tmp` neighbour rather
    than the canonical name.
    """
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

    seen_writes: list[Path] = []
    original_write = Path.write_text

    def _track_write(self: Path, *args: object, **kwargs: object) -> int:
        seen_writes.append(self)
        return original_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _track_write)

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=gate,
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)

    # No direct write_text call landed on state.json after the hook started;
    # the hook wrote to a sibling tempfile and renamed via Path.replace.
    state_writes = [p for p in seen_writes if p == state_path]
    assert state_writes == [], (
        f"state.json must be written via atomic-replace, got direct writes: {state_writes}"
    )
    # And the final state.json reflects the deviation.
    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert final["deviations"][-1]["resolution"] == "user_acknowledged"


def test_ack_hook_creates_decisions_file_with_h1_header_when_missing(tmp_path: Path) -> None:
    """M5 — auto-created decisions.md must include the `# Decisions` H1.

    Pre-fix the hook used `decisions_path.open("a", ...)` which created the
    file (under POSIX append-create semantics) without any header, leaving a
    headerless decisions.md that downstream `validate_decisions` could
    reject. Both the amend lifecycle and the ACK hook must produce a
    decisions.md with the standard H1 when bootstrapping the file.
    """
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
    # Decisions.md DOES NOT EXIST at start; hook must bootstrap it with H1.
    decisions_path = tmp_path / "decisions.md"
    assert not decisions_path.exists()

    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _w, _i = sg.partition_by_article_level(findings, _articles())

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=gate,
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)

    text = decisions_path.read_text(encoding="utf-8")
    assert text.startswith("# Decisions\n"), (
        f"auto-created decisions.md must lead with `# Decisions` H1, got {text[:64]!r}"
    )
    assert "Constitution finding acknowledged at ship" in text


def test_ack_hook_raises_ship_gate_error_on_corrupt_state_json(tmp_path: Path) -> None:
    """M2 — corrupt state.json must surface ShipGateError, not raw JSONDecodeError.

    Pre-fix the ACK hook called `json.loads(state_path.read_text(...))` with
    no try/except, leaking a raw JSONDecodeError into ship_feature's
    pre_archive_hook caller and burying the actual cause behind the generic
    ArchiveError wrap.
    """
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json at all", encoding="utf-8")
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
    with pytest.raises(sg.ShipGateError, match=r"state\.json is corrupt"):
        hook(tmp_path)


def test_parse_review_findings_skips_untagged_rows_with_unusual_status(tmp_path: Path) -> None:
    """M3 — Status vocab check applies only to constitution-tagged rows.

    Pre-fix, the Status validity check ran BEFORE the tag check, so an
    untagged row with a typo Status (e.g. `In progress`) raised
    ShipGateError even though the row was not gate-eligible. Move the
    Status check after the tag presence check so untagged rows pass through
    silently regardless of Status content.
    """
    src = tmp_path / "review_untagged_weird_status.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | HIGH | in-progress | src/x.py:1 | trailing whitespace (no constitution tag) | strip | self |
""",
        encoding="utf-8",
    )
    # Untagged row -> not gate-eligible -> Status typo must not raise.
    assert sg.parse_review_findings(src) == []


def test_parse_review_findings_returns_empty_when_no_findings_section(tmp_path: Path) -> None:
    """H4 — without a `# Findings` heading the parser must return [], not raise.

    A document without a `# Findings` section but with a stray `| F-...` row
    elsewhere (e.g. quoted in prose) must not be treated as a real finding.
    """
    src = tmp_path / "review_no_section.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

| ID | Role |
|----|------|
| R-1 | heavy-subagent |

Quoting an example: `| F-1 | HIGH | open | src/x.py | [constitution:A1] x | fix | self |`.
""",
        encoding="utf-8",
    )
    assert sg.parse_review_findings(src) == []
