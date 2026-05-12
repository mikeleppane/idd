"""End-to-end bootstrap pipeline: collect signals, validate draft, persist atomically.

Walks the full pipeline using a real signal payload + a real Constitution
draft (no LLM call — deterministic fixture) + a real persist call. Catches
seam regressions like a :class:`BootstrapSignals` field-name change breaking
the validator or a draft-shape change breaking the structural validator.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath

from tools.constitution_amend import (
    BootstrapSignals,
    collect_bootstrap_signals,
    persist_drafted_constitution,
    validate_drafted_markdown,
)

_NONCE_HEX = "deadbeefcafebabe"


def _write_manifest_fixture(repo: Path) -> None:
    """Drop a minimal manifest + docs trio so collect picks up known names."""
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "forge-bootstrap-e2e-fixture"\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    (repo / "AGENTS.md").write_text(
        "# Agents\n\nFixture project agents file.\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text(
        "# Bootstrap E2E\n\nFixture README for integration coverage.\n",
        encoding="utf-8",
    )


def _draft_body() -> str:
    """Return a deterministic, structurally-valid Constitution body.

    Three articles in monotonic order with Rule + Reference + Rationale +
    Exception fields — the minimum surface validate_drafted_markdown and the
    structural validator both demand.
    """
    return (
        "---\n"
        "version: 0.1.0\n"
        'created: "2026-05-11"\n'
        "---\n"
        "\n"
        "# Project Constitution\n"
        "\n"
        "Intro paragraph for the fixture project.\n"
        "\n"
        "## Article 1 — Secrets via vault [CRITICAL]\n"
        "**Rule:** All credentials live in the team vault; never commit them.\n"
        "**Reference:** Team consensus 2026-05.\n"
        "**Rationale:** Prevents secret leaks via git history.\n"
        "**Exception:** None.\n"
        "\n"
        "## Article 2 — Tests gate merges [SHOULD]\n"
        "**Rule:** Every PR must show green tests before merge.\n"
        "**Reference:** Team consensus 2026-05.\n"
        "**Rationale:** Keeps main green for downstream branches.\n"
        "**Exception:** Hotfixes for production outages may bypass with sign-off.\n"
        "\n"
        "## Article 3 — Document deviations [MAY]\n"
        "**Rule:** Add an ADR when intentionally deviating from a stated convention.\n"
        "**Reference:** Team consensus 2026-05.\n"
        "**Rationale:** Future maintainers see the why, not just the what.\n"
        "**Exception:** None.\n"
    )


def test_constitution_bootstrap_full_pipeline_persists_byte_equal_body(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_manifest_fixture(repo)

    # Step 1: collect signals (pinned nonce so the marker is reproducible).
    signals = collect_bootstrap_signals(repo, nonce_hex=_NONCE_HEX)
    assert isinstance(signals, BootstrapSignals)
    collected_paths = {sf.relative_path for sf in signals.files}
    assert PurePosixPath("pyproject.toml") in collected_paths
    assert PurePosixPath("AGENTS.md") in collected_paths
    assert PurePosixPath("README.md") in collected_paths
    assert signals.dropped == []
    assert signals.dropped_records == []
    assert signals.dropped_for_escape == []
    assert signals.truncated == []
    assert signals.total_bytes > 0

    # Step 2: validate the deterministic draft body.
    body = _draft_body()
    articles = validate_drafted_markdown(body)
    assert [a.id for a in articles] == ["A1", "A2", "A3"]
    assert [a.level for a in articles] == ["CRITICAL", "SHOULD", "MAY"]

    # Step 3: persist atomically against the same repo root.
    decisions = repo / "decisions.md"
    constitution_path = persist_drafted_constitution(
        repo_root=repo,
        body=body,
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )

    assert constitution_path == repo / ".forge" / "CONSTITUTION.md"
    assert constitution_path.read_text(encoding="utf-8") == body

    entry = decisions.read_text(encoding="utf-8")
    assert "# Decisions" in entry
    assert "v0.1.0" in entry
    assert "3 article(s)" in entry
    assert "2026-05-11" in entry


def test_constitution_bootstrap_collect_is_byte_equal_with_pinned_nonce(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_manifest_fixture(repo)

    first = collect_bootstrap_signals(repo, nonce_hex=_NONCE_HEX)
    second = collect_bootstrap_signals(repo, nonce_hex=_NONCE_HEX)

    assert first == second
    assert [sf.relative_path for sf in first.files] == [sf.relative_path for sf in second.files]
    assert [sf.body for sf in first.files] == [sf.body for sf in second.files]
