"""Tests for ``tools.domain.render_mermaid``.

The renderer is a deterministic, pure function: glossary rows in, Mermaid
block out. No filesystem, no subprocess. Two callable surfaces:

- ``render_bounded_context_mermaid(rows)`` — works on a parsed
  ``GlossaryRow`` list. Used by tests and by callers that already parsed
  DOMAIN.md.
- ``render_from_domain_md(domain_md_text)`` — a thin wrapper that parses
  the ``# Glossary`` table out of raw DOMAIN.md text and renders. This is
  what the ``forge-domain`` skill invokes.

These tests pin the exact output so any whitespace drift fails fast.
"""

from __future__ import annotations

import re

from tools.domain.render_mermaid import (
    GlossaryRow,
    render_bounded_context_mermaid,
    render_from_domain_md,
)


def _placeholder_block() -> str:
    return (
        "```mermaid\n"
        "%% auto-generated; do not edit\n"
        "graph LR\n"
        "  ctx_placeholder[no contexts annotated]\n"
        "```"
    )


def test_render_mermaid_no_contexts_emits_placeholder() -> None:
    """Empty glossary or all-None contexts collapses to the placeholder block."""
    assert render_bounded_context_mermaid([]) == _placeholder_block()
    rows = [
        GlossaryRow(term="Order", context_id=None, cross_refs=[]),
        GlossaryRow(term="Cart", context_id=None, cross_refs=[]),
    ]
    assert render_bounded_context_mermaid(rows) == _placeholder_block()


def test_render_mermaid_single_context_no_edges() -> None:
    """Three rows, single anchored context, no cross-refs → one node, zero edges."""
    rows = [
        GlossaryRow(term="Invoice", context_id="billing", cross_refs=[]),
        GlossaryRow(term="LineItem", context_id="billing", cross_refs=[]),
        GlossaryRow(term="TaxCode", context_id="billing", cross_refs=[]),
    ]
    expected = "```mermaid\n%% auto-generated; do not edit\ngraph LR\n  ctx_billing[billing]\n```"
    assert render_bounded_context_mermaid(rows) == expected


def test_render_mermaid_multi_context_with_cross_ref() -> None:
    """Cross-context annotation produces a single edge between two nodes."""
    rows = [
        GlossaryRow(term="Invoice", context_id="billing", cross_refs=["shipping"]),
        GlossaryRow(term="Address", context_id="shipping", cross_refs=[]),
    ]
    expected = (
        "```mermaid\n"
        "%% auto-generated; do not edit\n"
        "graph LR\n"
        "  ctx_billing[billing]\n"
        "  ctx_shipping[shipping]\n"
        "  ctx_billing --> ctx_shipping\n"
        "```"
    )
    assert render_bounded_context_mermaid(rows) == expected


def test_render_mermaid_idempotent() -> None:
    """Running the renderer twice on the same input is byte-identical."""
    rows = [
        GlossaryRow(term="Invoice", context_id="billing", cross_refs=["shipping"]),
        GlossaryRow(term="Address", context_id="shipping", cross_refs=["billing"]),
        GlossaryRow(term="Order", context_id="sales", cross_refs=["billing"]),
    ]
    first = render_bounded_context_mermaid(rows)
    second = render_bounded_context_mermaid(rows)
    assert first == second


def test_render_mermaid_edge_dedup_alphabetical() -> None:
    """Reciprocal cross-refs dedupe to a single alphabetically-ordered edge."""
    rows = [
        GlossaryRow(term="Invoice", context_id="billing", cross_refs=["shipping"]),
        GlossaryRow(term="Address", context_id="shipping", cross_refs=["billing"]),
    ]
    rendered = render_bounded_context_mermaid(rows)
    assert rendered.count("-->") == 1
    assert "ctx_billing --> ctx_shipping" in rendered
    assert "ctx_shipping --> ctx_billing" not in rendered


def test_render_mermaid_sanitizes_node_ids() -> None:
    """Non-alphanumeric chars in context ids become underscores plus a hash suffix.

    The suffix prevents distinct context-ids that differ only in punctuation
    (``sales-orders`` vs ``sales_orders``) from collapsing onto the same
    Mermaid node id. The label preserves the original spelling.
    """
    rows = [
        GlossaryRow(term="Manifest", context_id="multi-word ctx", cross_refs=[]),
    ]
    rendered = render_bounded_context_mermaid(rows)
    # Node id starts with sanitized base + underscore + 6-char hex suffix.
    assert re.search(
        r"^  ctx_multi_word_ctx_[0-9a-f]{6}\[multi-word ctx\]$",
        rendered,
        re.MULTILINE,
    ), rendered


def test_render_mermaid_no_collision_on_punctuation_difference() -> None:
    """``sales-orders`` and ``sales_orders`` MUST render as two distinct nodes."""
    rows = [
        GlossaryRow(term="OrderA", context_id="sales-orders", cross_refs=[]),
        GlossaryRow(term="OrderB", context_id="sales_orders", cross_refs=[]),
    ]
    rendered = render_bounded_context_mermaid(rows)
    # Two distinct node lines; both labels preserved verbatim.
    node_lines = [line for line in rendered.splitlines() if line.startswith("  ctx_")]
    assert len(node_lines) == 2, rendered
    assert any("[sales-orders]" in line for line in node_lines)
    assert any("[sales_orders]" in line for line in node_lines)
    # The two node ids are NOT identical (collision check).
    ids = [line.strip().split("[")[0] for line in node_lines]
    assert ids[0] != ids[1], ids


def test_render_from_domain_md_parses_table() -> None:
    """Wrapper parses a real DOMAIN.md `# Glossary` table and renders the block."""
    domain_md = (
        "---\nid: feature-x\n---\n\n"
        "# Glossary\n\n"
        "| Term | Definition | Context | Invariants |\n"
        "|---|---|---|---|\n"
        "| [Invoice](context: billing) | The invoice. | billing | totals reconcile |\n"
        "| [Address](context: shipping) | The address. | shipping | — |\n"
        "| [Crate](context: shipping) | A shipping crate referencing"
        " [Address](context: shipping). | shipping | — |\n"
        "| [Refund](context: billing) | A refund touching"
        " [Address](context: shipping). | billing | — |\n\n"
        "# Bounded Contexts\n\n"
        "(placeholder)\n"
    )
    expected = (
        "```mermaid\n"
        "%% auto-generated; do not edit\n"
        "graph LR\n"
        "  ctx_billing[billing]\n"
        "  ctx_shipping[shipping]\n"
        "  ctx_billing --> ctx_shipping\n"
        "```"
    )
    assert render_from_domain_md(domain_md) == expected


def test_render_from_domain_md_skips_fenced_examples() -> None:
    """Fenced code blocks must NOT contribute glossary rows to the renderer."""
    domain_md = (
        "---\nid: feature-x\n---\n\n"
        "# Glossary\n\n"
        "| Term | Definition | Context | Invariants |\n"
        "|---|---|---|---|\n"
        "| [Invoice](context: billing) | The invoice. | billing | — |\n\n"
        "Example below should not be parsed:\n\n"
        "```markdown\n"
        "| [Bogus](context: ghost) | Should be ignored. | ghost | — |\n"
        "```\n\n"
        "# Bounded Contexts\n\n"
    )
    rendered = render_from_domain_md(domain_md)
    assert "ctx_ghost" not in rendered
    assert "ctx_billing[billing]" in rendered
