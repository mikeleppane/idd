"""Constitution loader, scope-keyword extractor, and minimal relevance filter.

Token budget approximation: this module estimates token counts as
``len(text.split()) * WORD_TO_TOKEN_RATIO`` because the runtime stays
stdlib-only on user machines (no tiktoken). The 1500-token cap from M3
spec D-9 translates to ~1150 words after the multiplier. Revisit in M4
if budget pressure materializes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

WORD_TO_TOKEN_RATIO: float = 1.3
MAX_INJECTED_TOKENS: int = 1500
MAX_INJECTED_WORDS: int = int(MAX_INJECTED_TOKENS / WORD_TO_TOKEN_RATIO)  # 1153

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
_FIELD_RE = re.compile(r"^\*\*(Rule|Reference|Rationale|Exception):\*\*\s*(.*)$")
_FIELD_KEYS = ("rule", "reference", "rationale", "exception")

# `text.split("---\n", 2)` returns 3 elements on properly terminated
# frontmatter (`prefix`, `frontmatter_block`, `body`); fewer when the closing
# delimiter is missing. Naming the boundary keeps the magic-value lint quiet.
_FRONTMATTER_PARTS_REQUIRED = 3

# Loader/validator must agree on what counts as an article header. The
# structural validator (tools.validate._frontmatter._strip_code) blanks
# fenced + inline code regions before scanning so illustrative quotes
# inside ```markdown ... ``` cannot trigger phantom-article findings.
# We mirror the blanking here — keeping byte offsets stable via
# whitespace replacement so any future line-number reporting matches the
# original file.
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_code_regions(text: str) -> str:
    """Replace fenced + inline code spans with same-length whitespace.

    Mirrors ``tools.validate._frontmatter._strip_code`` so the loader sees
    exactly what the validator sees. Whitespace replacement preserves byte
    offsets (line counts unchanged); article headers inside fences are
    therefore invisible to the parser and cannot leak phantom Articles into
    the dispatch payload.
    """
    out = _FENCE_BLOCK_RE.sub(lambda m: " " * len(m.group(0)), text)
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), out)


def parse_constitution_text(text: str) -> list[Article]:
    """Parse an in-memory Constitution body and return Article records.

    Public, file-free counterpart to :func:`parse_constitution`. Both share
    the exact same parser; this entry point exists so callers that already
    hold the body in memory (e.g. ``classify_change`` comparing two text
    snapshots) can avoid the TemporaryDirectory dance.

    Args:
        text: Full Constitution body, frontmatter included.

    Returns:
        List of parsed Article records in declaration order.

    Raises:
        ConstitutionError: When frontmatter cannot be parsed or an article
            header is malformed.
    """
    if not text.startswith("---\n"):
        raise ConstitutionError("Constitution missing frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) < _FRONTMATTER_PARTS_REQUIRED:
        # Unterminated frontmatter: closing `---` never showed up. Wrap the
        # would-be ValueError into the domain error so callers can match on
        # ConstitutionError uniformly.
        raise ConstitutionError("frontmatter missing closing ---")
    _, frontmatter_block, body = parts
    try:
        yaml.safe_load(frontmatter_block)  # parsed for side-effect validation
    except yaml.YAMLError as exc:
        raise ConstitutionError(f"frontmatter parse error: {exc}") from exc

    # Strip fenced + inline code from the body so illustrative `## Article`
    # examples inside code blocks do not produce phantom Articles. Validator
    # parity: tools.validate._frontmatter._strip_code applies the same blanking
    # before its own structural scan.
    scrubbed_body = _strip_code_regions(body)
    articles: list[Article] = []
    state = _ParseState()
    for line in scrubbed_body.splitlines():
        _consume_line(line, state, articles)
    if state.current is not None:
        articles.append(_block_to_article(state.current))
    return articles


def parse_constitution(path: Path) -> list[Article]:
    """Read .forge/CONSTITUTION.md and return parsed Article records.

    Trusts the structural validator (``tools.validate.validate_constitution``)
    to gate frontmatter + numbering + Rule/Exception presence. This parser
    is permissive on already-valid files; it raises ``ConstitutionError`` on
    shape failures the validator would also catch.

    Args:
        path: Path to the Constitution markdown file.

    Returns:
        List of parsed Article records in declaration order.

    Raises:
        ConstitutionError: When the file is missing, frontmatter cannot be
            parsed, or an article header is malformed.
    """
    if not path.exists():
        raise ConstitutionError(f"Constitution not found at {path}")
    return parse_constitution_text(path.read_text(encoding="utf-8"))


@dataclass
class _ParseState:
    """Mutable per-article scratchpad used by ``_consume_line``."""

    current: dict[str, Any] | None = None
    active_field: str | None = None


def _block_to_article(block: dict[str, Any]) -> Article:
    rule = block.get("rule", "").strip()
    reference = block.get("reference", "").strip() or None
    rationale = block.get("rationale", "").strip() or None
    # Score uses rule + reference; body_words mirrors that so the cap
    # denominator and the relevance score share their input. Rationale is
    # carried for surfacing but does not push articles over the cap.
    body_words = len((rule + " " + (reference or "")).split())
    return Article(
        id=f"A{block['number']}",
        title=block["title"],
        level=block["level"],
        rule=rule,
        reference=reference,
        rationale=rationale,
        body_words=body_words,
    )


def _consume_line(line: str, state: _ParseState, articles: list[Article]) -> None:
    """Apply one body line to the running parse state.

    Handles three cases per spec D-9:
        1. Article header — closes the prior article and starts a new one.
        2. Field marker (``**Rule:** ...``) — opens a field; same-line tail
           seeds the value.
        3. Continuation — accumulates onto the active field; blank line
           closes the field so stray paragraphs cannot bleed into Rule.
    """
    if line.startswith("## Article"):
        match = _HEADER_RE.match(line)
        if not match:
            raise ConstitutionError(f"malformed article header: {line!r}")
        if state.current is not None:
            articles.append(_block_to_article(state.current))
        state.current = {
            "number": int(match.group(1)),
            "title": match.group(2).strip(),
            "level": match.group(3),
            "rule": "",
            "reference": "",
            "rationale": "",
            "exception": "",
        }
        state.active_field = None
        return
    if state.current is None:
        return
    field_match = _FIELD_RE.match(line)
    if field_match:
        key = field_match.group(1).lower()
        state.active_field = key
        if key in _FIELD_KEYS:
            state.current[key] = field_match.group(2).strip()
        return
    if state.active_field not in _FIELD_KEYS:
        return
    stripped = line.strip()
    if not stripped:
        state.active_field = None
        return
    existing = state.current[state.active_field]
    state.current[state.active_field] = f"{existing} {stripped}".strip() if existing else stripped


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


def extract_scope_keywords(
    *,
    idea_text: str = "",
    files_in_scope: Iterable[Path] = (),
) -> set[str]:
    """Union scope tokens from idea text and files in scope.

    Read-only. Returns lowercase tokens with stopwords filtered. The
    function performs no filesystem reads — scope signals come from the
    caller's already-resolved ``idea_text`` and ``files_in_scope``.

    Args:
        idea_text: Free-form idea / spec intent text.
        files_in_scope: Paths the caller considers in scope; their string
            forms feed the tokenizer.

    Returns:
        Union set of all derived scope keywords.
    """
    keywords: set[str] = set()
    keywords |= tokenize(idea_text)
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


# M3 D-9 article -> bucket map. CRITICAL is always kept (exempt from both the
# percentile gate and the hard cap); SHOULD gates at the 25th percentile; MAY
# gates at the median. Captured here so the shared scorer + the article scorer
# share one source of truth for the bucket labels.
_ARTICLE_LEVEL_BUCKET: dict[str, Literal["always_kept", "p25_gate", "median_gate"]] = {
    "CRITICAL": "always_kept",
    "SHOULD": "p25_gate",
    "MAY": "median_gate",
}


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

    Delegates the percentile + cap arithmetic to
    :func:`tools.intel._relevance.score_and_trim`; this module owns only the
    article-specific scoring + the M3 D-9 error message wording.

    Args:
        articles: Parsed Constitution articles in declaration order.
        scope_keywords: Pre-tokenized scope keyword set.

    Returns:
        Tuple of (kept_articles, dropped_article_ids). ``kept`` is ordered
        by article numbering (A1 first, A2 next, ...); ``dropped`` is sorted
        the same way.

    Raises:
        ConstitutionError: When CRITICAL articles alone exceed
            ``MAX_INJECTED_WORDS``. The author must trim Rule bodies, demote
            some to SHOULD, or split the articles.
    """
    # Deferred import: ``tools.intel.__init__`` eagerly loads the lessons
    # submodule, which transitively reaches back into ``tools.constitution``
    # via the amend helper. Importing the relevance helper at module top
    # would deadlock the import graph (constitution -> intel.__init__ ->
    # intel.lessons -> constitution_amend -> constitution).
    from tools.intel._relevance import (  # noqa: PLC0415
        RelevanceError,
        RelevanceRule,
        score_and_trim,
    )

    rule: RelevanceRule[Article] = RelevanceRule(
        score=lambda a: score_article(a, scope_keywords),
        level_of=lambda a: a.level,
        body_words_of=lambda a: a.body_words,
        id_of=lambda a: a.id,
        level_bucket=dict(_ARTICLE_LEVEL_BUCKET),
        max_words=MAX_INJECTED_WORDS,
    )
    try:
        return score_and_trim(articles, rule=rule)
    except RelevanceError as exc:
        # Preserve the exact error wording the prior implementation surfaced
        # so error-text-asserting tests keep passing byte-equal.
        # Recompute the critical_ids + total from the parsed articles (the
        # generic helper does not know about Constitution semantics).
        critical_ids = [a.id for a in articles if a.level == "CRITICAL"]
        total = sum(a.body_words for a in articles if a.level == "CRITICAL")
        raise ConstitutionError(
            f"CRITICAL articles {critical_ids} exceed the {MAX_INJECTED_WORDS}-word "
            f"injection budget on their own ({total} words). Trim Rule bodies, "
            f"demote some to SHOULD, or split the articles."
        ) from exc


def load_and_filter(
    repo_root: Path,
    *,
    idea_text: str = "",
    files_in_scope: Iterable[Path] = (),
) -> tuple[list[Article], list[str]]:
    """One-shot: parse + filter against scope signals derived from ``repo_root``.

    Returns ``([], [])`` when ``.forge/CONSTITUTION.md`` is absent.

    Args:
        repo_root: Repository root containing ``.forge/CONSTITUTION.md``.
        idea_text: Free-form idea / spec intent text fed into the scope
            keyword extractor.
        files_in_scope: Paths to include as scope signals.

    Returns:
        Tuple of (kept_articles, dropped_article_ids). Empty pair when the
        Constitution file does not exist.
    """
    constitution_path = repo_root / ".forge" / "CONSTITUTION.md"
    if not constitution_path.exists():
        return [], []
    articles = parse_constitution(constitution_path)
    keywords = extract_scope_keywords(
        idea_text=idea_text,
        files_in_scope=files_in_scope,
    )
    return filter_articles(articles, scope_keywords=keywords)
