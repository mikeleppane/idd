"""UAT-replay module for `/forge:harden`.

Replays the conversational UAT prompts recorded in a feature's
``VERIFICATION.md`` against the merged artifact and folds the per-prompt
outcomes into a structured :class:`UATResult`.

Two modes are supported:

* **interactive** — caller supplies a ``prompter`` callable that re-asks the
  human reviewer. The skill wires a real ``input()``-driven prompter; the
  default keeps the module importable by returning ``skipped`` for every
  prompt.
* **non-interactive** — caller supplies a ``transcript_path`` pointing at a
  JSONL file recorded during the original UAT pass. Each line carries
  ``{"prompt_id", "status", "detail"}``; prompt-ids without a matching record
  fold in as ``skipped``.

Prompt parsing is fence-aware: a fenced ``# UAT`` example block in
VERIFICATION.md cannot smuggle prompts into the replay. Bullets in the live
``# UAT`` (or ``## UAT``) section are picked up when they end in ``?`` or are
written as a blockquote (``> ...``). ``HardenError`` from
:mod:`tools.harden.contract` is reused so harden modules surface a single
error type.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tools.harden.contract import HardenError
from tools.validate._frontmatter import _read_text, _strip_code

UATStatus = Literal["pass", "fail", "skipped", "partial"]
PromptStatus = Literal["confirmed", "disconfirmed", "skipped"]
ReplayMode = Literal["interactive", "non-interactive"]

# Match the body of a top- or sub-level ``UAT`` heading. Sliced off at the
# next sibling-or-higher heading so adjacent ``# Coverage`` / ``# Gaps``
# sections don't pollute the prompt list.
_UAT_BLOCK = re.compile(
    r"(?ms)^(?P<level>#{1,2})\s+UAT\b[^\n]*\n(?P<body>.*?)(?=^#{1,2} |\Z)",
)

# Bullet-list line: ``-`` or ``*`` plus space, then the prompt text.
_BULLET = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$", re.MULTILINE)

# Default detail returned by the no-op prompter so callers can distinguish a
# genuinely-skipped prompt from an unconfigured replay run.
_DEFAULT_PROMPTER_DETAIL: Final[str] = "no prompter configured"


@dataclass(frozen=True)
class UATPrompt:
    """A single UAT prompt parsed out of VERIFICATION.md.

    Attributes:
        prompt_id: Canonical id (``prompt-1``, ``prompt-2``, ...) derived from
            the order the bullet appears in the UAT section.
        text: The prompt text — question or blockquoted assertion — re-asked
            against the merged artifact.
    """

    prompt_id: str
    text: str


@dataclass(frozen=True)
class UATOutcome:
    """Outcome of replaying a single prompt.

    Attributes:
        prompt_id: Canonical id (matches the input :class:`UATPrompt`).
        status: ``confirmed`` / ``disconfirmed`` / ``skipped``.
        detail: One-line user response or transcript content. Empty when
            ``status == "skipped"`` and no detail was recorded.
    """

    prompt_id: str
    status: PromptStatus
    detail: str


@dataclass(frozen=True)
class UATResult:
    """Aggregate result of replaying every UAT prompt.

    Attributes:
        status: ``pass`` when every prompt was confirmed; ``fail`` when at
            least one prompt was disconfirmed; ``skipped`` when the feature
            recorded no UAT prompts (or has no VERIFICATION.md); ``partial``
            when at least one prompt was skipped and zero were disconfirmed.
        mode: Whether the replay ran ``interactive`` or ``non-interactive``.
        prompts_replayed: Number of prompts dispatched.
        prompts_confirmed: Number of prompts whose outcome was ``confirmed``.
        outcomes: Per-prompt outcomes, in VERIFICATION source order.
    """

    status: UATStatus
    mode: ReplayMode
    prompts_replayed: int
    prompts_confirmed: int
    outcomes: list[UATOutcome] = field(default_factory=list)


def _default_prompter(prompt: UATPrompt) -> UATOutcome:
    """Return a ``skipped`` outcome — no real prompter wired in.

    Keeps the module importable and exercisable without a real input loop.
    Real wiring lives in the harden orchestrator skill, which threads
    ``input()`` (or its non-TTY equivalent) into a callable.
    """
    return UATOutcome(
        prompt_id=prompt.prompt_id,
        status="skipped",
        detail=_DEFAULT_PROMPTER_DETAIL,
    )


def _parse_prompts(verification_text: str) -> list[UATPrompt]:
    """Parse the ``# UAT`` (or ``## UAT``) section into ordered prompts.

    Fence-aware: ``_strip_code`` is applied to the section body before
    bullet scanning, so fenced example blocks (which the VERIFICATION
    template uses for illustrations) cannot smuggle prompts into the parse.

    A bullet becomes a prompt when its text either ends with ``?`` or is
    written as a blockquote (``> ...``). Other bullets are treated as
    section commentary and skipped — keeps stray notes out of the replay
    loop.
    """
    block_match = _UAT_BLOCK.search(verification_text)
    if block_match is None:
        return []

    stripped_body = _strip_code(block_match.group("body"))

    prompts: list[UATPrompt] = []
    for bullet in _BULLET.finditer(stripped_body):
        text = bullet.group("text").strip()
        if not text:
            continue
        if text.endswith("?") or text.startswith(">"):
            cleaned = text.lstrip("> ").strip() if text.startswith(">") else text
            prompts.append(
                UATPrompt(
                    prompt_id=f"prompt-{len(prompts) + 1}",
                    text=cleaned,
                )
            )
    return prompts


def _aggregate(outcomes: list[UATOutcome]) -> UATStatus:
    """Fold per-prompt statuses into the replay-level status.

    - ``pass`` — every outcome is ``confirmed``.
    - ``fail`` — at least one outcome is ``disconfirmed``.
    - ``partial`` — no disconfirmations, at least one skip.
    - ``skipped`` — empty outcome list (no prompts found).
    """
    if not outcomes:
        return "skipped"
    if any(outcome.status == "disconfirmed" for outcome in outcomes):
        return "fail"
    if any(outcome.status == "skipped" for outcome in outcomes):
        return "partial"
    return "pass"


def _load_transcript(transcript_path: Path) -> dict[str, UATOutcome]:
    """Load a JSONL transcript file into a ``prompt_id`` → :class:`UATOutcome` map.

    Each line is expected to be a JSON object with ``prompt_id``, ``status``,
    and ``detail`` keys. Malformed lines or unknown statuses raise
    :class:`HardenError` so transcript drift surfaces loudly instead of
    silently degrading to ``skipped``.
    """
    if not transcript_path.is_file():
        raise HardenError(f"transcript file not found: {transcript_path}")

    raw = transcript_path.read_text(encoding="utf-8")
    records: dict[str, UATOutcome] = {}
    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise HardenError(f"transcript line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise HardenError(
                f"transcript line {line_number} must be a JSON object, got {type(payload).__name__}"
            )
        prompt_id = payload.get("prompt_id")
        status = payload.get("status")
        detail = payload.get("detail", "")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise HardenError(f"transcript line {line_number} missing string 'prompt_id'")
        if status not in ("confirmed", "disconfirmed", "skipped"):
            raise HardenError(f"transcript line {line_number} has unsupported status {status!r}")
        if not isinstance(detail, str):
            raise HardenError(f"transcript line {line_number} 'detail' must be a string")
        records[prompt_id] = UATOutcome(prompt_id=prompt_id, status=status, detail=detail)
    return records


def run_uat_replay(
    repo_root: Path,
    feature_id: str,
    *,
    mode: ReplayMode = "interactive",
    prompter: Callable[[UATPrompt], UATOutcome] | None = None,
    transcript_path: Path | None = None,
) -> UATResult:
    """Replay UAT prompts from `.forge/features/<feature_id>/VERIFICATION.md`.

    Args:
        repo_root: Repository root the feature folder resolves under.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        mode: ``interactive`` (default) — ``prompter`` re-asks the human;
            ``non-interactive`` — ``transcript_path`` supplies recorded
            outcomes.
        prompter: Optional callable that handles a single interactive prompt.
            When omitted, the default prompter returns ``skipped`` for every
            prompt so the module stays importable without a real input loop.
        transcript_path: JSONL file with ``{"prompt_id", "status", "detail"}``
            records. Required when ``mode == "non-interactive"``.

    Returns:
        :class:`UATResult` with per-prompt outcomes preserved in
        VERIFICATION source order. When the feature has no
        ``VERIFICATION.md``, or VERIFICATION.md has no UAT prompts, returns
        ``status="skipped"`` rather than raising — many features ship without
        a recorded UAT pass.

    Raises:
        HardenError: When ``mode == "non-interactive"`` but no
            ``transcript_path`` was supplied, or the supplied transcript file
            is missing / malformed.
    """
    verification_path = repo_root / ".forge" / "features" / feature_id / "VERIFICATION.md"
    verification_text = _read_text(verification_path)
    if verification_text is None:
        return UATResult(
            status="skipped",
            mode=mode,
            prompts_replayed=0,
            prompts_confirmed=0,
            outcomes=[],
        )

    prompts = _parse_prompts(verification_text)
    if not prompts:
        return UATResult(
            status="skipped",
            mode=mode,
            prompts_replayed=0,
            prompts_confirmed=0,
            outcomes=[],
        )

    outcomes: list[UATOutcome]
    if mode == "non-interactive":
        if transcript_path is None:
            raise HardenError("transcript_path required for non-interactive mode")
        records = _load_transcript(transcript_path)
        outcomes = [
            records.get(
                prompt.prompt_id,
                UATOutcome(prompt_id=prompt.prompt_id, status="skipped", detail=""),
            )
            for prompt in prompts
        ]
    else:
        active_prompter = prompter if prompter is not None else _default_prompter
        outcomes = [active_prompter(prompt) for prompt in prompts]

    prompts_confirmed = sum(1 for outcome in outcomes if outcome.status == "confirmed")

    return UATResult(
        status=_aggregate(outcomes),
        mode=mode,
        prompts_replayed=len(outcomes),
        prompts_confirmed=prompts_confirmed,
        outcomes=outcomes,
    )
