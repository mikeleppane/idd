#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool.

Reads the hook input on stdin (JSON). The dispatch prompt MUST contain a
top-level ``context_budget:`` marker (column 0, outside any fenced code
block) followed by a JSON object literal. The object must declare a
non-empty ``files_in_scope`` list of bounded globs, and a non-empty
``forbidden`` list. Otherwise the hook returns a PreToolUse deny decision;
otherwise it returns an empty object (allow).

Optional budget fields recognized elsewhere in the FORGE stack:

* ``tests_in_scope`` (required for ``phase == "execute"`` unless
  ``tdd_exception_ref`` is set; shape-checked here).
* ``articles`` (optional): list of filtered Constitution articles
  serialized via ``tools.constitution.Article.to_budget_dict``. Hook is
  permissive on this field — shape is not validated here; the producing
  skill (``forge-spec`` / ``forge-plan`` / ``forge-execute`` /
  ``forge-review``) owns shape.
* ``traps`` (optional): list of filtered cross-feature trap lessons,
  serialized via ``tools.intel.lessons.Lesson.to_budget_dict``. Hook is
  permissive on this field — shape is not validated here; the producing
  skill (``forge-spec`` / ``forge-plan`` / ``forge-execute``) owns
  shape.

Stdlib-only. The hook may run as ``python3 ${CLAUDE_PLUGIN_ROOT}/hooks/check_budget.py``
from a target repo where third-party deps (``jsonschema``, ``yaml``) are
not on ``sys.path``. To stay independent of the dev install we:

* Bootstrap ``sys.path`` to the hook's sibling ``tools/`` directory.
* Import only :mod:`tools.conventions_runtime` and :mod:`tools._glob`, both
  of which are stdlib-only.

The strict schema-validated path (jsonschema-backed) lives in
``tools.validate.conventions`` and is reserved for the dev-environment
review pass (``python -m tools.validate --target conventions``).

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
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Bootstrap sys.path so the hook can locate its sibling ``tools/`` directory
# even when invoked as ``python3 /abs/path/to/hooks/check_budget.py`` from a
# foreign cwd. Insert at index 0 so any system-wide collision (unlikely;
# we shadow ``tools`` deliberately) loses to the local module.
_HOOK_ROOT = Path(__file__).resolve().parent.parent
if str(_HOOK_ROOT) not in sys.path:
    sys.path.insert(0, str(_HOOK_ROOT))

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.conventions_runtime import Convention, Scope

    _LoadConventionsT = Callable[[Path], list[Convention]] | None
    _MatchConventionT = Callable[..., bool] | None
    _ = Scope  # imported for downstream type narrowing; mypy needs the binding

_load_conventions: _LoadConventionsT
_match_convention: _MatchConventionT
try:
    from tools.conventions_runtime import (
        load_conventions_permissive as _load_conventions,
    )
    from tools.conventions_runtime import (
        match_convention as _match_convention,
    )
except ImportError:
    # The hook's invariant is "stdlib-only consumer + sibling tools/ on path"
    # — if even that fails, something is structurally wrong with the install.
    # Surface the failure as a deny so the gate fails closed rather than
    # silently waving every dispatch through.
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

# Severities whose forbidden_text fire suppresses the matched-substring
# excerpt in the deny reason. The hook output is forwarded back through the
# Claude transcript; for high-severity rules that may have been authored to
# block secret leakage, including the matched substring would echo the
# trip text into the same transcript the gate is trying to protect. The
# validator path (``validate_conventions``) still includes the excerpt at
# review time when terminal-visible context is the goal.
_EXCERPT_SUPPRESS_SEVERITIES = frozenset({"BLOCK", "HIGH"})

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


def _strip_budget_block(prompt: str) -> str:
    """Return ``prompt`` with the first ``context_budget:`` block removed.

    The marker line and the balanced JSON object that follows it are excised
    from the prompt body before convention scanning. Rules scoped to
    ``dispatch_brief`` target the human-author-controlled sections of the
    dispatch; the machine-injected ``articles[]`` and ``traps[]`` arrays
    inside the budget block should not match those rules even when their
    bodies coincidentally contain forbidden patterns.

    Returns the original prompt unchanged when no budget block is locatable
    (no marker line, no following ``{``, or unbalanced braces).
    """
    lines = prompt.splitlines(keepends=True)
    marker_line_idx = _find_marker_line(lines)
    if marker_line_idx < 0:
        return prompt

    # Absolute offset of the marker line within ``prompt``.
    marker_offset = sum(len(line) for line in lines[:marker_line_idx])

    rest = "".join(lines[marker_line_idx:])[len(_MARKER) :]
    open_idx = rest.find("{")
    if open_idx < 0:
        return prompt
    if rest[:open_idx].strip() != "":
        return prompt

    body = _balanced_json_object(rest[open_idx:])
    if body is None:
        return prompt

    # End of the JSON object within ``prompt``: marker offset + marker text
    # length + characters skipped up to the opening brace + JSON body length.
    json_end = marker_offset + len(_MARKER) + open_idx + len(body)
    return prompt[:marker_offset] + prompt[json_end:]


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


def _git_toplevel(start: Path) -> Path | None:
    """Return ``git rev-parse --show-toplevel`` for ``start``, or ``None`` on miss."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    candidate = Path(result.stdout.strip())
    if not candidate.is_dir():
        return None
    return candidate


def _env_repo_root_override() -> Path | None:
    """Return ``$FORGE_REPO_ROOT`` when it points to a FORGE repo, else ``None``.

    Emits a one-line stderr warning when the env var is set but does not point
    at a directory containing ``.forge/``. Returns ``None`` in that case so the
    walk-up fallback can run. Returning ``None`` for unset is silent — only
    misconfigurations are noisy.
    """
    raw = os.environ.get("FORGE_REPO_ROOT")
    if raw is None or raw.strip() == "":
        return None
    candidate = Path(raw)
    try:
        resolved = candidate.resolve()
    except OSError:
        print(
            f"[forge-check-budget] FORGE_REPO_ROOT={raw!r} could not be resolved; "
            "falling back to walk-up",
            file=sys.stderr,
        )
        return None
    if not (resolved / ".forge").is_dir():
        print(
            f"[forge-check-budget] FORGE_REPO_ROOT={raw!r} does not contain a "
            ".forge/ directory; falling back to walk-up",
            file=sys.stderr,
        )
        return None
    return resolved


def _locate_repo_root(*, start: Path | None = None) -> Path | None:
    """Locate the FORGE repo root for ``start``.

    Resolution order:

    1. ``$FORGE_REPO_ROOT`` env override — when set and pointing at a
       directory containing ``.forge/``, use it. Invalid override (missing
       directory or absent ``.forge/``) emits a one-line stderr warning and
       falls through to the walk-up.
    2. Ask git: ``git -C <start> rev-parse --show-toplevel``. Accept the
       result only when it also contains a ``.forge`` directory — this
       distinguishes a FORGE repo from a generic git checkout that happens
       to sit anywhere on disk.
    3. Walk up to :data:`_REPO_ROOT_WALK_LIMIT` ancestors looking for the
       first one with a ``.forge`` directory. The walk is bounded so the
       hook cannot stall on malformed environments.

    Returns ``None`` when no path locates a ``.forge`` directory.
    """
    override = _env_repo_root_override()
    if override is not None:
        return override

    current = start if start is not None else Path.cwd()
    try:
        current = current.resolve()
    except OSError:
        return None

    git_root = _git_toplevel(current)
    if git_root is not None and (git_root / ".forge").is_dir():
        return git_root

    walker = current
    for _ in range(_REPO_ROOT_WALK_LIMIT):
        if (walker / ".forge").is_dir():
            return walker
        parent = walker.parent
        if parent == walker:
            return None
        walker = parent
    return None


def _load_conventions_or_error(repo_root: Path) -> tuple[list[Convention] | None, str | None]:
    """Load conventions for the dispatch-brief check.

    Returns ``(rules, error)``:

    * ``(rules, None)`` — file loaded cleanly; ``rules`` may be empty.
    * ``([], None)`` — file absent. The hook allows in this case.
    * ``(None, reason)`` — file present and structurally broken. The hook
      DENIES with ``reason`` so a malformed conventions.json cannot silently
      disable the gate (fail-closed; previous behavior allowed silently).
    """
    if _load_conventions is None:
        # The module import path failed despite the sys.path bootstrap —
        # this is a structural install error, not a user input issue.
        return None, (
            "tools.conventions_runtime could not be imported; verify the FORGE plugin install"
        )
    path = repo_root / ".forge" / "conventions.json"
    if not path.is_file():
        return [], None
    try:
        rules = _load_conventions(repo_root)
    except (ValueError, OSError) as exc:
        return None, f"conventions.json present but invalid: {exc}"
    return rules, None


def _first_fired_deny_reason(rules: list[Convention], prompt: str) -> str | None:
    """Return the deny reason for the first fired blocking rule, else ``None``.

    Rules are scanned in lexicographic order on ``id``. ``filename_glob_forbidden``
    is skipped silently — it is a schema violation in ``dispatch_brief`` scope
    that the strict validator path rejects at load time.
    """
    if _match_convention is None:
        return None
    for rule in sorted(rules, key=lambda r: r.id):
        if "dispatch_brief" not in rule.scope:
            continue
        if rule.pattern_kind == "filename_glob_forbidden":
            continue
        if rule.severity not in _CONVENTION_DENY_SEVERITIES:
            continue
        try:
            compiled = re.compile(rule.pattern)
        except re.error:
            # Fail-closed for high-severity rules with broken patterns: the
            # author intended a gate, the gate cannot evaluate, the safe
            # answer is to deny and surface the compile error.
            return (
                f"{rule.id}: pattern failed to compile; run "
                "`python -m tools.validate --target conventions` to inspect"
            )
        try:
            fired = _match_convention(rule, text=prompt, scope="dispatch_brief")
        except (ValueError, re.error):
            continue
        if not fired:
            continue
        if rule.pattern_kind == "forbidden_text":
            if rule.severity in _EXCERPT_SUPPRESS_SEVERITIES:
                # High-severity forbidden_text rules may have been authored to
                # block secret leakage; suppress the matched substring so the
                # deny reason does not echo the trip text into the transcript.
                return f"{rule.id}: forbidden_text matched (excerpt suppressed)"
            excerpt = _forbidden_text_excerpt(rule.pattern, prompt)
            del compiled  # explicit unused-binding cue
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

    Behavior on structurally-broken ``.forge/conventions.json``: the hook
    fail-CLOSES (deny). The previous version fail-opened, so a malformed
    file silently disabled the gate. Now the operator sees a clear deny
    pointing at ``python -m tools.validate --target conventions``.

    Args:
        prompt: The dispatch prompt body.
        repo_root: Optional override for testing. Production callers pass
            ``None`` and the helper locates the repo via
            :func:`_locate_repo_root`.

    Returns:
        ``(True, "ok")`` when no blocking rule fires. ``(False, reason)``
        with the first fired rule's id (or the structural-error reason)
        when one does.
    """
    root = repo_root if repo_root is not None else _locate_repo_root()
    if root is None:
        # No FORGE repo found within the walk limit — nothing to enforce.
        # This is distinct from "repo present but conventions.json broken":
        # there the file's mere presence signals intent to gate.
        return True, "ok"
    rules, load_error = _load_conventions_or_error(root)
    if load_error is not None:
        return False, f"{load_error} (repo: {root})"
    if rules is None or not rules:
        return True, "ok"
    # Carve the machine-injected ``context_budget:`` JSON out of the prompt
    # before scanning. ``articles[]`` and ``traps[]`` arrive verbatim from
    # ``tools.constitution`` / ``tools.intel.lessons``; ``dispatch_brief``
    # rules target the human-author-controlled sections of the brief, not
    # those repo-owned payloads. Without the carve-out, a CRITICAL lesson
    # whose ``trap`` text quotes a forbidden phrase (e.g. the canonical
    # ``Co-Authored-By: Claude`` ban) would itself trip the gate that was
    # authored to keep that phrase out of human-authored briefs.
    scan_text = _strip_budget_block(prompt)
    reason = _first_fired_deny_reason(rules, scan_text)
    if reason is None:
        return True, "ok"
    return False, f"{reason} (repo: {root})"


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
