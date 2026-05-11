r"""Mode-aware citation validator over RESEARCH.md body markdown.

The research-phase skill produces a RESEARCH.md whose External-docs
section is required to cite every paragraph that mentions an external
API symbol. Which citation form is acceptable depends on the resolved
grounding mode (see spec §5.3.9):

* ``full``       — ``[context7:<library_id>:<snippet_id>]`` is the
                   only authoritative form.
* ``byod``       — ``[byod:<lib>:<section>]`` accepted in lieu of
                   context7 (user-staged docs cover the library).
* ``websearch``  — ``[websearch:<url>]`` accepted in lieu of context7.
* ``degraded``   — citation rule waived; instead, the body MUST contain
                   the explicit unavailable marker line.
* ``byod-partial`` — per-paragraph: ``[byod:lib:...]`` accepted only for
                   libraries enumerated in ``libraries``; for paragraphs
                   that mention an uncovered library we record it in
                   ``byod_partial_uncovered`` and fall through to the
                   degraded rule (require the unavailable marker).

Heuristics
----------

* Paragraphs are split on blank lines (``\\n\\n``).
* A paragraph "contains a code-fenced symbol" if it has at least one
  identifier-shaped backtick token like `` `SomeApi` `` (we require
  the first character to be a letter and the remainder to be
  identifier characters or dots, so ``\\n`` and ``# heading`` are not
  treated as symbols).
* Lines inside fenced ``` blocks are skipped (otherwise the
  detection-table example fragments inside the skill prose would
  trigger false positives).
* HTML comments (``<!-- ... -->``) are stripped before paragraph and
  marker analysis so the degraded-mode fragment shipped inside a
  template comment cannot accidentally satisfy the marker check —
  the subagent must actually replace the External docs section.
* The "_Context7 not available_" substring match is case-insensitive.
"""

import re
from dataclasses import dataclass, field

from tools.research.library_extract import normalize

_SYMBOL_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_.]*)`")
_CONTEXT7_RE = re.compile(r"\[context7:[^\]]+\]")
_BYOD_RE = re.compile(r"\[byod:([^:\]]+):[^\]]+\]")
_WEBSEARCH_RE = re.compile(r"\[websearch:[^\]]+\]")
_FENCE_RE = re.compile(r"^```")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DEGRADED_MARKER = "context7 not available"


@dataclass(frozen=True)
class CitationResult:
    """Outcome of a single citation validation pass."""

    missing_citations: list[str] = field(default_factory=list)
    degraded_marker_present: bool = False
    byod_partial_uncovered: list[str] = field(default_factory=list)


def _strip_html_comments(body: str) -> str:
    """Remove ``<!-- ... -->`` blocks (including multi-line spans).

    The RESEARCH.md template ships a degraded-mode fragment inside an HTML
    comment so authors can copy/paste it into the visible body. A naive
    marker check on the raw body would treat the commented fragment as
    satisfying the rule and let an unmodified template pass. Stripping
    comments first forces the subagent to actually replace the
    ``External docs`` section before the marker counts.
    """
    return _HTML_COMMENT_RE.sub("", body)


def _strip_fenced_blocks(body: str) -> str:
    out_lines: list[str] = []
    inside = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            out_lines.append(line)
    return "\n".join(out_lines)


def _paragraphs(body: str) -> list[str]:
    return [p for p in body.split("\n\n") if p.strip()]


def _symbols_in(paragraph: str) -> list[str]:
    return _SYMBOL_RE.findall(paragraph)


def _has_context7(paragraph: str) -> bool:
    return bool(_CONTEXT7_RE.search(paragraph))


def _has_byod_for(paragraph: str, covered: set[str]) -> bool:
    return any(normalize(match.group(1)) in covered for match in _BYOD_RE.finditer(paragraph))


def _byod_libs_in(paragraph: str) -> list[str]:
    return [normalize(m.group(1)) for m in _BYOD_RE.finditer(paragraph)]


def _has_websearch(paragraph: str) -> bool:
    return bool(_WEBSEARCH_RE.search(paragraph))


def _candidate_libs(paragraph: str) -> list[str]:
    """Return canonical library candidates referenced by paragraph symbols.

    A backtick symbol like `` `pydantic.BaseModel` `` yields the
    canonical head ``pydantic`` as a candidate library name.
    """
    candidates: list[str] = []
    for symbol in _symbols_in(paragraph):
        head = normalize(symbol.split(".", 1)[0])
        if head and head not in candidates:
            candidates.append(head)
    return candidates


def _snippet(paragraph: str, max_chars: int = 80) -> str:
    flattened = " ".join(paragraph.split())
    if len(flattened) <= max_chars:
        return flattened
    return flattened[: max_chars - 1] + "…"


def _paragraph_satisfied(paragraph: str, mode: str, covered: set[str]) -> bool:
    """Return True iff ``paragraph`` carries an acceptable citation for ``mode``."""
    if _has_context7(paragraph):
        return True
    if mode == "byod":
        return bool(_BYOD_RE.search(paragraph))
    if mode == "websearch":
        return _has_websearch(paragraph)
    if mode == "byod-partial":
        return _has_byod_for(paragraph, covered)
    return False


def validate(
    body: str,
    *,
    mode: str,
    libraries: tuple[str, ...] = (),
) -> CitationResult:
    """Validate citations in ``body`` against the rules for ``mode``.

    See module docstring for per-mode semantics. The function never
    raises; unrecognised modes degrade to "no findings" (the higher-level
    validator owns mode-vocabulary enforcement).
    """
    cleaned = _strip_fenced_blocks(_strip_html_comments(body))
    marker_present = _DEGRADED_MARKER in cleaned.lower()

    if mode == "degraded":
        return CitationResult(
            missing_citations=[],
            degraded_marker_present=marker_present,
            byod_partial_uncovered=[],
        )

    covered = {normalize(lib) for lib in libraries}
    missing: list[str] = []
    uncovered: list[str] = []

    for paragraph in _paragraphs(cleaned):
        if not _symbols_in(paragraph):
            continue
        if _paragraph_satisfied(paragraph, mode, covered):
            continue
        if mode == "byod-partial":
            for lib in _candidate_libs(paragraph) + _byod_libs_in(paragraph):
                if lib and lib not in covered and lib not in uncovered:
                    uncovered.append(lib)
            if marker_present:
                continue
        missing.append(_snippet(paragraph))

    return CitationResult(
        missing_citations=missing,
        degraded_marker_present=marker_present,
        byod_partial_uncovered=uncovered,
    )
