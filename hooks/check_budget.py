#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool.

Reads the hook input on stdin (JSON). The dispatch prompt MUST contain a
top-level ``context_budget:`` marker (column 0, outside any fenced code
block) followed by a JSON object literal. The object must declare a
non-empty ``files_in_scope`` list of bounded globs, and a non-empty
``forbidden`` list. Otherwise the hook returns a PreToolUse deny decision;
otherwise it returns an empty object (allow).

Idempotent and side-effect-free. Imports ``tools.validate.conventions`` for the
dispatch-brief convention check; otherwise stdlib-only. The relaxation is
intentional — re-implementing schema parsing + regex matching in the hook
would duplicate code without saving meaningful startup cost.

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.validate.conventions import Convention

_load_conventions: Any | None
_match_convention: Any | None
try:
    from tools.validate.conventions import (
        load_conventions as _load_conventions,
    )
    from tools.validate.conventions import (
        match_convention as _match_convention,
    )
except ImportError:  # pragma: no cover - hook may run with stdlib-only env
    _load_conventions = None
    _match_convention = None

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

# Severities that turn a fired dispatch_brief rule into a deny. MEDIUM/LOW/WARN
# fires are surfaced by the validator path; the hook is a strict gate.
_CONVENTION_DENY_SEVERITIES = frozenset({"BLOCK", "HIGH"})

# Cap on how far up the directory tree we walk looking for a ``.forge``
# directory. The hook runs from arbitrary cwds; 8 levels covers any sane
# repo layout without unbounded I/O on malformed environments.
_REPO_ROOT_WALK_LIMIT = 8


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


_FORBIDDEN_EXCERPT_MAX = 80


def _forbidden_text_excerpt(pattern: str, text: str) -> str:
    """Return the matched substring (truncated) for a forbidden_text fire."""
    try:
        match = re.search(pattern, text)
    except re.error:
        return pattern
    if match is None:
        return pattern
    excerpt = match.group(0)
    if len(excerpt) > _FORBIDDEN_EXCERPT_MAX:
        excerpt = excerpt[:_FORBIDDEN_EXCERPT_MAX]
    return excerpt


def _locate_repo_root(*, start: Path | None = None) -> Path | None:
    """Walk up from ``start`` looking for the first ancestor with ``.forge/``.

    Args:
        start: Directory to begin the walk from. Defaults to ``Path.cwd()``.

    Returns:
        The first ancestor containing a ``.forge`` directory, or ``None`` when
        no such ancestor is found within :data:`_REPO_ROOT_WALK_LIMIT` levels.
    """
    current = start if start is not None else Path.cwd()
    try:
        current = current.resolve()
    except OSError:
        return None
    for _ in range(_REPO_ROOT_WALK_LIMIT):
        if (current / ".forge").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _load_conventions_safely(repo_root: Path) -> list[Convention] | None:
    """Load conventions for the dispatch-brief check; return None on any failure.

    The hook never denies on a load-error path — schema or JSON shape errors
    are owned by ``python -m tools.validate --target conventions``. Returning
    ``None`` signals the caller to allow.
    """
    if _load_conventions is None:
        return None
    try:
        rules: list[Convention] = _load_conventions(repo_root)
    except (ValueError, OSError):
        return None
    return rules


def _first_fired_deny_reason(rules: list[Convention], prompt: str) -> str | None:
    """Return the deny reason for the first fired blocking rule, else ``None``.

    Rules are scanned in lexicographic order on ``id``. ``filename_glob_forbidden``
    is skipped silently — it is a schema violation in ``dispatch_brief`` scope
    that ``load_conventions`` already rejected at load time.
    """
    if _match_convention is None:
        return None
    for rule in sorted(rules, key=lambda r: r.id):
        if "dispatch_brief" not in rule.scope:
            continue
        if rule.pattern_kind == "filename_glob_forbidden":
            continue
        try:
            fired = _match_convention(rule, text=prompt, scope="dispatch_brief")
        except (ValueError, re.error):
            continue
        if not fired or rule.severity not in _CONVENTION_DENY_SEVERITIES:
            continue
        if rule.pattern_kind == "forbidden_text":
            excerpt = _forbidden_text_excerpt(rule.pattern, prompt)
            return f"{rule.id}: forbidden_text matched: {excerpt}"
        return f"{rule.id}: required_text not found in dispatch_brief"
    return None


def _check_dispatch_brief_conventions(
    prompt: str,
    *,
    repo_root: Path | None = None,
) -> tuple[bool, str]:
    """Evaluate ``scope: dispatch_brief`` rules against ``prompt``.

    The hook is a strict gate: only ``BLOCK`` / ``HIGH`` severity fires deny.
    Lower-severity rules are surfaced by the validator path, not here.

    Args:
        prompt: The dispatch prompt body.
        repo_root: Optional override for testing. Production callers pass
            ``None`` and the helper locates the repo via
            :func:`_locate_repo_root`.

    Returns:
        ``(True, "ok")`` when no blocking rule fires. ``(False, reason)`` with
        the first fired rule's id when one does.
    """
    root = repo_root if repo_root is not None else _locate_repo_root()
    if root is None or not (root / ".forge" / "conventions.json").is_file():
        return True, "ok"
    rules = _load_conventions_safely(root)
    if rules is None:
        return True, "ok"
    reason = _first_fired_deny_reason(rules, prompt)
    if reason is None:
        return True, "ok"
    return False, reason


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

    allow, reason = _validate_budget(parsed)
    if not allow:
        return allow, reason

    return _check_dispatch_brief_conventions(prompt)


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
