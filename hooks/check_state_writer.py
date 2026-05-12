#!/usr/bin/env python3
"""PreToolUse hook refusing direct writes to feature state.json.

Reads the hook input on stdin (JSON). When the dispatched tool is one of
``Write`` / ``Edit`` / ``MultiEdit`` and its ``file_path`` lands inside
``.forge/features/<feature_id>/state.json`` (at any depth), the hook returns
a PreToolUse deny decision. Otherwise it returns an empty object (allow).

state.json is mechanically owned by the ``tools.state.*`` and
``tools.routing.*`` helpers: ``tools.routing.seed_routed_feature`` for the
initial seed (creating a new state.json from nothing) and
``complete_phase`` / ``start_phase`` / ``record_routing_decision`` /
``record_refined_idea`` for in-place transitions. Direct edits bypass
schema validation and produce broken seeds; this hook closes that bypass
at the tool boundary.

Threat-model summary (what this hook does and does NOT cover):

* **Protected**: ``Write`` / ``Edit`` / ``MultiEdit`` targeting any path
  whose component sequence ends with ``.forge/features/<id>/state.json``.
* **NOT protected**: ``SPEC.md`` / ``PLAN.md`` / ``UNDERSTANDING.md`` /
  ``decisions.md`` and other per-feature artifacts. Their shape is owned
  by the ``python -m tools.validate`` family, which fails downstream
  rather than at the tool boundary — operators routinely hand-edit these
  files, so hook-level refusal would be hostile.
* **NOT protected**: ``.forge/CONSTITUTION.md`` /
  ``.forge/conventions.json`` / ``.forge/intel/lessons.md``. The
  authoring helpers (``tools.constitution_amend``, ``tools.intel.lessons``)
  apply atomic-pair writes and advisory locks; direct edits land but the
  validator surfaces shape errors at next read.
* **Documented design — NOT a bug**: symlink traversal is not resolved.
  ``ln -s state.json alias.json`` and then writing ``alias.json``
  bypasses the literal-path matcher. The threat requires an attacker
  who can already create files in the repo, in which case state.json
  is the least of the target's concerns; resolving symlinks would
  introduce a TOCTOU race and several pathlib portability hazards for
  marginal benefit.

Stdlib only. Idempotent and side-effect-free.

Hook input shape (per Claude Code docs):
{
  "session_id": "...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Write" | "Edit" | "MultiEdit",
  "tool_input": { "file_path": "...", ... },
  ...
}
"""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

# Tool names that mutate file contents directly. The matcher in hooks.json
# is "Write|Edit|MultiEdit"; this defensive set guards against drift if the
# matcher widens without a corresponding update here.
_WRITE_TOOL_NAMES = frozenset({"Write", "Edit", "MultiEdit"})

_DENY_REASON = (
    "state.json is mechanically owned by FORGE. Direct "
    "Write/Edit/MultiEdit is refused.\n"
    "\n"
    "To seed a NEW feature, run this Bash command (do NOT translate it "
    "into a Python heredoc):\n"
    '  forge-do --idea "<idea>" --tier <focused|standard|full> '
    '--rationale "<one_sentence>"\n'
    "\n"
    "When forge-do is not on PATH (typical in a fresh target repo), "
    "use the module form:\n"
    "  PYTHONPATH=<plugin_install> python3 -m tools.do_cli "
    '--idea "<idea>" --tier <focused|standard|full> '
    '--rationale "<one_sentence>"\n'
    "\n"
    "Resolve <plugin_install> from $CLAUDE_PLUGIN_ROOT or "
    "`claude plugin list` (typically "
    "~/.claude/plugins/cache/forge-marketplace/forge/<version>).\n"
    "\n"
    "For POST-SEED mutations, call the tools.state.* helpers via Bash "
    "(complete_phase / start_phase / record_routing_decision / "
    "record_refined_idea / record_commit / append_deviation / "
    "set_execute_current_slice)."
)


def _path_parts(file_path: str) -> tuple[str, ...]:
    """Return the path components of `file_path`, tolerant of separator style.

    Uses ``PurePosixPath`` first (the canonical form in this repo and in
    Claude Code's tool inputs). Falls back to ``PureWindowsPath`` only when
    the posix split yields a single component but the raw string contains
    a backslash separator. Symlinks are NOT resolved — the literal path the
    tool was about to write is what the hook judges.
    """
    posix_parts = PurePosixPath(file_path).parts
    if "\\" in file_path and len(posix_parts) <= 1:
        return PureWindowsPath(file_path).parts
    return posix_parts


def is_blocked_path(file_path: str) -> bool:
    """Return True when `file_path` targets a feature state.json.

    The pattern is the subsequence ``[".forge", "features", "<id>",
    "state.json"]`` anywhere in the resolved components, where ``<id>`` is
    any single non-empty segment. The hook intentionally matches at any
    depth so paths produced under tmp dirs, absolute repo roots, or nested
    worktrees all resolve to the same rule.

    Args:
        file_path: The literal path the tool was about to write.

    Returns:
        True when the path is a feature state.json under
        ``.forge/features/<id>/``; False otherwise (including empty input,
        templates, fixtures, and any sibling artifact under the feature
        folder such as ``SPEC.md`` or ``decisions.md``).
    """
    if not file_path:
        return False
    parts = _path_parts(file_path)
    # Need at least: ".forge", "features", "<id>", "state.json" — four parts.
    for i in range(len(parts) - 3):
        if (
            parts[i] == ".forge"
            and parts[i + 1] == "features"
            and parts[i + 2] not in {"", ".", ".."}
            and parts[i + 3] == "state.json"
            and i + 3 == len(parts) - 1  # state.json must be the final component
        ):
            return True
    return False


def evaluate(tool_name: str, tool_input: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (allow, deny_reason_or_None) for a single PreToolUse event.

    Args:
        tool_name: The tool Claude Code is about to invoke (e.g. ``Write``).
        tool_input: The tool's input payload. The hook only inspects
            ``file_path``; ``MultiEdit`` carries the same key alongside its
            ``edits`` list.

    Returns:
        ``(True, None)`` when the call is allowed (unrelated tool, or a
        write to a path other than a feature state.json). ``(False, reason)``
        when the call must be refused, where ``reason`` is the human-readable
        explanation surfaced to the user.
    """
    if tool_name not in _WRITE_TOOL_NAMES:
        return True, None

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return True, None

    if is_blocked_path(file_path):
        return False, _DENY_REASON
    return True, None


def main() -> int:
    """Read stdin JSON, evaluate, emit decision JSON. Exit 0 on success."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return 0

    if not isinstance(payload, dict):
        print(json.dumps({}))
        return 0

    tool_name = payload.get("tool_name")
    tool_input_raw = payload.get("tool_input", {})
    tool_input: dict[str, Any] = tool_input_raw if isinstance(tool_input_raw, dict) else {}

    if not isinstance(tool_name, str):
        print(json.dumps({}))
        return 0

    allow, reason = evaluate(tool_name, tool_input)

    if allow or reason is None:
        print(json.dumps({}))
    else:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"FORGE state-writer hook: {reason}",
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
