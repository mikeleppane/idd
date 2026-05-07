"""Constitution loader, scope-keyword extractor, and minimal relevance filter.

Token budget approximation: this module estimates token counts as
``len(text.split()) * WORD_TO_TOKEN_RATIO`` because the runtime stays
stdlib-only on user machines (no tiktoken). The 1500-token cap from M3
spec D-9 translates to ~1150 words after the multiplier. Revisit in M4
if budget pressure materializes.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

WORD_TO_TOKEN_RATIO: float = 1.3
MAX_INJECTED_TOKENS: int = 1500
MAX_INJECTED_WORDS: int = int(MAX_INJECTED_TOKENS / WORD_TO_TOKEN_RATIO)  # 1153
# Per-article cap (Open Scoping #16): single article body must fit two
# CRITICAL articles inside MAX_INJECTED_TOKENS. 600 words ~= 780 tokens; two
# CRITICAL articles ~= 1560 tokens, with the JSON envelope absorbing slack.
MAX_ARTICLE_BODY_WORDS: int = 600

Level = Literal["CRITICAL", "SHOULD", "MAY"]


class ConstitutionError(RuntimeError):
    """Raised when the Constitution cannot be parsed or filtered."""


@dataclass(frozen=True, kw_only=True)
class Article:
    """One Constitution article with parsed body and loader-internal metadata."""

    id: str  # "A1", "A2", ...
    title: str
    level: Level
    rule: str
    reference: str | None
    rationale: str | None
    body_words: int  # loader-internal; not part of the dispatch contract

    def to_budget_dict(self) -> dict[str, Any]:
        """Return the locked JSON shape consumed by the dispatch budget.

        ``body_words`` is loader-internal; it never leaks into subagent prompts.
        Tests assert ``json.dumps`` round-trip matches Open Scoping #9 exactly.

        Returns:
            Dict with the locked keys (id, title, level, rule, reference, rationale).
        """
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "rule": self.rule,
            "reference": self.reference,
            "rationale": self.rationale,
        }


_HEADER_RE = re.compile(r"^## Article (\d+) — (.+) \[(CRITICAL|SHOULD|MAY)\]\s*$")
_FIELD_RE = re.compile(r"^\*\*(Rule|Reference|Rationale|Exception):\*\*\s*(.+)$")


def parse_constitution(path: Path) -> list[Article]:
    """Read .idd/CONSTITUTION.md and return parsed Article records.

    Trusts the structural validator (``tools.validate.validate_constitution``)
    to gate frontmatter + numbering + Rule/Exception presence. This parser
    is permissive on already-valid files; it raises ``ConstitutionError`` on
    shape failures the validator would also catch AND on per-article body
    word-count overflow (Open Scoping #16 — guarantees the 1500-token cap
    is not bypassed by a single fat CRITICAL article).

    Args:
        path: Path to the Constitution markdown file.

    Returns:
        List of parsed Article records in declaration order.

    Raises:
        ConstitutionError: When the file is missing, frontmatter cannot be
            parsed, an article header is malformed, or any article body
            exceeds ``MAX_ARTICLE_BODY_WORDS``.
    """
    if not path.exists():
        raise ConstitutionError(f"Constitution not found at {path}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ConstitutionError("Constitution missing frontmatter")
    _, frontmatter_block, body = text.split("---\n", 2)
    try:
        yaml.safe_load(frontmatter_block)  # parsed for side-effect validation
    except yaml.YAMLError as exc:
        raise ConstitutionError(f"frontmatter parse error: {exc}") from exc

    articles: list[Article] = []
    current: dict[str, Any] | None = None

    def _commit(block: dict[str, Any]) -> None:
        rule = block.get("rule", "").strip()
        reference = block.get("reference", "").strip() or None
        rationale = block.get("rationale", "").strip() or None
        body_words = len((rule + " " + (reference or "") + " " + (rationale or "")).split())
        if body_words > MAX_ARTICLE_BODY_WORDS:
            raise ConstitutionError(
                f"article A{block['number']} body is {body_words} words; "
                f"per-article cap is {MAX_ARTICLE_BODY_WORDS} words "
                f"(~{int(MAX_ARTICLE_BODY_WORDS * WORD_TO_TOKEN_RATIO)} tokens). "
                "Rewrite shorter or split into two articles."
            )
        articles.append(
            Article(
                id=f"A{block['number']}",
                title=block["title"],
                level=block["level"],
                rule=rule,
                reference=reference,
                rationale=rationale,
                body_words=body_words,
            )
        )

    for line in body.splitlines():
        if line.startswith("## Article"):
            match = _HEADER_RE.match(line)
            if not match:
                raise ConstitutionError(f"malformed article header: {line!r}")
            if current is not None:
                _commit(current)
            current = {
                "number": int(match.group(1)),
                "title": match.group(2).strip(),
                "level": match.group(3),
                "rule": "",
                "reference": "",
                "rationale": "",
            }
            continue
        if current is None:
            continue
        field_match = _FIELD_RE.match(line)
        if field_match:
            key = field_match.group(1).lower()
            if key in {"rule", "reference", "rationale"}:
                current[key] = field_match.group(2).strip()

    if current is not None:
        _commit(current)
    return articles


STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "with",
        "for",
        "from",
        "by",
        "in",
        "on",
        "at",
        "as",
        "is",
        "be",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "are",
        "was",
        "were",
        "via",
        "use",
        "uses",
        "used",
        "must",
        "shall",
        "should",
        "may",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "yes",
        "they",
        "their",
        "them",
        "we",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "i",
        "me",
        "my",
        "mine",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9_-]+")
_MIN_TOKEN_LENGTH: int = 3


def tokenize(text: str) -> set[str]:
    """Lowercase, drop stopwords + short tokens, return unique set.

    Args:
        text: Free-form text to tokenize.

    Returns:
        Set of lowercased tokens, stopwords filtered, length >= 3.
    """
    raw = _TOKEN_RE.findall(text.lower())
    return {t for t in raw if len(t) >= _MIN_TOKEN_LENGTH and t not in STOPWORDS}


def read_pyproject_top_level_deps(path: Path) -> list[str]:
    r"""Return PEP 621 ``[project.dependencies]`` entries as raw strings.

    Public so ``tools.constitution_amend`` can reuse it without duplicating
    the parsing logic. Returns full dep strings (e.g. ``"requests>=2.0"``); a
    caller that wants bare names splits on ``re.split(r"[<>=!\[ ]", ...)``.

    Args:
        path: Path to ``pyproject.toml``. Missing file returns ``[]``.

    Returns:
        List of dependency strings, or ``[]`` if the file is missing or
        malformed.
    """
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    project = data.get("project", {})
    deps = project.get("dependencies", [])
    return list(deps) if isinstance(deps, list) else []


def read_package_json_top_level_deps(path: Path) -> list[str]:
    """Return ``package.json`` dependency keys (deps + devDeps).

    Public so ``tools.constitution_amend`` can reuse it.

    Args:
        path: Path to ``package.json``. Missing file returns ``[]``.

    Returns:
        List of dependency names; empty list when file missing or malformed.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[str] = []
    for section in ("dependencies", "devDependencies"):
        out.extend((data.get(section) or {}).keys())
    return out


def _bare_dep_name(entry: str) -> str:
    """``'requests>=2.0'`` -> ``'requests'``. Strips version specifier + extras."""
    return re.split(r"[<>=!\[ ]", entry, maxsplit=1)[0]


def extract_scope_keywords(
    *,
    repo_root: Path,
    idea_text: str = "",
    files_in_scope: Iterable[Path] = (),
) -> set[str]:
    """Union scope tokens from idea text + project deps + files in scope.

    Read-only. Returns lowercase tokens with stopwords filtered.

    Args:
        repo_root: Repository root used to locate ``pyproject.toml`` /
            ``package.json``.
        idea_text: Free-form idea / spec intent text.
        files_in_scope: Paths the caller considers in scope; their string
            forms feed the tokenizer.

    Returns:
        Union set of all derived scope keywords.
    """
    keywords: set[str] = set()
    keywords |= tokenize(idea_text)

    for dep in read_pyproject_top_level_deps(repo_root / "pyproject.toml"):
        keywords |= tokenize(_bare_dep_name(dep))
    for dep in read_package_json_top_level_deps(repo_root / "package.json"):
        keywords |= tokenize(dep)

    for path in files_in_scope:
        keywords |= tokenize(str(path))
    return keywords


def score_article(article: Article, scope_keywords: set[str]) -> int:
    """Count article tokens (rule + reference) that overlap ``scope_keywords``.

    Args:
        article: Article to score.
        scope_keywords: Pre-tokenized scope keyword set.

    Returns:
        Overlap count (>= 0).
    """
    body = article.rule + " " + (article.reference or "")
    return len(tokenize(body) & scope_keywords)


def _percentile(values: list[int], pct: float) -> float:
    """Inclusive linear-interpolation percentile. Returns 0.0 for empty input."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def filter_articles(
    articles: list[Article],
    *,
    scope_keywords: set[str],
) -> tuple[list[Article], list[str]]:
    """Apply the M3 D-9 minimal relevance filter.

    Rules (in order):
        1. CRITICAL articles are always kept (regardless of score).
        2. MAY articles below the median score are dropped.
        3. SHOULD articles below the 25th percentile score are dropped.
        4. Hard cap: cumulative kept body word count <= ``MAX_INJECTED_WORDS``;
           drop further by ascending score until under cap.

    Args:
        articles: Parsed Constitution articles in declaration order.
        scope_keywords: Pre-tokenized scope keyword set.

    Returns:
        Tuple of (kept_articles, dropped_article_ids). ``kept`` is ordered
        by article numbering (A1 first, A2 next, ...); ``dropped`` is sorted
        the same way.
    """
    if not articles:
        return [], []
    scored = [(a, score_article(a, scope_keywords)) for a in articles]
    scores = [s for _, s in scored]
    median = _percentile(scores, 50)
    p25 = _percentile(scores, 25)

    kept: list[Article] = []
    dropped: list[str] = []
    for article, score in scored:
        if article.level == "CRITICAL":
            kept.append(article)
        elif (article.level == "MAY" and score < median) or (
            article.level == "SHOULD" and score < p25
        ):
            dropped.append(article.id)
        else:
            kept.append(article)

    # Hard cap pass: drop in ascending score order, but never drop CRITICAL.
    cumulative = sum(a.body_words for a in kept)
    if cumulative > MAX_INJECTED_WORDS:
        kept_with_score = sorted(
            ((a, score_article(a, scope_keywords)) for a in kept),
            key=lambda pair: (pair[0].level == "CRITICAL", pair[1]),
        )
        kept_after_cap: list[Article] = []
        running = 0
        # Iterate descending so highest-priority articles are added first.
        for article, _score in reversed(kept_with_score):
            if running + article.body_words <= MAX_INJECTED_WORDS:
                kept_after_cap.append(article)
                running += article.body_words
            elif article.level == "CRITICAL":
                kept_after_cap.append(article)  # CRITICAL exempt from cap
                running += article.body_words
            else:
                dropped.append(article.id)
        kept = sorted(kept_after_cap, key=lambda a: int(a.id[1:]))
    return kept, sorted(set(dropped), key=lambda x: int(x[1:]))


def load_and_filter(
    repo_root: Path,
    *,
    idea_text: str = "",
    files_in_scope: Iterable[Path] = (),
) -> tuple[list[Article], list[str]]:
    """One-shot: parse + filter against scope signals derived from ``repo_root``.

    Returns ``([], [])`` when ``.idd/CONSTITUTION.md`` is absent.

    Args:
        repo_root: Repository root containing ``.idd/CONSTITUTION.md``.
        idea_text: Free-form idea / spec intent text fed into the scope
            keyword extractor.
        files_in_scope: Paths to include as scope signals.

    Returns:
        Tuple of (kept_articles, dropped_article_ids). Empty pair when the
        Constitution file does not exist.
    """
    constitution_path = repo_root / ".idd" / "CONSTITUTION.md"
    if not constitution_path.exists():
        return [], []
    articles = parse_constitution(constitution_path)
    keywords = extract_scope_keywords(
        repo_root=repo_root,
        idea_text=idea_text,
        files_in_scope=files_in_scope,
    )
    return filter_articles(articles, scope_keywords=keywords)
