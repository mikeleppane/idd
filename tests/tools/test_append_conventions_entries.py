"""Tests for tools.constitution_amend.append_conventions_entries."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tools import constitution_amend as am
from tools.validate.conventions import Convention


def _rule(
    *,
    rule_id: str,
    pattern_kind: str = "required_text",
    pattern: str = r"cite the Constitution",
    scope: tuple[str, ...] = ("dispatch_brief",),
    source_file: str = "AGENTS.md",
    source_line: int = 12,
    severity: str = "HIGH",
) -> Convention:
    return Convention(
        id=rule_id,
        source_file=source_file,
        source_line=source_line,
        pattern_kind=pattern_kind,  # type: ignore[arg-type]
        pattern=pattern,
        scope=scope,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
    )


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    return repo


def test_append_conventions_entries_happy_path_creates_file(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"
    entry = _rule(rule_id="cite-constitution")

    result = am.append_conventions_entries(
        repo,
        [entry],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )

    assert result == repo / ".forge" / "conventions.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"] == "cite-constitution"

    adr = decisions.read_text(encoding="utf-8")
    assert "Conventions resync" in adr
    assert "cite-constitution" in adr
    assert "2026-05-11" in adr
    assert "1 convention entry" in adr


def test_append_conventions_entries_merges_with_existing_in_order(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    am.append_conventions_entries(
        repo,
        [_rule(rule_id="first-rule")],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )

    am.append_conventions_entries(
        repo,
        [
            _rule(rule_id="second-rule", pattern="MUST do thing"),
            _rule(rule_id="third-rule", pattern="MUST do other"),
        ],
        decisions_path=decisions,
        today=date(2026, 5, 12),
    )

    payload = json.loads((repo / ".forge" / "conventions.json").read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in payload]
    assert ids == ["first-rule", "second-rule", "third-rule"]

    adr = decisions.read_text(encoding="utf-8")
    assert "second-rule" in adr
    assert "third-rule" in adr
    assert "2 convention entries" in adr


def test_append_conventions_entries_rejects_id_collision_with_existing(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    am.append_conventions_entries(
        repo,
        [_rule(rule_id="dup-id")],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )
    before_body = (repo / ".forge" / "conventions.json").read_text(encoding="utf-8")
    before_decisions = decisions.read_text(encoding="utf-8")

    with pytest.raises(am.AmendError, match="dup-id"):
        am.append_conventions_entries(
            repo,
            [_rule(rule_id="dup-id", pattern="MUST something else")],
            decisions_path=decisions,
            today=date(2026, 5, 12),
        )

    # No disk mutation.
    assert (repo / ".forge" / "conventions.json").read_text(encoding="utf-8") == before_body
    assert decisions.read_text(encoding="utf-8") == before_decisions


def test_append_conventions_entries_rejects_duplicate_within_new_entries(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    with pytest.raises(am.AmendError, match="duplicate"):
        am.append_conventions_entries(
            repo,
            [
                _rule(rule_id="rule-twice"),
                _rule(rule_id="rule-twice", pattern="MUST something"),
            ],
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "conventions.json").exists()
    assert not decisions.exists()


def test_append_conventions_entries_rejects_empty_list(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    with pytest.raises(am.AmendError, match="at least one"):
        am.append_conventions_entries(
            repo,
            [],
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "conventions.json").exists()
    assert not decisions.exists()


def test_append_conventions_entries_rejects_bad_regex_and_restores(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    # Seed the file with one valid entry so we can verify restore-to-prior-body.
    am.append_conventions_entries(
        repo,
        [_rule(rule_id="seed-rule")],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )
    before_body = (repo / ".forge" / "conventions.json").read_text(encoding="utf-8")

    bad = _rule(rule_id="bad-regex", pattern_kind="forbidden_text", pattern="(unbalanced")

    with pytest.raises(am.AmendError, match=r"failed validation|failed to compile"):
        am.append_conventions_entries(
            repo,
            [bad],
            decisions_path=decisions,
            today=date(2026, 5, 12),
        )

    # Conventions.json restored to pre-call body.
    assert (repo / ".forge" / "conventions.json").read_text(encoding="utf-8") == before_body


def test_append_conventions_entries_decisions_append_failure_rolls_back_absent_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.append_conventions_entries(
            repo,
            [_rule(rule_id="solo")],
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    # Conventions.json was absent before; rollback must remove it.
    assert not (repo / ".forge" / "conventions.json").exists()
    # decisions.md was freshly created by ensure_decisions_file → also removed.
    assert not decisions.exists()


def test_append_conventions_entries_decisions_append_failure_restores_existing_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    # Seed the conventions file + a real decisions.md.
    am.append_conventions_entries(
        repo,
        [_rule(rule_id="seed-rule")],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )
    before_body = (repo / ".forge" / "conventions.json").read_text(encoding="utf-8")
    before_decisions = decisions.read_text(encoding="utf-8")

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.append_conventions_entries(
            repo,
            [_rule(rule_id="new-rule", pattern="MUST do thing")],
            decisions_path=decisions,
            today=date(2026, 5, 12),
        )

    # Conventions.json restored to pre-call body; decisions.md preserved.
    assert (repo / ".forge" / "conventions.json").read_text(encoding="utf-8") == before_body
    assert decisions.read_text(encoding="utf-8") == before_decisions


def test_append_conventions_entries_today_overrides_default(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    decisions = repo / "decisions.md"

    am.append_conventions_entries(
        repo,
        [_rule(rule_id="x-rule")],
        decisions_path=decisions,
        today=date(2023, 7, 4),
    )

    assert "2023-07-04" in decisions.read_text(encoding="utf-8")


def test_append_conventions_entries_default_decisions_path(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    result = am.append_conventions_entries(
        repo,
        [_rule(rule_id="default-decisions")],
        today=date(2026, 5, 11),
    )

    assert result == repo / ".forge" / "conventions.json"
    assert (repo / "decisions.md").exists()
