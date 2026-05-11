"""Pre-dispatch disclosure summary for the cross-AI substrate.

Given a built reviewer ``Prompt``, the post-redaction ``RedactionResult``,
the chosen reviewer ``CLI``, and the resolved ``CrossAiConfig``, produce a
frozen ``Disclosure`` snapshot the dispatcher can render to the operator
*before* any external CLI is invoked.

Two consumers read this surface:

* The renderer prints the file list, exclusions, token + cost estimate,
  and the literal command preview so the operator sees exactly what is
  about to leave the machine.
* The cost-warn gate reads ``cost_warn_triggered`` and ``had_redactions``
  to decide whether to require the ``APPROVE-COST`` literal before
  dispatch.

Sourcing rules
--------------
* ``file_list`` copies ``prompt.files_referenced`` verbatim. Redaction
  operates on the prompt text (replacing inline secrets with markers); it
  never mutates the file inventory the prompt builder discovered, so the
  two collections are independent.
* ``excluded_files`` is the redaction layer's authoritative list of paths
  dropped by ``deny_globs`` / ``gitignore_patterns`` after ``allow_globs``
  rescue (spec §5.3.11 RedactionResult shape). Disclosure forwards it
  untouched.
* ``prompt_tokens`` / ``prompt_cost_usd`` are computed against
  ``redaction_result.output_text`` — the post-scrub text that will
  actually ship — not the raw ``prompt.body``. Otherwise a redacted
  secret's original characters would inflate the estimate.
* ``cost_warn_triggered`` is a strict ``>`` against the configured
  threshold so a prompt sitting *exactly* at the threshold does not
  trigger the warn path.
* ``command_preview`` is a literal string template; this module never
  spawns a subprocess. The auto-mode dispatcher constructs the real
  argv at invocation time.

Purity
------
``build_disclosure`` is sync and pure: same inputs always produce an
equal ``Disclosure``. No filesystem access, no subprocess, no clock.
That guarantee is what lets the renderer surface a stable preview the
operator can trust to match what the dispatcher will send.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tools import redaction
from tools.cross_ai import cost
from tools.cross_ai.config import CrossAiConfig
from tools.cross_ai.detect import CLI
from tools.cross_ai.prompt import Prompt, PromptTarget


@dataclass(frozen=True)
class Disclosure:
    """Pre-dispatch summary handed to the operator before the CLI is called.

    Attributes:
        target: ``plan`` or ``code`` — copied from the source ``Prompt`` so
            the renderer can label the preview without re-deriving it.
        cli: The chosen reviewer CLI; surfaced verbatim so the operator
            sees which family is about to receive the prompt.
        file_list: Paths the prompt body discusses, sourced from
            ``prompt.files_referenced``. Disclosure does not mutate the
            inventory; redaction's exclusions live in ``excluded_files``.
        excluded_files: Paths the redaction layer dropped (deny-glob /
            gitignore hit without an allow-glob rescue). Empty tuple when
            redaction excluded nothing.
        diff_loc: Optional pre-computed diff line count for ``code``
            target prompts; defaults to 0 because the plan-target branch
            has no diff. Provided by the caller (the renderer may compute
            it once); disclosure stores rather than recomputes.
        command_preview: Literal string the renderer prints — never a
            real argv. Shape: ``"<cli> <self-contained-prompt>"``.
        prompt_tokens: ``ceil(len(redacted_text) / 4)`` per
            ``cost.estimate_tokens``. Counted against the post-scrub text.
        prompt_cost_usd: Prompt-only USD estimate per
            ``cost.estimate_usd(cli.value, prompt_tokens)``.
        had_redactions: Mirrors ``redaction_result.had_denials`` —
            whether any file was excluded or any inline span was scrubbed.
            Fatal regex hits are surfaced separately on the redaction
            result and do NOT flip this flag (see spec §5.3.11
            RedactionResult shape).
        cost_warn_triggered: ``prompt_cost_usd > config.cost_warn_threshold_usd``
            (strict greater-than). The dispatcher uses this to decide
            whether to require the ``APPROVE-COST`` literal before the
            external CLI is invoked.
    """

    target: PromptTarget
    cli: CLI
    file_list: tuple[PurePosixPath, ...]
    excluded_files: tuple[PurePosixPath, ...]
    diff_loc: int
    command_preview: str
    prompt_tokens: int
    prompt_cost_usd: float
    had_redactions: bool
    cost_warn_triggered: bool


def build_disclosure(
    prompt: Prompt,
    redaction_result: redaction.RedactionResult,
    cli: CLI,
    config: CrossAiConfig,
    diff_loc: int = 0,
) -> Disclosure:
    """Build the pre-dispatch summary the renderer + warn-gate consume.

    Pure and sync per module docstring; no IO. Same inputs always yield
    an equal ``Disclosure``.

    Args:
        prompt: The ``Prompt`` produced by ``tools.cross_ai.prompt.build_prompt``.
            ``target`` and ``files_referenced`` are copied onto the result.
        redaction_result: Output of ``tools.redaction.filter`` over the
            prompt body + file inventory. ``output_text`` drives the
            cost estimate; ``excluded_files`` and ``had_denials`` flow
            into the disclosure.
        cli: The chosen reviewer CLI from ``detect.pick_reviewer``.
        config: Resolved ``CrossAiConfig`` — ``cost_warn_threshold_usd``
            gates ``cost_warn_triggered``.
        diff_loc: Optional pre-computed diff line count for ``code``
            target previews; ``0`` when unknown or not applicable.

    Returns:
        Frozen ``Disclosure``.
    """
    # Cost estimate runs against the POST-redaction text — the bytes
    # that will actually ship — so a redacted secret's original
    # characters never inflate the estimate.
    prompt_tokens = cost.estimate_tokens(redaction_result.output_text)
    prompt_cost_usd = cost.estimate_usd(cli.value, prompt_tokens)

    # Strict greater-than: a prompt sitting exactly at the threshold
    # does not trigger the warn path.
    cost_warn_triggered = prompt_cost_usd > config.cost_warn_threshold_usd

    # Literal template — the auto-mode dispatcher builds the real argv
    # at dispatch time.
    command_preview = f"{cli.value} <self-contained-prompt>"

    return Disclosure(
        target=prompt.target,
        cli=cli,
        file_list=prompt.files_referenced,
        excluded_files=redaction_result.excluded_files,
        diff_loc=diff_loc,
        command_preview=command_preview,
        prompt_tokens=prompt_tokens,
        prompt_cost_usd=prompt_cost_usd,
        had_redactions=redaction_result.had_denials,
        cost_warn_triggered=cost_warn_triggered,
    )
