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
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
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
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
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
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
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
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
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
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
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


def test_amend_rollback_removes_decisions_file_when_we_created_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic-pair contract: when decisions.md did NOT exist before the amend,
    a decisions-append failure must remove the auto-created file along with
    rolling back the Constitution. Otherwise the bare header file lingers."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
    original = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    constitution.write_text(original, encoding="utf-8")
    decisions_path = repo / "decisions.md"
    assert not decisions_path.exists(), "precondition: no decisions.md yet"

    after_text = original.replace(
        "Hard-coded credentials are the most common cause",
        "Hard-coded credentials remain the most common cause",
    )
    inputs = StubInputs(editor_writes=after_text, decisions_entry="text edit only")

    original_open = Path.open

    def patched(self, *a, **kw):  # type: ignore[no-untyped-def]
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

    assert constitution.read_text(encoding="utf-8") == original
    assert not decisions_path.exists(), (
        "decisions.md was created by _ensure_decisions_file but never written; "
        "rollback must remove it to keep the atomic-pair contract"
    )


def test_amend_constitution_replaces_existing_updated_field(tmp_path: Path) -> None:
    """Cover the _replace_or_append_frontmatter replace branch (every amend after the first)."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    # Seed a Constitution that already has an `updated:` field.
    seeded = (
        '---\nversion: 0.1.0\ncreated: "2026-01-01"\nupdated: "2025-12-15"\n---\n\n'
        "# Project Constitution\n\n"
        "## Article 1 — Secrets via vault only [CRITICAL]\n"
        "**Rule:** Use the vault loader.\n"
        "**Reference:** OWASP A02:2021\n"
        "**Rationale:** Prevent leaked credentials.\n"
        "**Exception:** None.\n"
    )
    constitution.write_text(seeded, encoding="utf-8")

    # Editor makes a clarification edit (rule rewording) -> classify=patch.
    after_text = seeded.replace("Use the vault loader.", "Always use the vault loader.")
    inputs = StubInputs(editor_writes=after_text, decisions_entry="rule rewording")

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
    assert 'updated: "2026-05-07"' in final_text
    # Old date must not survive.
    assert '"2025-12-15"' not in final_text
    # No duplicate `updated:` lines.
    assert final_text.count("\nupdated:") == 1


def test_propose_starter_articles_picks_orm_and_test_floor(tmp_path: Path) -> None:
    repo = FIXTURES / "bootstrap_repo_python"
    proposals = am.propose_starter_articles(repo_root=repo)
    titles = {p.title.lower() for p in proposals}
    assert any("secrets" in t for t in titles), "secrets article always proposed"
    assert any("repository" in t for t in titles), "ORM detected → repository article"
    assert any("test coverage" in t for t in titles), "pytest detected → coverage floor"
    assert len(proposals) <= 5, "default cap = 5"


def test_propose_starter_articles_uses_bare_dep_name_match_not_substring(
    tmp_path: Path,
) -> None:
    """Detection MUST tokenize dep names before comparison, not substring-match.

    A dep entry like ``preact>=10`` would false-match against ``"react"`` under
    the old ``kw in blob`` substring check. Once any future signal extends the
    keyword set with ``"react"``, this test guards the bare-name boundary so
    ``preact`` never silently trips a React-shaped article.
    """
    repo = tmp_path / "preact_only"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies": {"preact": "^10"}}', encoding="utf-8")
    proposals = am.propose_starter_articles(repo_root=repo)
    # ORM/test signals must not fire on `preact` alone (covers `pytest` substring
    # match too — `preact` contains neither).
    titles = {p.title.lower() for p in proposals}
    assert not any("repository" in t for t in titles)
    assert not any("test coverage" in t for t in titles)


def test_propose_starter_articles_no_orm_no_test_only_minimum(tmp_path: Path) -> None:
    repo = tmp_path / "minimal"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "minimal"\n', encoding="utf-8")
    proposals = am.propose_starter_articles(repo_root=repo)
    titles = {p.title.lower() for p in proposals}
    assert any("secrets" in t for t in titles), "secrets always proposed"
    assert not any("repository" in t for t in titles)
    assert not any("test coverage" in t for t in titles)


def test_bootstrap_constitution_writes_starter_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest>=8.0"]\n',
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    accept_all = lambda proposal: ("accept", proposal)  # noqa: E731
    am.bootstrap_constitution(
        repo_root=repo,
        decisions_path=decisions_path,
        review_proposal=accept_all,
        today=date(2026, 5, 7),
    )

    final = (repo / ".forge" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert "version: 0.1.0" in final
    assert "## Article 1 — " in final
    assert "Constitution bootstrap: v0.1.0" in decisions_path.read_text(encoding="utf-8")


def test_bootstrap_constitution_refuses_when_file_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / ".forge" / "CONSTITUTION.md").write_text(
        '---\nversion: 0.1.0\ncreated: "2026-01-01"\n---\n', encoding="utf-8"
    )
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    accept_all = lambda proposal: ("accept", proposal)  # noqa: E731

    with pytest.raises(am.AmendError, match="already exists"):
        am.bootstrap_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            review_proposal=accept_all,
            today=date(2026, 5, 7),
        )


def test_bootstrap_drop_removes_proposal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest>=8.0", "sqlalchemy>=2.0"]\n',
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    def review(proposal: am.ProposedArticle) -> tuple[str, am.ProposedArticle | None]:
        if "secrets" in proposal.title.lower():
            return ("drop", None)
        return ("accept", proposal)

    am.bootstrap_constitution(
        repo_root=repo,
        decisions_path=decisions_path,
        review_proposal=review,
        today=date(2026, 5, 7),
    )

    final = (repo / ".forge" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert "secrets" not in final.lower()
    # At least one article (repository or test coverage) remains.
    assert "## Article 1 —" in final


def test_bootstrap_constitution_rolls_back_on_validator_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validator rejects the assembled body -> Constitution stays absent, decisions clean."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest>=8.0"]\n',
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    # Force the validator subprocess to report failure regardless of body content.
    def _fake_validate(target: Path) -> None:
        raise am.AmendError("Constitution validation failed (forced)")

    monkeypatch.setattr(am, "_validate_constitution_body", _fake_validate)

    accept_all = lambda proposal: ("accept", proposal)  # noqa: E731

    with pytest.raises(am.AmendError, match="validation failed"):
        am.bootstrap_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            review_proposal=accept_all,
            today=date(2026, 5, 7),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert "Constitution bootstrap" not in decisions_path.read_text(encoding="utf-8")


def test_bootstrap_constitution_rolls_back_constitution_on_decisions_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decisions append fails -> freshly-written Constitution is deleted; pair stays atomic."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest>=8.0"]\n',
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    original_open = Path.open

    def _patched_open(self, *a, **kw):  # type: ignore[no-untyped-def]
        if self == decisions_path and a and a[0] == "a":
            raise OSError("simulated append failure")
        return original_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _patched_open)

    accept_all = lambda proposal: ("accept", proposal)  # noqa: E731

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.bootstrap_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            review_proposal=accept_all,
            today=date(2026, 5, 7),
        )

    # Constitution must be gone; decisions log unchanged.
    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert decisions_path.read_text(encoding="utf-8") == "# Decisions\n\n"


def test_bootstrap_rollback_removes_decisions_file_when_we_created_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic-pair contract for bootstrap: an append failure with no
    pre-existing decisions.md must delete BOTH the freshly-written
    Constitution AND the decisions.md auto-created in this call."""
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest>=8.0"]\n',
        encoding="utf-8",
    )
    decisions_path = repo / "decisions.md"
    assert not decisions_path.exists(), "precondition: no decisions.md yet"

    original_open = Path.open

    def _patched_open(self, *a, **kw):  # type: ignore[no-untyped-def]
        if self == decisions_path and a and a[0] == "a":
            raise OSError("simulated append failure")
        return original_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _patched_open)

    accept_all = lambda proposal: ("accept", proposal)  # noqa: E731

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.bootstrap_constitution(
            repo_root=repo,
            decisions_path=decisions_path,
            review_proposal=accept_all,
            today=date(2026, 5, 7),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert not decisions_path.exists(), (
        "decisions.md was created by _ensure_decisions_file but never written; "
        "rollback must remove it to keep the atomic-pair contract"
    )
