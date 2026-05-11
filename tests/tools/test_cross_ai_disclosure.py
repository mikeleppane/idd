"""Tests for ``tools.cross_ai.disclosure`` — pre-dispatch summary builder.

Cases (a)-(f) per the cross-ai substrate plan:
  * (a) Empty prompt + default config + empty redaction → all-zero
    disclosure with the cost warn flag clear. Anchors the zero-input
    branch so a future regression to `len(text) // 4` (which would still
    return 0) does not silently flip the warn flag.
  * (b) ~5000-character prompt at the claude rate sits comfortably below
    the default 0.50 USD threshold — locks the prompt-only formula.
  * (c) ~1_000_000-character prompt at the claude rate trips the warn
    threshold — locks the > comparison and the unit math.
  * (d) ``had_redactions`` mirrors ``redaction_result.had_denials`` so a
    file exclusion routes the disclosure into the warn-render branch.
  * (e) ``command_preview`` literally starts with the CLI value; the
    builder never spawns a subprocess (pure string template).
  * (f) ``Disclosure`` is frozen — caller cannot mutate after build, so
    P2 render and P3 evaluate read a stable snapshot.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import PurePosixPath

import pytest

from tools import redaction
from tools.cross_ai.config import CrossAiConfig
from tools.cross_ai.detect import CLI
from tools.cross_ai.disclosure import Disclosure, build_disclosure
from tools.cross_ai.prompt import Prompt, PromptTarget


def _empty_prompt(target: PromptTarget = PromptTarget.plan) -> Prompt:
    return Prompt(target=target, feature_id="feat-x", body="", files_referenced=())


def _empty_redaction(text: str = "") -> redaction.RedactionResult:
    return redaction.RedactionResult(output_text=text)


def test_empty_inputs_yield_zero_disclosure() -> None:
    # (a) No prompt text, no excluded files, default 0.50 USD threshold:
    # everything zero, nothing to warn about.
    result = build_disclosure(
        prompt=_empty_prompt(),
        redaction_result=_empty_redaction(""),
        cli=CLI.claude,
        config=CrossAiConfig(),
    )

    assert result.target is PromptTarget.plan
    assert result.cli is CLI.claude
    assert result.file_list == ()
    assert result.excluded_files == ()
    assert result.diff_loc == 0
    assert result.prompt_tokens == 0
    assert result.prompt_cost_usd == 0.0
    assert result.had_redactions is False
    assert result.cost_warn_triggered is False
    assert result.command_preview == "claude <self-contained-prompt>"


def test_moderate_prompt_below_warn_threshold() -> None:
    # (b) 5_000 chars → 1_250 tokens → 1_250 * 0.003 / 1000 = 0.00375 USD,
    # well under the default 0.50 USD warn threshold.
    text = "x" * 5_000
    result = build_disclosure(
        prompt=_empty_prompt(),
        redaction_result=_empty_redaction(text),
        cli=CLI.claude,
        config=CrossAiConfig(),
    )

    assert result.prompt_tokens == 1_250
    assert result.prompt_cost_usd == pytest.approx(0.00375)
    assert result.cost_warn_triggered is False


def test_large_prompt_trips_warn_threshold() -> None:
    # (c) 1_000_000 chars → 250_000 tokens → 0.75 USD at the claude rate,
    # which clears the default 0.50 USD warn threshold.
    text = "x" * 1_000_000
    result = build_disclosure(
        prompt=_empty_prompt(),
        redaction_result=_empty_redaction(text),
        cli=CLI.claude,
        config=CrossAiConfig(),
    )

    assert result.prompt_tokens == 250_000
    assert result.prompt_cost_usd == pytest.approx(0.75)
    assert result.cost_warn_triggered is True


def test_had_redactions_mirrors_had_denials() -> None:
    # (d) An excluded file flips ``had_denials`` on the redaction result;
    # the disclosure must surface that as ``had_redactions``.
    excluded = (PurePosixPath(".env"),)
    red = redaction.RedactionResult(excluded_files=excluded, output_text="")

    result = build_disclosure(
        prompt=_empty_prompt(),
        redaction_result=red,
        cli=CLI.claude,
        config=CrossAiConfig(),
    )

    assert red.had_denials is True
    assert result.had_redactions is True
    assert result.excluded_files == excluded


def test_command_preview_starts_with_cli_value() -> None:
    # (e) Preview is a literal template; never invokes a subprocess.
    for cli in (CLI.codex, CLI.claude, CLI.gemini):
        result = build_disclosure(
            prompt=_empty_prompt(),
            redaction_result=_empty_redaction(""),
            cli=cli,
            config=CrossAiConfig(),
        )
        assert result.command_preview.startswith(cli.value + " ")
        assert result.command_preview == f"{cli.value} <self-contained-prompt>"


def test_disclosure_is_frozen() -> None:
    # (f) Frozen dataclass — P2 render and P3 evaluate read a stable
    # snapshot; mutation must raise.
    result = build_disclosure(
        prompt=_empty_prompt(),
        redaction_result=_empty_redaction(""),
        cli=CLI.claude,
        config=CrossAiConfig(),
    )

    with pytest.raises(FrozenInstanceError):
        result.prompt_tokens = 999  # type: ignore[misc]


def test_file_list_sourced_from_prompt_files_referenced() -> None:
    # Sanity: file_list copies prompt.files_referenced verbatim. Redaction
    # operates on prompt text, not on the file inventory, so the two
    # tuples are sourced independently.
    files = (PurePosixPath("tools/a.py"), PurePosixPath("tools/b.py"))
    prompt = Prompt(
        target=PromptTarget.code,
        feature_id="feat-y",
        body="",
        files_referenced=files,
    )
    excluded = (PurePosixPath(".env"),)
    red = redaction.RedactionResult(excluded_files=excluded, output_text="")

    result = build_disclosure(
        prompt=prompt,
        redaction_result=red,
        cli=CLI.codex,
        config=CrossAiConfig(),
        diff_loc=42,
    )

    assert result.target is PromptTarget.code
    assert result.file_list == files
    assert result.excluded_files == excluded
    assert result.diff_loc == 42


def test_disclosure_is_pure_same_inputs_same_output() -> None:
    # Determinism guard: build twice with the same inputs, expect equal
    # disclosures (frozen dataclass equality covers every field).
    prompt = _empty_prompt()
    red = _empty_redaction("hello world")
    config = CrossAiConfig()

    a = build_disclosure(prompt=prompt, redaction_result=red, cli=CLI.gemini, config=config)
    b = build_disclosure(prompt=prompt, redaction_result=red, cli=CLI.gemini, config=config)
    assert a == b
    assert isinstance(a, Disclosure)
