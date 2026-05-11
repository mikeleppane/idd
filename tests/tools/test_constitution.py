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


def test_extract_scope_keywords_unions_idea_and_files(tmp_path: Path) -> None:
    repo = tmp_path / "scope_repo"
    repo.mkdir()
    keywords = cn.extract_scope_keywords(
        repo_root=repo,
        idea_text="Add a webhook listener for stripe events",
        files_in_scope=[Path("src/webhooks/stripe.py")],
    )
    assert "webhook" in keywords
    assert "stripe" in keywords
    assert "listener" in keywords
    assert "events" in keywords


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


def test_article_to_budget_dict_returns_locked_shape() -> None:
    """Open Scoping #9 contract: dispatch budget JSON shape."""
    articles = cn.parse_constitution(FIXTURES / "passing.md")
    a1 = articles[0]
    payload = a1.to_budget_dict()
    assert set(payload.keys()) == {"id", "title", "level", "rule", "reference", "rationale"}
    assert payload["id"] == "A1"
    assert payload["level"] == "CRITICAL"
    assert payload["title"] == a1.title
    assert payload["rule"] == a1.rule
    # body_words MUST NOT leak into the dispatch payload.
    assert "body_words" not in payload


def test_filter_articles_raises_when_critical_alone_exceeds_cap(tmp_path: Path) -> None:
    """CRITICAL articles are exempt from the percentile/cap filters, but the
    1500-token injection budget is the contract D-9 promises. A Constitution
    whose CRITICAL articles alone exceed the cap MUST surface a hard error
    rather than silently inject an over-budget articles[]."""
    bloat_word = " word"  # leading space → split() yields one token per occurrence
    big_rule = "Always vault" + bloat_word * 1300
    text = (
        '---\nversion: 0.1.0\ncreated: "2026-05-07"\n---\n\n'
        "# Constitution\n\n"
        "## Article 1 — Big critical [CRITICAL]\n"
        f"**Rule:** {big_rule}\n"
        "**Reference:** ref\n"
        "**Rationale:** rationale\n"
        "**Exception:** None.\n"
    )
    bad = tmp_path / "over_critical.md"
    bad.write_text(text, encoding="utf-8")
    articles = cn.parse_constitution(bad)
    assert articles[0].body_words > cn.MAX_INJECTED_WORDS, "fixture must over-shoot the cap"
    with pytest.raises(cn.ConstitutionError, match=r"CRITICAL articles .* exceed"):
        cn.filter_articles(articles, scope_keywords={"vault"})


def test_parse_constitution_concatenates_multi_line_rule_body(tmp_path: Path) -> None:
    """A Rule field that wraps onto continuation lines must round-trip into a
    single rule string so scoring and body_words capture the entire rule."""
    text = (
        '---\nversion: 0.1.0\ncreated: "2026-05-07"\n---\n\n'
        "## Article 1 — Multi-line rule [SHOULD]\n"
        "**Rule:** First line of the rule.\n"
        "Second line continues the same rule with more detail.\n"
        "Third line keeps going.\n"
        "**Reference:** ref-x\n"
        "**Rationale:** rationale-y\n"
        "**Exception:** None.\n"
    )
    src = tmp_path / "multiline.md"
    src.write_text(text, encoding="utf-8")
    article = cn.parse_constitution(src)[0]
    assert "First line" in article.rule
    assert "Second line continues" in article.rule
    assert "Third line keeps going" in article.rule
    assert article.reference == "ref-x"
    assert article.rationale == "rationale-y"


def test_parse_constitution_ignores_article_headers_inside_fenced_code(tmp_path: Path) -> None:
    """H3 — `## Article N` inside a fenced or inline code block must NOT be parsed.

    Pre-fix loader scanned the raw body and emitted phantom Article rows for
    illustrative quotations inside a fenced code block, while the structural
    validator (which strips fences before its own scan) never saw them.
    Loader and validator must agree on what counts as an article header.
    """
    text = (
        '---\nversion: 0.1.0\ncreated: "2026-05-07"\n---\n\n'
        "## Article 1 — Real article [SHOULD]\n"
        "**Rule:** Be explicit.\n"
        "**Reference:** ref\n"
        "**Rationale:** because.\n"
        "**Exception:** None.\n"
        "\n"
        "Below is an example template the team should follow:\n"
        "\n"
        "```markdown\n"
        "## Article 99 — Phantom critical [CRITICAL]\n"
        "**Rule:** Never trigger this from inside a fence.\n"
        "**Reference:** —\n"
        "**Rationale:** —\n"
        "**Exception:** None.\n"
        "```\n"
        "\n"
        "Inline `## Article 98 — Inline phantom [CRITICAL]` should also be ignored.\n"
    )
    src = tmp_path / "with_fenced_phantom.md"
    src.write_text(text, encoding="utf-8")
    articles = cn.parse_constitution(src)
    ids = [a.id for a in articles]
    assert ids == ["A1"], f"only the real article should be parsed, got {ids}"


def test_parse_constitution_raises_on_unterminated_frontmatter(tmp_path: Path) -> None:
    """M1 — malformed (unterminated) frontmatter must surface ConstitutionError.

    Pre-fix `_, fm, body = text.split('---\\n', 2)` raised a raw ValueError
    when the closing `---` was missing, leaking a non-domain exception into
    the preflight loader.
    """
    bad = tmp_path / "no_close.md"
    bad.write_text("---\nversion: 0.1.0\n\n## Article 1 — Demo [SHOULD]\n", encoding="utf-8")
    with pytest.raises(cn.ConstitutionError, match="frontmatter"):
        cn.parse_constitution(bad)


def test_parse_constitution_text_round_trips_in_memory_body() -> None:
    """`parse_constitution_text` parses an in-memory Constitution body without
    writing anything to disk, sharing the exact loader contract with
    `parse_constitution(path)`. Public surface so callers (e.g.
    `tools.constitution_amend.classify_change`) can avoid TemporaryDirectory.
    """
    text = (FIXTURES / "passing.md").read_text(encoding="utf-8")
    via_text = cn.parse_constitution_text(text)
    via_path = cn.parse_constitution(FIXTURES / "passing.md")
    assert [a.id for a in via_text] == [a.id for a in via_path]
    assert via_text[0].title == via_path[0].title
    assert via_text[0].level == via_path[0].level
    assert via_text[0].rule == via_path[0].rule


def test_parse_constitution_text_raises_on_missing_frontmatter() -> None:
    """In-memory parser must reject inputs the file parser would also reject."""
    with pytest.raises(cn.ConstitutionError, match="frontmatter"):
        cn.parse_constitution_text("## Article 1 — No frontmatter [SHOULD]\n")


def test_article_to_budget_dict_preserves_null_optionals() -> None:
    """Articles without Reference/Rationale serialize them as None, not omitted."""
    text = (
        '---\nversion: 0.1.0\ncreated: "2026-05-07"\n---\n\n'
        "## Article 1 — Bare rule [SHOULD]\n"
        "**Rule:** Be explicit.\n"
        "**Exception:** None.\n"
    )
    bare = FIXTURES / "_tmp_bare.md"
    bare.write_text(text, encoding="utf-8")
    try:
        articles = cn.parse_constitution(bare)
    finally:
        bare.unlink()
    payload = articles[0].to_budget_dict()
    assert payload["reference"] is None
    assert payload["rationale"] is None
