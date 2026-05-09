#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool.

Reads the hook input on stdin (JSON). The dispatch prompt MUST contain a
top-level ``context_budget:`` marker (column 0, outside any fenced code
block) followed by a JSON object literal. The object must declare a
non-empty ``files_in_scope`` list of bounded globs, and a non-empty
``forbidden`` list. Otherwise the hook returns a PreToolUse deny decision;
otherwise it returns an empty object (allow).

Stdlib only. Idempotent and side-effect-free.

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

# Tool names that mean "subagent dispatch" across Claude Code versions.
# Matcher in hooks.json is "Agent"; this defensive check covers historical
# naming ("Task") so an upstream rename does not silently disable the hook.
_DISPATCH_TOOL_NAMES = frozenset({"Agent", "Task"})

# Items in files_in_scope that scope the entire repository (or close enough).
_UNBOUNDED_LITERALS = frozenset({"**", "*", "./**", "/**", "."})
_UNBOUNDED_KEYWORDS = frozenset({"all", "any", "everything", "*"})
# Bare extension glob with no directory prefix: "*.py", "*.ts".
_BARE_EXTENSION_GLOB = re.compile(r"^\*\.[a-zA-Z0-9]+$")

# Phase enum frozen literal. Canonical source: schemas/state.schema.json
# (current_phase + phases.propertyNames + commits[].phase + skipped[].phase
# + deviations[].phase). The hook stays stdlib-only — no jsonschema import,
# no tools.* import — so drift between this set and the canonical schema is
# caught by the schema enum tests in tests/tools/. Only "execute" triggers
# tests_in_scope enforcement.
_EXECUTE_PHASE = "execute"

_MARKER = "context_budget:"


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


def _find_marker_line(lines: list[str]) -> int:
    """Return the index of the first top-level ``context_budget:`` line, or -1."""
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith(_MARKER):
            return i
    return -1


def _balanced_json_object(text: str) -> str | None:
    """Return the substring covering the first balanced JSON object in `text`, or None.

    String-aware brace counting; ignores ``{``/``}`` inside double-quoted strings
    and respects backslash escapes. Returns None on unbalanced input.
    """
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[: i + 1]
    return None


def _extract_budget_block(prompt: str) -> str | None:
    """Return the JSON text of the first top-level ``context_budget:`` block.

    The marker is the literal ``context_budget:`` at column 0, outside any
    fenced code block (```...```). The body is the next JSON object literal,
    found by scanning for the next ``{`` and balancing braces (string-aware).
    """
    lines = prompt.splitlines(keepends=True)
    marker_line_idx = _find_marker_line(lines)
    if marker_line_idx < 0:
        return None

    rest = "".join(lines[marker_line_idx:])[len(_MARKER) :]
    open_idx = rest.find("{")
    if open_idx < 0:
        return None
    if rest[:open_idx].strip() != "":
        return None  # junk between marker and JSON object

    return _balanced_json_object(rest[open_idx:])


def _validate_files_in_scope(files: Any) -> str | None:
    """Return a deny reason for an invalid files_in_scope, or None when valid."""
    if files is None:
        return "context_budget.files_in_scope is required"
    if not isinstance(files, list):
        return f"context_budget.files_in_scope must be a JSON array (got {type(files).__name__})"
    if len(files) == 0:
        return "context_budget.files_in_scope must be a non-empty array"
    for item in files:
        if not isinstance(item, str):
            return (
                f"context_budget.files_in_scope items must be strings (got {type(item).__name__})"
            )
        if _is_unbounded_glob(item):
            return f"context_budget.files_in_scope item is unbounded: '{item}'"
    return None


def _validate_forbidden(forbidden: Any) -> str | None:
    """Return a deny reason for an invalid forbidden list, or None when valid."""
    if forbidden is None:
        return "context_budget.forbidden is required"
    if not isinstance(forbidden, list):
        return f"context_budget.forbidden must be a JSON array (got {type(forbidden).__name__})"
    if len(forbidden) == 0:
        return "context_budget.forbidden must list at least one explicit prohibition"
    return None


def _validate_tests_in_scope_shape(tests: Any) -> str | None:
    """Return a shape-error reason for tests_in_scope, or None when shape is OK or absent."""
    if tests is None:
        return None
    if not isinstance(tests, list):
        return f"context_budget.tests_in_scope must be a JSON array (got {type(tests).__name__})"
    for item in tests:
        if not isinstance(item, str):
            return (
                f"context_budget.tests_in_scope items must be strings (got {type(item).__name__})"
            )
    return None


def _validate_tests_in_scope(budget: dict[str, Any]) -> str | None:
    """Return a deny reason when execute-phase budget lacks tests_in_scope, else None.

    Rules:
    - When ``phase == "execute"``: ``tests_in_scope`` MUST be a non-empty
      list of strings, UNLESS the budget block also declares
      ``tdd_exception_ref: "<ADR-id>"``.
    - When ``phase`` is absent or any non-execute value: ``tests_in_scope``
      is optional; if present, it must still be a list of strings (cheap
      shape check that protects downstream consumers without rejecting
      legacy dispatches that omit the field entirely).
    """
    tests = budget.get("tests_in_scope")
    shape_error = _validate_tests_in_scope_shape(tests)
    if shape_error is not None:
        return shape_error

    if budget.get("phase") != _EXECUTE_PHASE:
        return None

    exception_ref = budget.get("tdd_exception_ref")
    has_exception = isinstance(exception_ref, str) and exception_ref.strip() != ""
    if has_exception:
        return None

    if tests is None:
        return (
            "context_budget.tests_in_scope is required for execute-phase dispatches "
            "(set tdd_exception_ref to an ADR id to allow empty)"
        )
    if len(tests) == 0:
        return (
            "context_budget.tests_in_scope must be non-empty for execute-phase dispatches "
            "(set tdd_exception_ref to an ADR id to allow empty)"
        )
    return None


def _validate_budget(budget: Any) -> tuple[bool, str]:
    """Validate the parsed budget object. Return (allow, reason)."""
    if not isinstance(budget, dict):
        return False, f"context_budget must be a JSON object, got {type(budget).__name__}"

    for reason in (
        _validate_files_in_scope(budget.get("files_in_scope")),
        _validate_forbidden(budget.get("forbidden")),
        _validate_tests_in_scope(budget),
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
        parsed = json.loads(block_text)
    except json.JSONDecodeError as exc:
        return False, f"context_budget block is not valid JSON: {exc.msg}"

    return _validate_budget(parsed)


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
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"FORGE context-budget hook: {reason}",
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
