"""Cross-AI cost estimation — char-based token heuristic + per-CLI rate table.

Estimates are **advisory** per spec §5.3.3: they exist to surface a
ballpark cost before a peer-review dispatch, not to bill anyone. We
deliberately avoid runtime tokenizer dependencies (e.g. ``tiktoken``)
because the substrate must stay zero-install per spec D-7 + D-16; a
4-char-per-token rule of thumb is "good enough" for a warn threshold.

Token heuristic
---------------
``estimate_tokens(text)`` returns ``ceil(len(text) / 4)``, expressed as
``(len(text) + 3) // 4`` so the empty string maps to 0 and any
non-empty input maps to at least 1. This matches OpenAI's
"≈ 4 characters per English token" rule of thumb (see the OpenAI
tokenization guide, retrieved 2026-05-10).

Cost heuristic
--------------
``estimate_usd(cli, prompt_tokens)`` returns
``prompt_tokens * rate.input_per_1k_usd / 1000`` — **prompt-only**, by
design. We do not know the response length at estimate time, and
counting unknowns toward a warn threshold would either over- or
under-warn. Unrecognised CLIs fall through to the conservative
``"unknown"`` rate so a typo never silently zeros out the warning.

Rate table provenance (all retrieved 2026-05-10)
------------------------------------------------
* ``codex``  — https://openai.com/api/pricing
* ``claude`` — https://www.anthropic.com/pricing
* ``gemini`` — https://ai.google.dev/pricing
* ``unknown`` — conservative fallback, ~2x the most expensive listed
  input rate so an unmapped CLI errs loud rather than quiet.

The constants are intentionally hard-coded: a runtime fetch against a
vendor pricing page would couple the substrate to network reachability
and HTML scraping, neither of which belongs in a peer-review tool.
Refresh by hand when a vendor changes its pricing and bump
``retrieved_at`` in the same commit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliRate:
    """Per-CLI input/output USD rate snapshot with provenance metadata.

    ``source_url`` and ``retrieved_at`` are mandatory so the rate table
    never decays into anonymous numbers — a future audit can re-verify
    each entry against the cited page.
    """

    input_per_1k_usd: float
    output_per_1k_usd: float
    source_url: str
    retrieved_at: str


USD_PER_1K_TOKENS: dict[str, CliRate] = {
    "codex": CliRate(0.0025, 0.010, "https://openai.com/api/pricing", "2026-05-10"),
    "claude": CliRate(0.003, 0.015, "https://www.anthropic.com/pricing", "2026-05-10"),
    "gemini": CliRate(0.00125, 0.005, "https://ai.google.dev/pricing", "2026-05-10"),
    "unknown": CliRate(0.005, 0.020, "conservative-fallback", "2026-05-10"),
}


def estimate_tokens(text: str) -> int:
    """Return ``ceil(len(text) / 4)`` — char-based token estimate.

    Empty string → 0 tokens; any non-empty input → at least 1. Advisory
    only per spec §5.3.3; no tokenizer dependency by design.
    """
    return (len(text) + 3) // 4


def estimate_usd(cli: str, prompt_tokens: int) -> float:
    """Estimate prompt-only USD cost for ``prompt_tokens`` against ``cli``.

    Unknown ``cli`` values dispatch to the conservative ``"unknown"``
    fallback rate. Response cost is intentionally excluded — see module
    docstring.
    """
    rate = USD_PER_1K_TOKENS.get(cli, USD_PER_1K_TOKENS["unknown"])
    return prompt_tokens * rate.input_per_1k_usd / 1000
