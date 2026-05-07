"""Tests for tools.constitution_amend semver bump rules + atomic edit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from tools import constitution_amend as am

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "_constitution"


def test_classify_change_clarification_is_patch(tmp_path: Path) -> None:
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before.replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials are still the most common cause",
    )
    assert am.classify_change(before, after) == "patch"


def test_classify_change_add_article_is_minor(tmp_path: Path) -> None:
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before + (
        "\n## Article 6 — New rule [SHOULD]\n**Rule:** New rule body.\n**Exception:** None.\n"
    )
    assert am.classify_change(before, after) == "minor"


def test_classify_change_loosen_level_is_minor() -> None:
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before.replace("[CRITICAL]", "[SHOULD]", 1)
    assert am.classify_change(before, after) == "minor"


def test_classify_change_remove_article_is_major() -> None:
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before.replace(
        "## Article 5 — Documentation in commit body [MAY]",
        "## ZZZ-removed-article-marker",
    )
    # Strip the rest of A5's body too:
    after = after.split("## ZZZ-removed-article-marker")[0]
    assert am.classify_change(before, after) == "major"


def test_classify_change_tighten_level_is_major() -> None:
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before.replace("[SHOULD]", "[CRITICAL]", 1)
    assert am.classify_change(before, after) == "major"


def test_classify_change_mixed_loosen_then_tighten_is_major() -> None:
    """First level diff is a loosening (should remain provisional minor);
    a later tightening must escalate to major regardless of order."""
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    # Loosen A1 (CRITICAL → SHOULD), then tighten A2 (SHOULD → CRITICAL).
    after = before.replace(
        "## Article 1 — Secrets via vault only [CRITICAL]",
        "## Article 1 — Secrets via vault only [SHOULD]",
    )
    after = after.replace(
        "## Article 2 — Test coverage floor [SHOULD]",
        "## Article 2 — Test coverage floor [CRITICAL]",
    )
    assert am.classify_change(before, after) == "major"


def test_classify_change_mixed_tighten_then_loosen_is_major() -> None:
    """Tightening first must NOT short-circuit before the loop sees the
    loosening (or vice versa). Major wins regardless."""
    before = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    after = before.replace(
        "## Article 2 — Test coverage floor [SHOULD]",
        "## Article 2 — Test coverage floor [CRITICAL]",
    )
    after = after.replace(
        "## Article 1 — Secrets via vault only [CRITICAL]",
        "## Article 1 — Secrets via vault only [SHOULD]",
    )
    assert am.classify_change(before, after) == "major"


def test_bump_version_patch_minor_major() -> None:
    assert am.bump_version("0.1.0", "patch") == "0.1.1"
    assert am.bump_version("0.1.5", "minor") == "0.2.0"
    assert am.bump_version("0.2.7", "major") == "1.0.0"
    assert am.bump_version("1.4.2", "minor") == "1.5.0"


@dataclass
class StubInputs:
    editor_writes: str
    decisions_entry: str = "Routine clarification."

    def open_editor(self, path: Path) -> None:
        path.write_text(self.editor_writes, encoding="utf-8")

    def prompt_decisions(self, scope: str, new_version: str) -> str:
        return self.decisions_entry


def test_amend_constitution_writes_bumped_version_and_decisions_entry(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".idd").mkdir(parents=True)
    constitution = repo / ".idd" / "CONSTITUTION.md"
    constitution.write_text(
        (FIXTURES / "passing.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    decisions_dir = repo / "docs"
    decisions_dir.mkdir()
    decisions_path = decisions_dir / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    after_text = constitution.read_text(encoding="utf-8").replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    inputs = StubInputs(editor_writes=after_text, decisions_entry="text edit only")

    result = am.amend_constitution(
        repo_root=repo,
        decisions_path=decisions_path,
        editor=inputs.open_editor,
        prompter=inputs.prompt_decisions,
        today=date(2026, 5, 7),
    )

    assert result.scope == "patch"
    assert result.new_version == "0.1.1"
    final_text = constitution.read_text(encoding="utf-8")
    assert "version: 0.1.1" in final_text
    decisions = decisions_path.read_text(encoding="utf-8")
    assert "Constitution amendment: v0.1.1" in decisions
    assert "text edit only" in decisions


def test_amend_constitution_aborts_when_validator_rejects(tmp_path: Path) -> None:
    """Validator failure must abort BEFORE any disk write.

    The editor wrote a malformed body. Validation is the gate; both
    Constitution and decisions.md must end at pre-amend state.
    """
    repo = tmp_path / "repo"
    (repo / ".idd").mkdir(parents=True)
    constitution = repo / ".idd" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    bad_text = original + "\n## Article 6 — Broken [SHOULD]\n"
    inputs = StubInputs(editor_writes=bad_text)

    with pytest.raises(am.AmendError):
        am.amend_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            editor=inputs.open_editor,
            prompter=inputs.prompt_decisions,
            today=date(2026, 5, 7),
        )
    assert constitution.read_text(encoding="utf-8") == original
    assert "Constitution amendment" not in decisions_path.read_text(encoding="utf-8")


def test_amend_constitution_rolls_back_constitution_on_decisions_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validator passes + Constitution written + decisions append fails →
    Constitution must be restored to pre-amend state. Atomic-pair contract."""
    repo = tmp_path / "repo"
    (repo / ".idd").mkdir(parents=True)
    constitution = repo / ".idd" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    after_text = original.replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials are still the most common cause",
    )
    inputs = StubInputs(editor_writes=after_text, decisions_entry="text edit only")

    # Plan literal used `decisions_path.open` (a bound method) which would
    # incorrectly reopen decisions.md for any Path. This deviation guards only
    # the failing case (append-mode open against decisions_path) and lets
    # everything else fall through to the original Path.open implementation.
    original_open = Path.open

    def patched(  # type: ignore[no-untyped-def]
        self,
        *a,
        **kw,
    ):
        if self == decisions_path and a and a[0] == "a":
            raise OSError("simulated decisions append failure")
        return original_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", patched)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.amend_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            editor=inputs.open_editor,
            prompter=inputs.prompt_decisions,
            today=date(2026, 5, 7),
        )

    # Constitution restored to original; version unchanged.
    assert constitution.read_text(encoding="utf-8") == original
    assert "version: 0.1.0" in constitution.read_text(encoding="utf-8")
    # Decisions log untouched.
    assert "Constitution amendment" not in decisions_path.read_text(encoding="utf-8")


def test_amend_constitution_creates_missing_decisions_file(tmp_path: Path) -> None:
    """Repo-level decisions.md may not exist yet on first amend. Skill must
    create it with the standard header rather than crash mid-lifecycle."""
    repo = tmp_path / "repo"
    (repo / ".idd").mkdir(parents=True)
    constitution = repo / ".idd" / "CONSTITUTION.md"
    constitution.write_text(
        (FIXTURES / "passing.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    assert not decisions_path.exists(), "precondition: no decisions.md yet"

    after_text = constitution.read_text(encoding="utf-8").replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    inputs = StubInputs(editor_writes=after_text, decisions_entry="text edit only")

    am.amend_constitution(
        repo_root=repo,
        decisions_path=decisions_path,
        editor=inputs.open_editor,
        prompter=inputs.prompt_decisions,
        today=date(2026, 5, 7),
    )

    assert decisions_path.exists()
    decisions = decisions_path.read_text(encoding="utf-8")
    assert decisions.startswith("# Decisions\n")
    assert "Constitution amendment: v0.1.1" in decisions


def test_amend_constitution_rejects_empty_decisions_body(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".idd").mkdir(parents=True)
    constitution = repo / ".idd" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions_path = repo / "decisions.md"

    after_text = original.replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    inputs = StubInputs(editor_writes=after_text, decisions_entry="   ")

    with pytest.raises(am.AmendError, match="decisions entry is empty"):
        am.amend_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            editor=inputs.open_editor,
            prompter=inputs.prompt_decisions,
            today=date(2026, 5, 7),
        )
    # No mutation.
    assert constitution.read_text(encoding="utf-8") == original
    assert not decisions_path.exists()
