"""Tests for atomic decisions.md append + race-free decisions.md bootstrap.

Covers ``append_decisions_atomic`` and the hardened ``ensure_decisions_file``,
plus regression checks that every caller in :mod:`tools.constitution_amend`
routes its decisions.md append through the atomic helper instead of a raw
``open("a")``. The raw append is atomic only for payloads at or below
``PIPE_BUF`` (~4 KiB on Linux); bootstrap ADRs and the multi-rule conventions
ADR can exceed that, and two concurrent writers can interleave half-written
rows or both believe they own the bootstrap header.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tools import constitution_amend as am
from tools.validate.conventions import Convention

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "_constitution"


# ---------------------------------------------------------------------------
# append_decisions_atomic — direct tests
# ---------------------------------------------------------------------------


def test_append_decisions_atomic_appends_to_existing_header(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.md"
    decisions.write_text("# Decisions\n\n", encoding="utf-8")

    am.append_decisions_atomic(decisions, "\n## Entry 1\n")

    assert decisions.read_text(encoding="utf-8") == "# Decisions\n\n\n## Entry 1\n"


def test_append_decisions_atomic_treats_missing_file_as_empty_body(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.md"
    assert not decisions.exists()

    am.append_decisions_atomic(decisions, "\n## Bootstrap entry\n")

    # Helper is the write primitive; the bootstrap header lives in
    # ensure_decisions_file. When the file is missing, the entry lands
    # verbatim as the full file body.
    assert decisions.read_text(encoding="utf-8") == "\n## Bootstrap entry\n"


def test_append_decisions_atomic_preserves_large_payload_intact(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.md"
    decisions.write_text("# Decisions\n\n", encoding="utf-8")
    # 10 KiB is above PIPE_BUF on Linux (4096), which is where a raw
    # ``open("a") + write`` ceases to be atomic.
    large_entry = "\n## Big entry\n" + ("body line " * 1000) + "\n"

    am.append_decisions_atomic(decisions, large_entry)

    final = decisions.read_text(encoding="utf-8")
    assert final.startswith("# Decisions\n\n\n## Big entry\n")
    assert final.endswith("\n")
    assert final.count("body line ") == 1000


def test_append_decisions_atomic_two_concurrent_threads_keep_both_entries(
    tmp_path: Path,
) -> None:
    """Two threads append concurrently; both entries must land, no interleave.

    The advisory ``fcntl.flock`` lock serializes the read-modify-write so the
    losing thread reads the post-winner state before its own append, never the
    pre-winner state. On platforms without ``fcntl`` (Windows), this property
    is not guaranteed and the test would be skipped, but FORGE's CI runs on
    POSIX.
    """
    pytest.importorskip("fcntl")
    decisions = tmp_path / "decisions.md"
    decisions.write_text("# Decisions\n\n", encoding="utf-8")

    entry_a = "\n## Entry A\n" + ("a" * 5000) + "\n"
    entry_b = "\n## Entry B\n" + ("b" * 5000) + "\n"
    barrier = threading.Barrier(2)

    def writer(entry: str) -> None:
        barrier.wait()
        am.append_decisions_atomic(decisions, entry)

    threads = [
        threading.Thread(target=writer, args=(entry_a,)),
        threading.Thread(target=writer, args=(entry_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = decisions.read_text(encoding="utf-8")
    # Both entries must be present in full — no lost write, no interleave.
    assert "## Entry A" in final
    assert "## Entry B" in final
    assert final.count("a" * 5000) == 1
    assert final.count("b" * 5000) == 1
    # The file must still start with the canonical header.
    assert final.startswith("# Decisions\n\n")


# ---------------------------------------------------------------------------
# ensure_decisions_file — race-free bootstrap
# ---------------------------------------------------------------------------


def test_ensure_decisions_file_creates_with_canonical_header(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.md"

    created = am.ensure_decisions_file(decisions)

    assert created is True
    assert decisions.read_text(encoding="utf-8") == "# Decisions\n\n"


def test_ensure_decisions_file_returns_false_when_file_already_exists(
    tmp_path: Path,
) -> None:
    decisions = tmp_path / "decisions.md"
    decisions.write_text("# Decisions\n\n## Existing entry\n", encoding="utf-8")

    created = am.ensure_decisions_file(decisions)

    assert created is False
    # Body unchanged.
    assert decisions.read_text(encoding="utf-8") == "# Decisions\n\n## Existing entry\n"


def test_ensure_decisions_file_does_not_overwrite_non_canonical_body(
    tmp_path: Path,
) -> None:
    """User may have written their own decisions.md with a different header."""
    decisions = tmp_path / "decisions.md"
    custom = "# My personal decisions log\n\nNotes here.\n"
    decisions.write_text(custom, encoding="utf-8")

    created = am.ensure_decisions_file(decisions)

    assert created is False
    assert decisions.read_text(encoding="utf-8") == custom


def test_ensure_decisions_file_concurrent_claim_only_one_wins(tmp_path: Path) -> None:
    """Two threads call ensure_decisions_file on a missing file simultaneously.

    Exactly one must return True. The file must contain exactly one canonical
    header — not two concatenated headers from a TOCTOU loss.
    """
    decisions = tmp_path / "decisions.md"
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def claim() -> None:
        barrier.wait()
        outcome = am.ensure_decisions_file(decisions)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True], f"exactly one caller must win the claim; got {results}"
    # Single header, not "# Decisions\n\n# Decisions\n\n".
    assert decisions.read_text(encoding="utf-8") == "# Decisions\n\n"


# ---------------------------------------------------------------------------
# Migration regression — every caller routes through the atomic helper
# ---------------------------------------------------------------------------


class _StubInputs:
    """Mirrors the stub in test_constitution_amend; minimal local copy."""

    def __init__(self, *, editor_writes: str, decisions_entry: str = "text edit only") -> None:
        self.editor_writes = editor_writes
        self.decisions_entry = decisions_entry

    def open_editor(self, path: Path) -> None:
        path.write_text(self.editor_writes, encoding="utf-8")

    def prompt_decisions(self, scope: str, new_version: str) -> str:
        return self.decisions_entry


def _good_drafted_body() -> str:
    """Render a minimal valid skill-drafted Constitution body."""
    head = '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n'
    intro = "# Project Constitution\n\nIntro paragraph.\n\n"
    article = (
        "## Article 1 — Sample article [SHOULD]\n"
        "**Rule:** Sample rule body that is long enough to count.\n"
        "**Reference:** Team consensus 2026-05.\n"
        "**Rationale:** Sample rationale explaining the trade-off.\n"
        "**Exception:** None.\n"
    )
    return head + intro + article


def _convention_rule(rule_id: str = "cite-constitution") -> Convention:
    return Convention(
        id=rule_id,
        source_file="AGENTS.md",
        source_line=12,
        pattern_kind="required_text",
        pattern=r"cite the Constitution",
        scope=("dispatch_brief",),
        severity="HIGH",
    )


def test_amend_constitution_decisions_append_uses_atomic_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration regression: amend_constitution routes through the atomic helper.

    Patches ``am.append_decisions_atomic`` to raise. If the call site still
    used raw ``open("a")``, the patch would never fire and the amend would
    succeed; we assert it raises AmendError and rolls back the Constitution.
    """
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions = repo / "decisions.md"
    decisions.write_text("# Decisions\n\n", encoding="utf-8")

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    after_text = original.replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    inputs = _StubInputs(editor_writes=after_text)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.amend_constitution(
            repo_root=repo,
            decisions_path=decisions,
            editor=inputs.open_editor,
            prompter=inputs.prompt_decisions,
            today=date(2026, 5, 11),
        )

    # Constitution restored.
    assert constitution.read_text(encoding="utf-8") == original
    # Pre-existing decisions.md untouched.
    assert decisions.read_text(encoding="utf-8") == "# Decisions\n\n"


def test_persist_drafted_constitution_decisions_append_uses_atomic_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_drafted_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    # Both files rolled back: no Constitution, no auto-created decisions.md.
    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert not decisions.exists()


def test_append_conventions_entries_decisions_append_uses_atomic_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.append_conventions_entries(
            repo,
            [_convention_rule()],
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    # Conventions.json rollback removed the freshly-written file.
    assert not (repo / ".forge" / "conventions.json").exists()
    # Auto-created decisions.md also removed.
    assert not decisions.exists()


def test_log_advisory_entries_decisions_append_uses_atomic_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    entry = am.AdvisoryEntry(
        rule_text="Cite the Constitution in every dispatch brief.",
        source_file="AGENTS.md",
        source_line=42,
    )

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.log_advisory_entries(
            repo_root=repo,
            entries=[entry],
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    # Auto-created decisions.md removed.
    assert not decisions.exists()


# ---------------------------------------------------------------------------
# End-to-end: post-migration body shape is byte-equal to pre-migration
# ---------------------------------------------------------------------------


def test_amend_constitution_decisions_body_after_two_amends_has_no_interleave(
    tmp_path: Path,
) -> None:
    """Two sequential amends produce two complete entries in order."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions = repo / "decisions.md"
    decisions.write_text("# Decisions\n\n", encoding="utf-8")

    first_after = constitution.read_text(encoding="utf-8").replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    am.amend_constitution(
        repo_root=repo,
        decisions_path=decisions,
        editor=_StubInputs(editor_writes=first_after, decisions_entry="edit one").open_editor,
        prompter=_StubInputs(
            editor_writes=first_after, decisions_entry="edit one"
        ).prompt_decisions,
        today=date(2026, 5, 11),
    )

    second_after = constitution.read_text(encoding="utf-8").replace(
        "Hard-coded credentials remain the most common cause",
        "Hard-coded credentials continue to be the most common cause",
    )
    am.amend_constitution(
        repo_root=repo,
        decisions_path=decisions,
        editor=_StubInputs(editor_writes=second_after, decisions_entry="edit two").open_editor,
        prompter=_StubInputs(
            editor_writes=second_after, decisions_entry="edit two"
        ).prompt_decisions,
        today=date(2026, 5, 12),
    )

    body = decisions.read_text(encoding="utf-8")
    # Header preserved + both entries present.
    assert body.startswith("# Decisions\n\n")
    assert body.count("Constitution amendment: v0.1.1") == 1
    assert body.count("Constitution amendment: v0.1.2") == 1
    # First entry strictly precedes the second.
    assert body.index("edit one") < body.index("edit two")


def test_append_conventions_entries_round_trip_after_seed(tmp_path: Path) -> None:
    """Seed one rule, then append a second; decisions.md keeps both ADRs in order."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    am.append_conventions_entries(
        repo,
        [_convention_rule(rule_id="rule-one")],
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )
    am.append_conventions_entries(
        repo,
        [_convention_rule(rule_id="rule-two")],
        decisions_path=decisions,
        today=date(2026, 5, 12),
    )

    body = decisions.read_text(encoding="utf-8")
    assert body.startswith("# Decisions\n\n")
    assert body.count("rule-one") >= 1
    assert body.count("rule-two") >= 1
    assert body.index("rule-one") < body.index("rule-two")

    payload: dict[str, Any] = json.loads(
        (repo / ".forge" / "conventions.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
    assert [rule["id"] for rule in payload["rules"]] == ["rule-one", "rule-two"]
