#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool.

Reads the hook input on stdin (JSON). The dispatch prompt MUST contain a
top-level ``context_budget:`` YAML block (column 0, outside any fenced code
block). The block must declare a non-empty ``files_in_scope`` list of bounded
globs, and a non-empty ``forbidden`` list. Otherwise the hook returns a
PreToolUse deny decision; otherwise it returns an empty object (allow).

Idempotent and side-effect-free.

Hook input shape (per Claude Code docs):
{
  "session_id": "...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Agent",
  "tool_input": { "prompt": "...", ... },
  ...
}
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

import yaml

# Tool names that mean "subagent dispatch" across Claude Code versions.
# Matcher in hooks.json is "Agent"; this defensive check covers historical
# naming ("Task") so an upstream rename does not silently disable the hook.
_DISPATCH_TOOL_NAMES = frozenset({"Agent", "Task"})

# Items in files_in_scope that scope the entire repository (or close enough).
_UNBOUNDED_LITERALS = frozenset({"**", "*", "./**", "/**", "."})
_UNBOUNDED_KEYWORDS = frozenset({"all", "any", "everything", "*"})
# Bare extension glob with no directory prefix: "*.py", "*.ts".
_BARE_EXTENSION_GLOB = re.compile(r"^\*\.[a-zA-Z0-9]+$")


def _is_unbounded_glob(item: str) -> bool:
    """Return True when a files_in_scope item lacks a directory anchor.

    Bounded examples: ``src/**/*.py``, ``tools/state.py``, ``tests/**``.
    Unbounded examples: ``**``, ``*.py`` (repo-wide), ``all``.
    """
    stripped = item.strip().strip("'\"")
    if not stripped:
        return True
    if stripped in _UNBOUNDED_LITERALS:
        return True
    if stripped.lower() in _UNBOUNDED_KEYWORDS:
        return True
    return bool(_BARE_EXTENSION_GLOB.fullmatch(stripped))


def _extract_budget_block(prompt: str) -> str | None:
    """Return the YAML text of the first top-level ``context_budget:`` block.

    The block must start with ``context_budget:`` at column 0, outside any
    fenced code block (```...```), and continues until the next non-indented,
    non-blank line (i.e. the next top-level construct) or end of input.
    """
    lines = prompt.splitlines()

    in_fence = False
    start = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("context_budget:"):
            start = i
            break

    if start < 0:
        return None

    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.lstrip().startswith("```"):
            break
        if line == "" or line[0] in (" ", "\t"):
            body.append(line)
            continue
        break

    return "\n".join(body)


def _validate_files_in_scope(files: Any) -> str | None:
    """Return a deny reason for an invalid files_in_scope, or None when valid."""
    if files is None:
        return "context_budget.files_in_scope is required"
    if not isinstance(files, list):
        return (
            "context_budget.files_in_scope must be a YAML list "
            f"(got {type(files).__name__})"
        )
    if len(files) == 0:
        return "context_budget.files_in_scope must be a non-empty list"
    for item in files:
        if not isinstance(item, str):
            return (
                "context_budget.files_in_scope items must be strings "
                f"(got {type(item).__name__})"
            )
        if _is_unbounded_glob(item):
            return f"context_budget.files_in_scope item is unbounded: '{item}'"
    return None


def _validate_forbidden(forbidden: Any) -> str | None:
    """Return a deny reason for an invalid forbidden list, or None when valid."""
    if forbidden is None:
        return "context_budget.forbidden is required"
    if not isinstance(forbidden, list):
        return (
            "context_budget.forbidden must be a YAML list "
            f"(got {type(forbidden).__name__})"
        )
    if len(forbidden) == 0:
        return "context_budget.forbidden must list at least one explicit prohibition"
    return None


def _validate_budget(budget: Any) -> tuple[bool, str]:
    """Validate the parsed ``context_budget`` mapping. Return (allow, reason)."""
    if not isinstance(budget, dict):
        return False, (
            f"context_budget must be a YAML mapping, got {type(budget).__name__}"
        )

    for reason in (
        _validate_files_in_scope(budget.get("files_in_scope")),
        _validate_forbidden(budget.get("forbidden")),
    ):
        if reason is not None:
            return False, reason

    return True, "ok"


def evaluate(prompt: str) -> tuple[bool, str]:
    """Return (allow, reason). allow=False means block the dispatch.

    Args:
        prompt: The dispatch prompt body to evaluate.

    Returns:
        (allow_flag, human-readable reason).
    """
    block_text = _extract_budget_block(prompt)
    if block_text is None:
        return False, "missing required `context_budget:` block at top of dispatch prompt"

    try:
        parsed = yaml.safe_load(block_text)
    except yaml.YAMLError as exc:
        return False, f"context_budget block is not valid YAML: {exc}"

    if not isinstance(parsed, dict) or "context_budget" not in parsed:
        return False, "context_budget block did not parse as a mapping with key 'context_budget'"

    return _validate_budget(parsed["context_budget"])


def main() -> int:
    """Read stdin JSON, evaluate, emit decision JSON. Exit 0 on success."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return 0

    if payload.get("tool_name") not in _DISPATCH_TOOL_NAMES:
        print(json.dumps({}))
        return 0

    prompt = payload.get("tool_input", {}).get("prompt", "")
    allow, reason = evaluate(prompt)

    if allow:
        print(json.dumps({}))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"IDD context-budget hook: {reason}",
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
