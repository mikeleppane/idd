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


def test_render_gate_prompt_raises_on_unknown_article_id() -> None:
    """L1 — defense in depth: gate-bucket findings must reference known articles.

    `partition_by_article_level` already routes unknown article ids to info
    so this path is unreachable in production. The assertion documents the
    invariant so a future caller bypassing the partitioner cannot smuggle a
    `(unknown)` rendering past the user prompt without the framework
    noticing.
    """
    rogue = sg.ShipFinding(
        article_id="A99",  # not present in `_articles()`
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A99] phantom",
    )
    with pytest.raises(sg.ShipGateError, match="unknown article id"):
        sg.render_gate_prompt([rogue], _articles())


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


# --- Resolved by column (WS3 slice 4) -------------------------------------
#
# The trap-memory harvest hook needs a deterministic signal for which findings
# convert into lessons. The `Resolved by` column carries that signal:
#   - empty                       → no resolution recorded
#   - 40-hex SHA                  → fix landed in this commit (harvest candidate)
#   - spec-edit / plan-edit       → resolution lived outside the code
#   - accepted-risk:<reason>      → exception logged in decisions.md
# Legacy review files (no column) are tolerated for backwards compat; every
# emitted ShipFinding from those files carries `resolved_by=None`.


def test_parse_review_findings_legacy_layout_has_resolved_by_none() -> None:
    """Pre-trap-memory review files have no `Resolved by` column → field None."""
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    assert findings, "fixture must yield at least one tagged finding"
    assert all(f.resolved_by is None for f in findings)


def test_parse_review_findings_populates_resolved_by_for_sha_on_open_row(
    tmp_path: Path,
) -> None:
    """40-hex SHA cell is preserved verbatim on the emitted ShipFinding.

    An `open`-status row with a populated `Resolved by` is odd but permitted —
    a reviewer may pre-populate the cell with a proposed commit before
    flipping Status to `resolved`. The parser passes the value through; the
    `Status: open` filter still surfaces the row.
    """
    src = tmp_path / "review_with_sha.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by == "1a2b3c4d5e6f7890abcdef1234567890abcdef12"


def test_parse_review_findings_empty_resolved_by_cell_is_none(tmp_path: Path) -> None:
    """Empty `Resolved by` cell on a tagged row → `resolved_by=None`."""
    src = tmp_path / "review_empty_resolved_by.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open |  | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by is None


def test_parse_review_findings_accepts_spec_edit_resolution(tmp_path: Path) -> None:
    src = tmp_path / "review_spec_edit.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | spec-edit | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by == "spec-edit"


def test_parse_review_findings_accepts_plan_edit_resolution(tmp_path: Path) -> None:
    src = tmp_path / "review_plan_edit.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | plan-edit | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by == "plan-edit"


def test_parse_review_findings_accepts_accepted_risk_resolution(tmp_path: Path) -> None:
    """`accepted-risk:<reason>` preserves the trailing reason verbatim."""
    src = tmp_path / "review_accepted_risk.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | accepted-risk:legacy module out of scope | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by == "accepted-risk:legacy module out of scope"


def test_parse_review_findings_raises_on_unknown_resolved_by_value(tmp_path: Path) -> None:
    """`unknown` is not a recognized resolution method — must raise."""
    src = tmp_path / "review_bad_resolved_by.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | unknown | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    with pytest.raises(sg.ShipGateError, match="unrecognized Resolved by"):
        sg.parse_review_findings(src)


def test_parse_review_findings_raises_on_truncated_sha_resolved_by(tmp_path: Path) -> None:
    """A 7-char SHA is not the 40-hex full form the vocabulary requires."""
    src = tmp_path / "review_short_sha.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | 1a2b3c4 | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    with pytest.raises(sg.ShipGateError, match="unrecognized Resolved by"):
        sg.parse_review_findings(src)


def test_parse_review_findings_skips_untagged_row_with_bad_resolved_by(tmp_path: Path) -> None:
    """Tag-check-first discipline: an untagged row with a bad Resolved by
    value must NOT raise. The harvest hook only cares about tagged rows;
    forcing the gate to fail over reviewer convergence-history on an
    untagged row would block ship over noise the gate never read.
    """
    src = tmp_path / "review_untagged_bad_resolved_by.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | totally-bogus | src/x.py:1 | no constitution tag here | strip | self |
""",
        encoding="utf-8",
    )
    # Untagged row → never gate-eligible → Resolved by typo must not raise.
    assert sg.parse_review_findings(src) == []


def test_parse_review_findings_multi_tag_row_shares_resolved_by(tmp_path: Path) -> None:
    """Multi-tag rows emit one ShipFinding per tag, all carrying the same
    Resolved by value — a single resolution covers every tag in the cell.
    """
    src = tmp_path / "review_multi_tag_resolved_by.md"
    src.write_text(
        """---
spec: 2026-05-07-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | src/x.py:1 | [constitution:A4] [constitution:A1] dual tag | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    article_ids = sorted(f.article_id for f in findings if f.article_id)
    assert article_ids == ["A1", "A4"]
    assert {f.resolved_by for f in findings} == {"1a2b3c4d5e6f7890abcdef1234567890abcdef12"}


def test_ship_finding_default_resolved_by_is_none() -> None:
    """Existing call sites that build ShipFinding without resolved_by stay valid.

    The dataclass field defaults to None so legacy constructors (every test
    above this block, every production call site outside the parser) compile
    and behave as before.
    """
    finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] x",
    )
    assert finding.resolved_by is None


# --- Lesson-kind ShipFinding + partitioner + renderer (WS3 slice 5) -------
#
# Reviewer subagents tag lesson-trap violations with [lesson:L<NNN>] in the
# REVIEW.md Problem cell. Those flow through the same parser as constitution
# tags but route via a separate partitioner that consults each lesson's own
# Severity field (CRITICAL/HIGH -> gate, MEDIUM -> warn, LOW -> info). The
# renderer and acknowledgement hook learn to print the lesson kind alongside
# constitution-kind findings without disturbing the legacy article path.


from datetime import date  # noqa: E402

from tools.intel.lessons import Lesson  # noqa: E402


def _make_lesson(
    lesson_id: str,
    severity: str,
    *,
    trap: str = "trap text",
    avoidance: str = "avoidance text",
    status: str = "active",
) -> Lesson:
    return Lesson(
        id=lesson_id,
        captured=date(2026, 5, 11),
        captured_from="2026-05-11-demo",
        resolved_by="1" * 40,
        trap=trap,
        avoidance=avoidance,
        tags=("imports",),
        severity=severity,  # type: ignore[arg-type]
        status=status,
        body_words=4,
    )


def _lessons_default() -> list[Lesson]:
    return [
        _make_lesson("L007", "HIGH", trap="async fixture teardown leaks DB sessions"),
        _make_lesson("L010", "MEDIUM", trap="logger swallowed stacks"),
        _make_lesson("L020", "CRITICAL", trap="missing PII redaction at sink"),
        _make_lesson("L030", "LOW", trap="cosmetic import order"),
    ]


def test_lesson_tag_re_extracts_single_tag() -> None:
    """`[lesson:L007]` in a Problem cell yields one lesson_id."""
    src_text = "Foo bar [lesson:L007] baz"
    assert sg._LESSON_TAG_RE.findall(src_text) == ["L007"]


def test_lesson_tag_re_extracts_multiple_tags() -> None:
    """Multiple lesson tags in one cell are all extracted in order."""
    src_text = "[lesson:L010] then [lesson:L007] later"
    assert sg._LESSON_TAG_RE.findall(src_text) == ["L010", "L007"]


def test_parse_review_findings_extracts_mixed_article_and_lesson_tags(tmp_path: Path) -> None:
    """A row containing both [constitution:A<n>] and [lesson:L<NNN>] tags emits one
    ShipFinding per tag — article-kind first, then lesson-kind.
    """
    src = tmp_path / "review_mixed.md"
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
| F-1 | HIGH | open | src/x.py:1 | [constitution:A1] [lesson:L007] dual tag | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 2
    # Article tag first, lesson tag second (pinned order from _findings_from_row).
    assert findings[0].kind == "article"
    assert findings[0].article_id == "A1"
    assert findings[0].lesson_id is None
    assert findings[1].kind == "lesson"
    assert findings[1].lesson_id == "L007"
    assert findings[1].article_id is None


def test_parse_review_findings_multiple_lesson_tags_emit_one_finding_each(tmp_path: Path) -> None:
    src = tmp_path / "review_multi_lesson.md"
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
| F-1 | HIGH | open | src/x.py:1 | [lesson:L007] [lesson:L010] both | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert [f.lesson_id for f in findings] == ["L007", "L010"]
    assert all(f.kind == "lesson" for f in findings)


def test_ship_finding_default_kind_is_article() -> None:
    """Legacy constructors must default to kind='article' so existing call
    sites continue to work without touching the new field.
    """
    finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] x",
    )
    assert finding.kind == "article"
    assert finding.lesson_id is None


def test_ship_finding_lesson_kind_builds_cleanly() -> None:
    """kind='lesson' + lesson_id constructs without error."""
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] x",
    )
    assert finding.kind == "lesson"
    assert finding.lesson_id == "L007"
    assert finding.article_id is None


def test_ship_finding_allows_both_ids_none_at_dataclass_level() -> None:
    """Both ids None is permitted at runtime — mypy is the gate for proper
    construction; the dataclass enforces no mutual exclusion to keep the
    constructor flexible for future shapes.
    """
    finding = sg.ShipFinding(
        severity="HIGH",
        location="src/x.py:1",
        message="placeholder",
    )
    assert finding.article_id is None
    assert finding.lesson_id is None


def test_partition_by_lesson_severity_empty_input() -> None:
    gate, warn, info = sg.partition_by_lesson_severity([], _lessons_default())
    assert gate == []
    assert warn == []
    assert info == []


def test_partition_by_lesson_severity_critical_routes_to_gate() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L020",
        severity="BLOCK",
        location="src/x.py:1",
        message="[lesson:L020] m",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert gate == [finding]
    assert warn == []
    assert info == []


def test_partition_by_lesson_severity_high_routes_to_gate() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert gate == [finding]
    assert warn == [] and info == []


def test_partition_by_lesson_severity_medium_routes_to_warn() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L010",
        severity="MEDIUM",
        location="src/x.py:1",
        message="[lesson:L010] m",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert warn == [finding]
    assert gate == [] and info == []


def test_partition_by_lesson_severity_low_routes_to_info() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L030",
        severity="LOW",
        location="src/x.py:1",
        message="[lesson:L030] m",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert info == [finding]
    assert gate == [] and warn == []


def test_partition_by_lesson_severity_filters_out_article_kind() -> None:
    """Article-kind findings pass through unaffected — they belong to the
    article partitioner.
    """
    article_finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] m",
    )
    lesson_finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
    )
    gate, warn, info = sg.partition_by_lesson_severity(
        [article_finding, lesson_finding], _lessons_default()
    )
    assert gate == [lesson_finding]
    assert article_finding not in gate
    assert article_finding not in warn
    assert article_finding not in info


def test_partition_by_lesson_severity_missing_lesson_id_raises() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L999",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L999] stale",
    )
    with pytest.raises(sg.ShipGateError, match="unknown lesson id"):
        sg.partition_by_lesson_severity([finding], _lessons_default())


def test_partition_by_lesson_severity_mismatched_severity_raises() -> None:
    """Row Severity cell BLOCK but lesson L030 has Severity LOW → loud error."""
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L030",
        severity="BLOCK",
        location="src/x.py:1",
        message="[lesson:L030] m",
    )
    with pytest.raises(sg.ShipGateError, match=r"row Severity=.*lesson L030 has Severity="):
        sg.partition_by_lesson_severity([finding], _lessons_default())


def test_partition_by_lesson_severity_multiple_lessons_split_correctly() -> None:
    findings = [
        sg.ShipFinding(
            kind="lesson",
            lesson_id="L020",
            severity="BLOCK",
            location="src/a.py:1",
            message="[lesson:L020] crit",
        ),
        sg.ShipFinding(
            kind="lesson",
            lesson_id="L007",
            severity="HIGH",
            location="src/b.py:1",
            message="[lesson:L007] high",
        ),
        sg.ShipFinding(
            kind="lesson",
            lesson_id="L010",
            severity="MEDIUM",
            location="src/c.py:1",
            message="[lesson:L010] med",
        ),
        sg.ShipFinding(
            kind="lesson",
            lesson_id="L030",
            severity="LOW",
            location="src/d.py:1",
            message="[lesson:L030] low",
        ),
    ]
    gate, warn, info = sg.partition_by_lesson_severity(findings, _lessons_default())
    assert {f.lesson_id for f in gate} == {"L020", "L007"}
    assert {f.lesson_id for f in warn} == {"L010"}
    assert {f.lesson_id for f in info} == {"L030"}


def test_partition_by_lesson_severity_raises_on_kind_lesson_without_lesson_id() -> None:
    """Defensive guard: kind='lesson' must carry a lesson_id."""
    finding = sg.ShipFinding(
        kind="lesson",
        severity="HIGH",
        location="src/x.py:1",
        message="missing lesson id",
    )
    with pytest.raises(sg.ShipGateError, match="missing lesson_id"):
        sg.partition_by_lesson_severity([finding], _lessons_default())


def test_render_gate_prompt_article_only_unchanged_when_lessons_none() -> None:
    """Regression: article-only gate must render identically with lessons=None."""
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    gate, _warn, _info = sg.partition_by_article_level(findings, _articles())
    prompt_legacy = sg.render_gate_prompt(gate, _articles())
    prompt_with_kw = sg.render_gate_prompt(gate, _articles(), lessons=None)
    assert prompt_legacy == prompt_with_kw
    assert "[constitution:A1]" in prompt_legacy
    assert "ACKNOWLEDGE" in prompt_legacy


def test_render_gate_prompt_lesson_finding_renders_trap_and_avoidance() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:99",
        message="[lesson:L007] async fixture leaks here",
    )
    prompt = sg.render_gate_prompt([finding], _articles(), lessons=_lessons_default())
    assert "[lesson:L007]" in prompt
    assert "async fixture teardown leaks DB sessions" in prompt  # trap body
    assert "src/x.py:99" in prompt
    assert "ACKNOWLEDGE" in prompt


def test_render_gate_prompt_mixed_kinds_renders_both() -> None:
    article_findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    article_gate, _w, _i = sg.partition_by_article_level(article_findings, _articles())
    lesson_finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:42",
        message="[lesson:L007] foo",
    )
    combined = [*list(article_gate), lesson_finding]
    prompt = sg.render_gate_prompt(combined, _articles(), lessons=_lessons_default())
    assert "[constitution:A1]" in prompt
    assert "[lesson:L007]" in prompt
    assert "Repository pattern" in prompt
    assert "async fixture teardown leaks DB sessions" in prompt


def test_render_gate_prompt_lesson_kind_without_lessons_arg_raises() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
    )
    with pytest.raises(sg.ShipGateError, match="no `lessons` argument"):
        sg.render_gate_prompt([finding], _articles())


def test_render_gate_prompt_unknown_lesson_id_raises() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L999",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L999] stale",
    )
    with pytest.raises(sg.ShipGateError, match="unknown lesson id"):
        sg.render_gate_prompt([finding], _articles(), lessons=_lessons_default())


def test_render_warn_summary_article_only_unchanged_when_lessons_none() -> None:
    findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    _gate, warn, _info = sg.partition_by_article_level(findings, _articles())
    legacy = sg.render_warn_summary(warn, _articles())
    with_kw = sg.render_warn_summary(warn, _articles(), lessons=None)
    assert legacy == with_kw
    assert "[constitution:A4]" in legacy


def test_render_warn_summary_lesson_finding_renders_trap_fragment() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L010",
        severity="MEDIUM",
        location="src/x.py:55",
        message="[lesson:L010] note",
    )
    summary = sg.render_warn_summary([finding], _articles(), lessons=_lessons_default())
    assert "[lesson:L010]" in summary
    assert "logger swallowed stacks" in summary
    assert "src/x.py:55" in summary


def test_render_warn_summary_mixed_kinds_renders_both() -> None:
    article_findings = sg.parse_review_findings(FIXTURES / "review_with_critical_finding.md")
    _g, article_warn, _i = sg.partition_by_article_level(article_findings, _articles())
    lesson_finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L010",
        severity="MEDIUM",
        location="src/y.py:1",
        message="[lesson:L010] med",
    )
    summary = sg.render_warn_summary(
        [*list(article_warn), lesson_finding], _articles(), lessons=_lessons_default()
    )
    assert "[constitution:A4]" in summary
    assert "[lesson:L010]" in summary


def test_render_warn_summary_lesson_kind_without_lessons_arg_raises() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L010",
        severity="MEDIUM",
        location="src/x.py:1",
        message="[lesson:L010] m",
    )
    with pytest.raises(sg.ShipGateError, match="no `lessons` argument"):
        sg.render_warn_summary([finding], _articles())


def test_render_warn_summary_unknown_lesson_id_raises() -> None:
    finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L999",
        severity="MEDIUM",
        location="src/x.py:1",
        message="[lesson:L999] stale",
    )
    with pytest.raises(sg.ShipGateError, match="unknown lesson id"):
        sg.render_warn_summary([finding], _articles(), lessons=_lessons_default())


def test_ack_hook_records_lesson_kind_acknowledgement(tmp_path: Path) -> None:
    """Lesson-kind ACK writes a recognizable ADR row + state.json deviation.

    The decisions.md body bullet starts with `[lesson:L<NNN>]`, the lesson's
    trap-fragment title, and the reviewer location — mirroring the
    constitution-kind bullet shape. The state.json `cause` field carries the
    `[lesson:L<NNN>]` tag verbatim so a future `validate_deviations` cross-ref
    locates the heading inside the body block.
    """
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-11-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {"ship": {"status": "in_progress", "started_at": "2026-05-11T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    lesson_finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:42",
        message="[lesson:L007] async fixture leaks here",
    )
    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=[lesson_finding],
        articles=_articles(),
        lessons=_lessons_default(),
        now=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["deviations"], "deviation entry must be appended"
    cause = payload["deviations"][-1]["cause"]
    assert "[lesson:L007]" in cause
    assert cause.lower().startswith("constitution finding acknowledged at ship:")
    decisions = decisions_path.read_text(encoding="utf-8")
    assert "[lesson:L007]" in decisions
    assert "async fixture teardown leaks DB sessions" in decisions
    assert "src/x.py:42" in decisions


def test_ack_hook_raises_when_lesson_kind_without_lessons_arg(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-11-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {"ship": {"status": "in_progress", "started_at": "2026-05-11T00:00:00Z"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    lesson_finding = sg.ShipFinding(
        kind="lesson",
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
    )
    with pytest.raises(sg.ShipGateError, match="no `lessons` argument"):
        sg.make_acknowledgement_hook(
            state_path=state_path,
            decisions_path=decisions_path,
            gate_findings=[lesson_finding],
            articles=_articles(),
            now=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
        )
