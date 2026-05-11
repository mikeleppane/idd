"""Tests for ``tools.cross_ai.cost`` — token heuristic + USD estimate.

Cases (a)-(i) per the cross-ai substrate plan:
  * (a)-(e) anchor the ceil-divide token heuristic at empty / 1-char /
    boundary / overflow / large-input lengths so a future regression to
    floor semantics fails immediately.
  * (f)-(h) lock the prompt-only USD formula (rate * tokens / 1000) at a
    known CLI, the unknown-fallback CLI, and the zero-token edge.
  * (i) audits the rate table itself: every entry must carry a
    ``source_url`` and ``retrieved_at`` so the table never silently
    decays into anonymous numbers.
"""

from __future__ import annotations

import pytest

from tools.cross_ai.cost import USD_PER_1K_TOKENS, CliRate, estimate_tokens, estimate_usd


def test_estimate_tokens_empty_string_returns_zero() -> None:
    # (a) Empty input must yield zero tokens — no ceil-divide off-by-one.
    assert estimate_tokens("") == 0


def test_estimate_tokens_single_char_returns_one() -> None:
    # (b) One character rounds up to one token under ceil semantics.
    assert estimate_tokens("a") == 1


def test_estimate_tokens_four_chars_returns_one() -> None:
    # (c) Exactly four characters fits in one token (boundary).
    assert estimate_tokens("abcd") == 1


def test_estimate_tokens_five_chars_returns_two() -> None:
    # (d) Five characters spills into a second token (boundary + 1).
    assert estimate_tokens("abcde") == 2


def test_estimate_tokens_four_thousand_chars_returns_one_thousand() -> None:
    # (e) Large multiple of four scales linearly: 4000 / 4 = 1000.
    assert estimate_tokens("a" * 4000) == 1000


def test_estimate_usd_claude_thousand_tokens() -> None:
    # (f) 1000 tokens * $0.003 / 1000 = $0.003 exactly. Float tolerance
    # via pytest.approx so a rate-table tweak fails loudly, not noisily.
    assert estimate_usd("claude", 1000) == pytest.approx(0.003, rel=1e-9)


def test_estimate_usd_unknown_cli_uses_fallback_rate() -> None:
    # (g) Unrecognised CLI must dispatch to the conservative 'unknown'
    # fallback ($0.005 / 1k input) — never silently zero.
    assert estimate_usd("nonexistent", 1000) == pytest.approx(0.005, rel=1e-9)


def test_estimate_usd_zero_tokens_is_zero_dollars() -> None:
    # (h) Zero tokens costs zero regardless of rate. Guards against a
    # "minimum charge" regression.
    assert estimate_usd("codex", 0) == 0.0


def test_rate_table_audit_all_entries_have_source_metadata() -> None:
    # (i) Rate-table audit guard: exactly the four documented CLIs, each
    # carrying a non-empty source_url and retrieved_at. Prevents a stale
    # entry from drifting into the table without provenance.
    assert set(USD_PER_1K_TOKENS) == {"codex", "claude", "gemini", "unknown"}
    for cli, rate in USD_PER_1K_TOKENS.items():
        assert isinstance(rate, CliRate), f"{cli!r} entry is not a CliRate"
        assert rate.source_url, f"{cli!r} missing source_url"
        assert rate.retrieved_at, f"{cli!r} missing retrieved_at"
