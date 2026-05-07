"""Tests for tools.constitution loader, parser, scoring, and filter."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import constitution as cn

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "_constitution"


def test_parse_constitution_returns_articles_in_order() -> None:
    articles = cn.parse_constitution(FIXTURES / "passing.md")
    assert [a.id for a in articles] == ["A1", "A2", "A3", "A4", "A5"]
    a1 = articles[0]
    assert a1.level == "CRITICAL"
    assert a1.title == "Secrets via vault only"
    assert "Secrets" in a1.rule
    assert a1.reference is not None  # passing fixture sets one


def test_parse_constitution_missing_file_raises() -> None:
    with pytest.raises(cn.ConstitutionError, match="not found"):
        cn.parse_constitution(FIXTURES / "does_not_exist.md")


def test_parse_constitution_malformed_header_raises() -> None:
    bad = FIXTURES / "_tmp_bad_header.md"
    bad.write_text(
        "---\nversion: 0.1.0\ncreated: 2026-05-07\n---\n\n## Article 1 — no level marker\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(cn.ConstitutionError, match="malformed"):
            cn.parse_constitution(bad)
    finally:
        bad.unlink()


def test_tokenize_drops_stopwords_and_short_tokens() -> None:
    tokens = cn.tokenize("Secrets are loaded via vault.")
    assert "secrets" in tokens
    assert "loaded" in tokens
    assert "vault" in tokens
    assert "are" not in tokens
    assert "via" not in tokens


def test_extract_scope_keywords_unions_three_sources(tmp_path: Path) -> None:
    repo = tmp_path / "scope_repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        '{"name": "demo", "dependencies": {"react": "^18"}}',
        encoding="utf-8",
    )
    keywords = cn.extract_scope_keywords(
        repo_root=repo,
        idea_text="Add a webhook listener for stripe events",
        files_in_scope=[Path("src/webhooks/stripe.py")],
    )
    assert "webhook" in keywords
    assert "stripe" in keywords
    assert "requests" in keywords
    assert "react" in keywords


def test_filter_articles_keeps_all_critical() -> None:
    articles = cn.parse_constitution(FIXTURES / "passing.md")
    kept, _dropped = cn.filter_articles(articles, scope_keywords={"unrelated"})
    critical_ids = {a.id for a in kept if a.level == "CRITICAL"}
    expected = {a.id for a in articles if a.level == "CRITICAL"}
    assert critical_ids == expected, "CRITICAL articles must always be kept"


def test_filter_articles_drops_below_token_cap() -> None:
    articles = cn.parse_constitution(FIXTURES / "over_token_cap.md")
    kept, dropped = cn.filter_articles(articles, scope_keywords={"loader"})
    total = sum(a.body_words for a in kept)
    assert total <= cn.MAX_INJECTED_WORDS
    assert dropped, "fixture is sized to force >= 1 drop"


def test_filter_articles_drops_may_below_median() -> None:
    articles = cn.parse_constitution(FIXTURES / "passing.md")
    # Scope keywords match the rule bodies of A1/A2/A3 so three articles
    # score above zero, pushing the median above zero and leaving MAY
    # (A5, "documentation") strictly below it.
    kept, _dropped = cn.filter_articles(
        articles,
        scope_keywords={
            "secrets",
            "vault",
            "credentials",
            "modules",
            "tests",
            "covering",
            "session",
            "repository",
            "orm",
        },
    )
    kept_ids = {a.id for a in kept}
    assert "A5" not in kept_ids, "MAY below median must drop"
    assert "A1" in kept_ids and "A3" in kept_ids, "CRITICAL must survive"


def test_filter_articles_no_critical_path() -> None:
    articles = cn.parse_constitution(FIXTURES / "no_critical.md")
    kept, _dropped = cn.filter_articles(articles, scope_keywords={"anything"})
    assert all(a.level in {"SHOULD", "MAY"} for a in kept)
    assert kept, "filter must not return empty when articles exist"
