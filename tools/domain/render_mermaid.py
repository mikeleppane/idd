"""Deterministic bounded-context Mermaid renderer for DOMAIN.md.

The ``forge-domain`` skill calls :func:`render_from_domain_md` after the
glossary is populated and splices the returned block into the
``# Bounded Contexts`` section of the in-memory DOMAIN.md text. Output is a
pure function of input — re-running the domain phase against an unchanged
glossary returns a byte-identical block.

Two callable surfaces:

- :func:`render_bounded_context_mermaid` — works on a parsed
  :class:`GlossaryRow` list. Useful when DOMAIN.md is already in memory in
  structured form.
- :func:`render_from_domain_md` — thin wrapper that parses the
  ``# Glossary`` table out of raw DOMAIN.md text (fence-aware) and renders.
  This is what the skill invokes.

Rendering rules:

1. **Nodes** — one node per UNIQUE non-``None`` ``context_id`` across all
   rows. Node id is ``ctx_<sanitized>`` where every non-alphanumeric
   character collapses to ``_``. Node label preserves the original
   ``context_id`` value.
2. **Edges** — one edge per UNIQUE pair ``(a, b)`` where some row anchored
   in ``a`` carries a ``[term](context: b)`` cross-reference and ``b != a``.
   Edges render as ``-->`` for layout but are deduplicated as undirected
   pairs in alphabetical leader order.
3. **Empty case** — when no row has a ``context_id``, emit a placeholder
   node (``ctx_placeholder[no contexts annotated]``) so downstream Mermaid
   renderers do not choke on an empty graph.
4. **Idempotent** — node lines are sorted by sanitized id; edge lines are
   sorted by ``(left_id, right_id)``.

The internal :func:`_extract_rows` parser intentionally duplicates a small
portion of ``tools.validate.domain_glossary`` (table-row split + Term-cell
context annotation parsing). When the validator grows a public helper for
the same shape, this module should switch to it; the duplication is
flagged here so the next maintainer notices.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from tools.validate._frontmatter import _strip_code

_PLACEHOLDER_NODE = "ctx_placeholder[no contexts annotated]"
_HEADER = "```mermaid\n%% auto-generated; do not edit\ngraph LR\n"
_FOOTER = "```"
_HASH_SUFFIX_LEN = 6

_GLOSSARY_BLOCK = re.compile(r"(?ms)^# Glossary\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*[:\-]+\s*(?:\|\s*[:\-]+\s*)+\|?\s*$")
_CONTEXT_ANNOTATION = re.compile(r"^\[(?P<term>[^\]]+)\]\(context:\s*(?P<ctx>[^)]+)\)$")
_INLINE_CTX_REF = re.compile(r"\[[^\]]+\]\(context:\s*(?P<ctx>[^)]+)\)")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class GlossaryRow:
    """A single parsed row from the DOMAIN.md ``# Glossary`` table.

    Attributes:
        term: The glossary term (the cell value with any
            ``[term](context: ctx)`` annotation peeled off).
        context_id: The context this term anchors to, or ``None`` when the
            row sits in the ``—`` / context-free bucket.
        cross_refs: Other context ids this row references via inline
            ``[other](context: <id>)`` annotations in cells beyond the Term
            cell. Self-references (matching ``context_id``) are filtered
            out by the parser; render-time logic skips any that slip
            through to keep the renderer honest.
    """

    term: str
    context_id: str | None
    cross_refs: list[str] = field(default_factory=list)


def _sanitize(context_id: str) -> str:
    """Return ``ctx_<safe>[_<hash6>]`` — collision-proof Mermaid node id.

    Mermaid node ids must be alphanumeric + underscore. Naively replacing
    every non-alnum run with ``_`` collapses distinct context-ids that
    differ only in punctuation (``sales-orders`` vs ``sales_orders`` →
    both ``ctx_sales_orders``). To preserve "one node per unique
    context_id", append a short stable hash suffix whenever the raw
    context-id contained any non-alphanumeric character. Pure-alnum ids
    (the common case) keep their plain shape so the rendered diagram
    stays readable.
    """
    safe = _NON_ALNUM.sub("_", context_id).strip("_") or "ctx"
    if _NON_ALNUM.search(context_id):
        # blake2s used as a non-cryptographic hash for stable disambiguation
        # of distinct context-ids that sanitize to the same alnum form.
        suffix = hashlib.blake2s(
            context_id.encode("utf-8"),
            digest_size=_HASH_SUFFIX_LEN // 2,
        ).hexdigest()
        return f"ctx_{safe}_{suffix}"
    return f"ctx_{safe}"


def _collect_nodes(rows: list[GlossaryRow]) -> list[str]:
    seen: dict[str, str] = {}
    for row in rows:
        if row.context_id is None:
            continue
        node_id = _sanitize(row.context_id)
        # First occurrence wins for the label; sorted output makes order stable.
        # The sanitize hash suffix prevents distinct context-ids from collapsing
        # onto the same node_id key.
        seen.setdefault(node_id, row.context_id)
    return [f"  {node_id}[{label}]" for node_id, label in sorted(seen.items())]


def _collect_edges(rows: list[GlossaryRow]) -> list[str]:
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if row.context_id is None:
            continue
        anchor = row.context_id
        for ref in row.cross_refs:
            if not ref or ref == anchor:
                continue
            left, right = sorted((anchor, ref))
            pairs.add((left, right))
    return [f"  {_sanitize(left)} --> {_sanitize(right)}" for left, right in sorted(pairs)]


def render_bounded_context_mermaid(glossary_rows: list[GlossaryRow]) -> str:
    """Render the ``# Bounded Contexts`` Mermaid block from parsed rows.

    Args:
        glossary_rows: Parsed glossary rows. Order does not matter; output
            is sorted.

    Returns:
        A fenced Mermaid block as a single string. Always begins with the
        literal three-backtick ``mermaid`` opener and ends with a matching
        three-backtick fence closer.
    """
    node_lines = _collect_nodes(glossary_rows)
    if not node_lines:
        return f"{_HEADER}  {_PLACEHOLDER_NODE}\n{_FOOTER}"
    edge_lines = _collect_edges(glossary_rows)
    body = "\n".join(node_lines + edge_lines)
    return f"{_HEADER}{body}\n{_FOOTER}"


def _split_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped.strip("|")
    return [cell.strip() for cell in inner.split("|")]


def _parse_term_cell(cell: str) -> tuple[str, str | None]:
    match = _CONTEXT_ANNOTATION.match(cell.strip())
    if match is None:
        return cell.strip(), None
    return match.group("term").strip(), match.group("ctx").strip()


def _extract_rows(domain_md_text: str) -> list[GlossaryRow]:
    """Parse the ``# Glossary`` table into :class:`GlossaryRow` instances.

    Fence-aware: fenced code blocks in the DOMAIN.md body are masked out
    before the glossary block is located, so example tables inside fences
    cannot inject phantom rows.
    """
    masked = _strip_code(domain_md_text)
    block = _GLOSSARY_BLOCK.search(masked)
    if block is None:
        return []
    rows: list[GlossaryRow] = []
    for line in block.group("body").splitlines():
        if not line.strip().startswith("|"):
            continue
        if _TABLE_SEPARATOR.match(line):
            continue
        cells = _split_row(line)
        if cells is None:
            continue
        if cells and cells[0].lower() == "term":
            continue
        if not cells or not cells[0]:
            continue
        term, anchor = _parse_term_cell(cells[0])
        if not term:
            continue
        cross: list[str] = []
        for cell in cells[1:]:
            for match in _INLINE_CTX_REF.finditer(cell):
                ctx = match.group("ctx").strip()
                if ctx and ctx != anchor and ctx not in cross:
                    cross.append(ctx)
        rows.append(GlossaryRow(term=term, context_id=anchor, cross_refs=cross))
    return rows


def render_from_domain_md(domain_md_text: str) -> str:
    """Parse DOMAIN.md raw text and render the bounded-context Mermaid block.

    Args:
        domain_md_text: Raw DOMAIN.md contents (frontmatter + sections).

    Returns:
        The rendered Mermaid block — same shape as
        :func:`render_bounded_context_mermaid`. The skill splices this
        block into the ``# Bounded Contexts`` section, replacing the
        placeholder byte-for-byte.
    """
    rows = _extract_rows(domain_md_text)
    return render_bounded_context_mermaid(rows)


__all__ = [
    "GlossaryRow",
    "render_bounded_context_mermaid",
    "render_from_domain_md",
]
