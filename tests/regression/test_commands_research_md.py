"""Lock test: ``commands/research.md`` user-facing surface contract.

The slash spec for `/forge:research` documents the args, tier-routing
rules, and the five grounding modes a user can encounter at exit. The
focused-tier refusal hint must match the literal string the skill
returns so the docs and the runtime stay in lock-step.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_COMMAND: Path = _REPO_ROOT / "commands" / "research.md"

_FOCUSED_REFUSAL = (
    'Research escalates to standard tier. Use /forge:do --standard --research "<idea>".'
)


def test_command_file_exists() -> None:
    assert _COMMAND.is_file(), f"missing command file: {_COMMAND.relative_to(_REPO_ROOT)}"


def test_feature_arg_documented() -> None:
    text = _COMMAND.read_text(encoding="utf-8")
    assert "--feature <id>" in text, "missing `--feature <id>` arg documentation"


def test_skip_arg_documented() -> None:
    text = _COMMAND.read_text(encoding="utf-8")
    assert '--skip "<reason>"' in text, 'missing `--skip "<reason>"` arg documentation'


def test_focused_tier_refusal_hint_matches_spec() -> None:
    text = _COMMAND.read_text(encoding="utf-8")
    assert _FOCUSED_REFUSAL in text, (
        "focused-tier refusal hint must match the literal spec string verbatim"
    )


def test_grounding_table_lists_all_five_modes() -> None:
    text = _COMMAND.read_text(encoding="utf-8")
    for mode in ("full", "degraded", "websearch", "byod", "byod-partial"):
        # Match `<mode>` inside a backtick fence to avoid accidental hits in
        # surrounding prose (e.g. "full mode" without the backticks).
        assert re.search(rf"`{re.escape(mode)}`", text), (
            f"grounding-mode summary table missing mode `{mode}`"
        )


def test_no_milestone_or_phase_refs() -> None:
    text = _COMMAND.read_text(encoding="utf-8")
    forbidden_patterns = (
        r"\bM8\b",
        r"\bP[0-6]\b",
    )
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text), (
            f"command file contains forbidden milestone/phase reference matching: {pattern}"
        )
