"""QA-hardening regression tests for ``tools.ship_gate``.

Covers five fixes that ship together:

* Unknown lesson / article id routes to ``info`` plus a ``routing_warnings``
  diagnostic instead of raising ``ShipGateError``. A stray ``[lesson:L042]``
  on a row whose lesson is absent or retired must NOT block ship — the
  partitioner downgrades the typo to a recoverable warning while the
  real configuration bug (Severity-mismatch) still raises.
* The acknowledgement hook strips BOTH the constitution tag regex AND the
  lesson tag regex from a finding's body line, regardless of the finding's
  ``kind``, so a mixed-tag REVIEW row cannot leak the OTHER tag into the
  ADR body bullet.
* ``_ACK_PREFIX`` reads ``"Ship-gate finding acknowledged at ship"`` (covers
  both article and lesson kinds; the old "Constitution finding ..." prefix
  was misleading on lesson-kind ACK).
* ``Resolved by`` SHA cells are case-insensitive — uppercase / mixed-case
  hex normalizes to lowercase at parse time so downstream comparisons stay
  deterministic. Applies to both ``parse_review_findings`` and
  ``tools.intel.lessons.parse``.
* ``render_git_conventions_info_summary`` exists for diagnostic CLI use and
  returns the empty string when the info bucket is empty. Symmetric with
  ``render_git_conventions_warn_summary``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tools import ship_gate as sg
from tools.constitution import Article
from tools.intel import lessons as intel_lessons
from tools.intel.lessons import Lesson
from tools.validate import Finding
from tools.validate.state_semantic import validate_deviations

# --- Shared fixtures ------------------------------------------------------


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
        _make_lesson("L007", "HIGH"),
        _make_lesson("L010", "MEDIUM"),
        _make_lesson("L020", "CRITICAL"),
        _make_lesson("L030", "LOW"),
    ]


# --- B2 / M3 — Unknown lesson id routes to info plus routing_warnings -----


def test_unknown_lesson_id_routes_to_info_not_raise() -> None:
    """B2 — A stray ``[lesson:L999]`` tag with no L999 in lessons must not raise.

    Pre-fix the partitioner accumulated the id into ``routing_errors`` and
    raised ``ShipGateError`` so the user could not even reach the ACK prompt.
    Downgrade to a synthetic LOW finding in the ``info`` bucket plus a
    ``routing_warnings`` diagnostic so ship can proceed.
    """
    finding = sg.ShipFinding(
        lesson_id="L999",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L999] stale tag",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert gate == []
    assert warn == []
    assert len(info) == 1
    synthetic = info[0]
    assert synthetic.is_lesson
    assert synthetic.lesson_id == "L999"
    assert synthetic.severity == "LOW"
    assert "L999" in synthetic.message
    assert synthetic.location == "src/x.py:1"


def test_unknown_lesson_id_surfaces_routing_warnings_field() -> None:
    """The synthetic info-bucket finding pairs with a routing_warnings entry.

    The warning channel is how skill prose surfaces "this tag looks like a
    typo" feedback without blocking ship.
    """
    finding = sg.ShipFinding(
        lesson_id="L999",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L999] stale tag",
    )
    result = sg.partition_by_lesson_severity([finding], _lessons_default())
    warnings = sg.routing_warnings(result)
    assert any("L999" in w for w in warnings)
    assert any("src/x.py:1" in w for w in warnings)


def test_retired_lesson_id_treated_as_unknown_routes_to_info() -> None:
    """A retired lesson is filtered out by ``load_and_filter``; the partitioner
    sees a missing id and must route the same way as a typo'd L<NNN>.
    """
    retired = _make_lesson("L042", "HIGH", status="retired")
    # Simulate the post-filter state: caller already removed retired entries.
    active_only = [le for le in [*_lessons_default(), retired] if le.status == "active"]
    finding = sg.ShipFinding(
        lesson_id="L042",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L042] retired ref",
    )
    gate, warn, info = sg.partition_by_lesson_severity([finding], active_only)
    assert gate == []
    assert warn == []
    assert len(info) == 1
    assert info[0].lesson_id == "L042"


def test_severity_mismatch_still_raises() -> None:
    """M3 distinction — Severity-mismatch is a real configuration bug, NOT a typo.

    The unknown-id path downgrades to a warning. The Severity-mismatch path
    still raises so a reviewer who types ``BLOCK`` in the Severity cell for
    a LOW-rated lesson cannot silently bypass the gate.
    """
    finding = sg.ShipFinding(
        lesson_id="L030",  # LOW
        severity="BLOCK",
        location="src/x.py:1",
        message="[lesson:L030] m",
    )
    with pytest.raises(sg.ShipGateError, match=r"row Severity=.*disagrees with lesson L030"):
        sg.partition_by_lesson_severity([finding], _lessons_default())


def test_unknown_article_id_routes_to_info_with_routing_warnings() -> None:
    """M3 symmetry — articles also surface a routing_warnings entry on unknown ids.

    Pre-fix the article partitioner silently routed unknown ids to info with
    no diagnostic; the symmetric channel makes typos visible without
    blocking ship.
    """
    finding = sg.ShipFinding(
        article_id="A99",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A99] stale",
    )
    result = sg.partition_by_article_level([finding], _articles())
    gate, warn, info = result
    assert gate == []
    assert warn == []
    assert len(info) == 1
    warnings = sg.routing_warnings(result)
    assert any("A99" in w for w in warnings)


def test_known_lesson_routes_have_empty_routing_warnings() -> None:
    """The diagnostic channel is empty for a clean partition (no typos)."""
    finding = sg.ShipFinding(
        lesson_id="L020",  # CRITICAL → gate
        severity="BLOCK",
        location="src/x.py:1",
        message="[lesson:L020] m",
    )
    result = sg.partition_by_lesson_severity([finding], _lessons_default())
    assert sg.routing_warnings(result) == ()


def test_known_article_routes_have_empty_routing_warnings() -> None:
    """Article partition mirrors lesson partition: clean input → empty channel."""
    finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] m",
    )
    result = sg.partition_by_article_level([finding], _articles())
    assert sg.routing_warnings(result) == ()


# --- H7 — Ack-hook strips both tag regexes regardless of kind -------------


def test_ack_hook_strips_both_tag_kinds_from_article_bullet(tmp_path: Path) -> None:
    """An article-kind finding whose message echoes a [lesson:L<NNN>] tag must
    NOT leak the lesson tag into the ADR body line.

    Pre-fix the ack-hook ran ``_TAG_RE.sub`` only when the finding was
    article-kind, leaving the [lesson:L007] tag intact in the bullet.
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
    article_finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] [lesson:L007] dual-tag echo",
    )
    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=[article_finding],
        articles=_articles(),
        lessons=_lessons_default(),
        now=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)
    decisions = decisions_path.read_text(encoding="utf-8")
    # Bullet starts with its own [constitution:A1] tag, but the echoed
    # [lesson:L007] tag inside the message must be stripped.
    bullet_lines = [line for line in decisions.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 1
    bullet = bullet_lines[0]
    assert "[lesson:L007]" not in bullet, (
        f"lesson tag must be stripped from article bullet body, got {bullet!r}"
    )
    # The leading [constitution:A1] tag is the bullet's own prefix and stays.
    assert bullet.count("[constitution:A1]") == 1


def test_ack_hook_strips_both_tag_kinds_from_lesson_bullet(tmp_path: Path) -> None:
    """A lesson-kind finding whose message echoes a [constitution:A<n>] tag
    must NOT leak the article tag into the ADR body line.
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
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A2] [lesson:L007] dual-tag echo",
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
    decisions = decisions_path.read_text(encoding="utf-8")
    bullet_lines = [line for line in decisions.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 1
    bullet = bullet_lines[0]
    assert "[constitution:A2]" not in bullet, (
        f"article tag must be stripped from lesson bullet body, got {bullet!r}"
    )
    assert bullet.count("[lesson:L007]") == 1


# --- M1 — `_ACK_PREFIX` rename ------------------------------------------


def test_ack_prefix_is_ship_gate_finding_not_constitution_finding() -> None:
    """The shared prefix reads ``Ship-gate finding ...`` so the heading is
    accurate for both article and lesson acknowledgements.
    """
    assert sg._ACK_PREFIX == "Ship-gate finding acknowledged at ship"


def test_ack_hook_article_uses_new_prefix(tmp_path: Path) -> None:
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
    article_finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] m",
    )
    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=[article_finding],
        articles=_articles(),
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)
    decisions = decisions_path.read_text(encoding="utf-8")
    assert "## 2026-05-07 — Ship-gate finding acknowledged at ship" in decisions
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    cause = payload["deviations"][-1]["cause"]
    assert cause.startswith("Ship-gate finding acknowledged at ship: ")


def test_ack_hook_lesson_uses_new_prefix(tmp_path: Path) -> None:
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
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
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
    decisions = decisions_path.read_text(encoding="utf-8")
    assert "## 2026-05-11 — Ship-gate finding acknowledged at ship" in decisions
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    cause = payload["deviations"][-1]["cause"]
    assert cause.startswith("Ship-gate finding acknowledged at ship: ")


def test_new_prefix_round_trips_validate_deviations(tmp_path: Path) -> None:
    """The new prefix must still satisfy the 60-char substring cross-ref
    in ``validate_deviations`` — the body's ``Cause: ...`` line carries the
    cause verbatim, so the substring match succeeds against the body group.
    """
    feature_root = tmp_path / "feature"
    feature_root.mkdir()
    state_path = feature_root / "state.json"
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
    decisions_path = feature_root / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    article_finding = sg.ShipFinding(
        article_id="A1",
        severity="HIGH",
        location="src/x.py:1",
        message="[constitution:A1] m",
    )
    lesson_finding = sg.ShipFinding(
        lesson_id="L007",
        severity="HIGH",
        location="src/x.py:1",
        message="[lesson:L007] m",
    )
    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=[article_finding, lesson_finding],
        articles=_articles(),
        lessons=_lessons_default(),
        now=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    hook(feature_root)
    assert validate_deviations(feature_root) == []


# --- M6 — Case-insensitive SHA normalization ------------------------------


def test_parse_review_findings_normalizes_uppercase_sha(tmp_path: Path) -> None:
    """Uppercase 40-hex SHA in the Resolved by cell is accepted and stored
    lowercase.
    """
    src = tmp_path / "review_upper_sha.md"
    upper = "ABCDEF" + "0" * 34
    src.write_text(
        f"""---
spec: 2026-05-11-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | {upper} | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert len(findings) == 1
    assert findings[0].resolved_by == upper.lower()


def test_parse_review_findings_normalizes_mixed_case_sha(tmp_path: Path) -> None:
    """Mixed-case 40-hex SHA normalizes to fully-lowercase on the ShipFinding."""
    src = tmp_path / "review_mixed_sha.md"
    mixed = "Aa1Bb2" + "0" * 34
    src.write_text(
        f"""---
spec: 2026-05-11-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | {mixed} | src/x.py:1 | [constitution:A1] tag here | fix | self |
""",
        encoding="utf-8",
    )
    findings = sg.parse_review_findings(src)
    assert findings[0].resolved_by == mixed.lower()


def test_intel_lessons_parse_normalizes_uppercase_sha(tmp_path: Path) -> None:
    """``tools.intel.lessons.parse`` mirrors ``parse_review_findings``: a
    Resolved-by SHA in uppercase is accepted and stored lowercase.
    """
    upper = "ABCDEF" + "0" * 34
    body = (
        '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n# FORGE Lessons\n\n'
        "## L001 — title\n\n"
        "**Captured:** 2026-05-11 from feature 2026-05-11-demo\n"
        f"**Resolved by:** {upper}\n"
        "**Trap:** a trap\n"
        "**Avoidance:** an avoidance\n"
        "**Tags:** imports\n"
        "**Severity:** HIGH\n"
        "**Status:** active\n"
    )
    path = tmp_path / "lessons.md"
    path.write_text(body, encoding="utf-8")
    parsed = intel_lessons.parse(path)
    assert parsed[0].resolved_by == upper.lower()


def test_intel_lessons_parse_normalizes_mixed_case_sha(tmp_path: Path) -> None:
    mixed = "Aa1Bb2" + "0" * 34
    body = (
        '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n# FORGE Lessons\n\n'
        "## L001 — title\n\n"
        "**Captured:** 2026-05-11 from feature 2026-05-11-demo\n"
        f"**Resolved by:** {mixed}\n"
        "**Trap:** a trap\n"
        "**Avoidance:** an avoidance\n"
        "**Tags:** imports\n"
        "**Severity:** HIGH\n"
        "**Status:** active\n"
    )
    path = tmp_path / "lessons.md"
    path.write_text(body, encoding="utf-8")
    parsed = intel_lessons.parse(path)
    assert parsed[0].resolved_by == mixed.lower()


# --- L5 — render_git_conventions_info_summary ---------------------------


def test_render_git_conventions_info_summary_empty_bucket_returns_empty_string() -> None:
    partition = sg.partition_git_conventions([])
    assert sg.render_git_conventions_info_summary(partition) == ""


def test_render_git_conventions_info_summary_renders_one_line_per_finding() -> None:
    low = Finding("LOW", "git-conventions", Path("state.json"), "abc123: trailer style nit")
    info_finding = Finding(
        "INFO", "git-conventions", Path("state.json"), "def456: trailer order non-canonical"
    )
    partition = sg.partition_git_conventions([low, info_finding])
    rendered = sg.render_git_conventions_info_summary(partition)
    bullet_lines = [line for line in rendered.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 2
    assert "abc123" in rendered
    assert "def456" in rendered
